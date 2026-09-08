import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from codex_window import core
from codex_window import cli


def observation(t=1000, used=0, reset=None, account="a"):
    return {"account": account, "observed_at": t,
            "five_hour": {"used_percent": used, "resets_at": reset if reset is not None else t+18000},
            "weekly": {"used_percent": 20, "resets_at": 900000}}


class Windows(unittest.TestCase):
    def test_moving_reset_requires_two_observations(self):
        self.assertEqual(core.decide(observation(1015), observation(), {}, 1015), "start")
        self.assertEqual(core.decide(observation(), None, {}, 1000), "observe")

    def test_rounded_zero_with_fixed_reset_is_active(self):
        self.assertEqual(core.decide(observation(1015, reset=19000), observation(), {}, 1015), "active")

    def test_used_window_never_fires(self):
        self.assertEqual(core.decide(observation(1015, used=1), observation(), {}, 1015), "active")

    def test_weekly_exhausted_never_fires_even_after_timestamp(self):
        current = observation(1015)
        current["weekly"] = {"used_percent": 100, "resets_at": 1}
        self.assertEqual(core.decide(current, observation(), {}, 1015), "weekly-exhausted")

    def test_attempt_cooldown_survives_moving_reset(self):
        self.assertEqual(core.decide(observation(1015), observation(), {"last_attempt": 1000}, 1015), "cooldown")

    def test_clock_backwards_keeps_cooldown(self):
        self.assertEqual(core.decide(observation(), observation(), {"last_attempt": 2000}, 1000), "cooldown")

    def test_account_switch_requires_new_confirmation(self):
        self.assertEqual(core.decide(observation(1015, account="b"), observation(), {}, 1015), "observe")

    def test_stale_or_too_close_samples_are_not_confirmation(self):
        for t in (1001, 1300, 999):
            self.assertEqual(core.decide(observation(t), observation(), {}, t), "observe")

    def test_wrong_window_duration_is_not_5h(self):
        with self.assertRaises(core.WindowError):
            core.normalize_limits({"rateLimits": {"primary": {"windowDurationMins": 60}}})

    def test_model_bucket_does_not_replace_general(self):
        with self.assertRaises(core.WindowError):
            core.normalize_limits({"rateLimitsByLimitId": {"spark": {}}})

    def test_nan_and_null_rejected(self):
        for used in (None, float("nan"), 101, -1, True):
            with self.assertRaises(core.WindowError):
                core.normalize_limits({"rateLimits": {"primary": {
                    "windowDurationMins": 300, "resetsAt": 19000, "usedPercent": used}}})

    def test_secondary_5h_recognized(self):
        result = core.normalize_limits({"rateLimits": {"secondary": {
            "windowDurationMins": 300, "resetsAt": 19000, "usedPercent": 5}}})
        self.assertEqual(result["five_hour"]["used_percent"], 5)

    def test_lowest_catalog_effort(self):
        class S:
            def call(self, *_):
                return {"data": [{"model": core.MODEL, "supportedReasoningEfforts": [
                    {"reasoningEffort": "high"}, {"reasoningEffort": "low"}]}]}
        self.assertEqual(core.select_effort(S()), "low")

    def test_no_expensive_fallback(self):
        class S:
            def call(self, *_):
                return {"data": [{"model": "gpt-6-astra"}]}
        with self.assertRaises(core.WindowError):
            core.select_effort(S())

    def test_atomic_state_and_lock(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            with core.locked_state(directory):
                with self.assertRaises(core.WindowError):
                    with core.locked_state(directory):
                        pass
                core.save_state(directory/"state.json", {"version": 1, "accounts": {}})
                self.assertEqual(cli.read_state(directory/"state.json")["version"], 1)

    def test_corrupt_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/"state.json"
            path.write_text("{broken")
            with self.assertRaises(core.WindowError):
                cli.read_state(path)

    def test_ping_needs_completed_turn(self):
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, '{"type":"thread.started"}\n', '')):
            with self.assertRaises(core.WindowError):
                core.cli_ping("codex", "low")

    def test_ping_reports_actual_usage_and_disables_context(self):
        events = '\n'.join(json.dumps(e) for e in [
            {"type":"item.completed", "item":{"type":"agent_message", "text":"1"}},
            {"type":"turn.completed", "usage":{"input_tokens": 44, "output_tokens": 8}}])
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, events, '')) as run:
            result = core.cli_ping("codex", "low")
            self.assertEqual(result["answer"], "1")
            self.assertEqual(result["usage"]["input_tokens"], 44)
            command = run.call_args.args[0]
            self.assertIn("--ignore-user-config", command)
            self.assertIn("project_doc_max_bytes=0", command)
            self.assertIn('service_tier="default"', command)

    def test_dry_run_never_dispatches(self):
        class S:
            def __enter__(self): return self
            def __exit__(self, *_): pass
        with tempfile.TemporaryDirectory() as d, patch.object(cli, "observe", return_value=observation(1015)), \
             patch.object(cli, "snapshot", return_value=observation(1016)), \
             patch.object(cli, "Server", return_value=S()), patch.object(cli,"select_effort",return_value="low"), \
             patch.object(cli, "ping") as ping, patch.object(cli.time,"time",return_value=1015), patch.object(cli,"emit"):
            cli.cycle("codex", Path(d), True, observation())
            ping.assert_not_called()
            self.assertFalse((Path(d)/"state.json").exists())

    def test_failure_persists_attempt_before_dispatch(self):
        class S:
            def __enter__(self): return self
            def __exit__(self, *_): pass
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/"state.json"
            def failing_ping(*_, **kwargs):
                self.assertEqual(cli.read_state(path)["accounts"]["a"]["outcome"], "pending")
                raise core.WindowError("uncertain")
            with patch.object(cli,"observe",return_value=observation(1015)), \
                 patch.object(cli,"snapshot",return_value=observation(1016)), \
                 patch.object(cli,"Server",return_value=S()), patch.object(cli,"select_effort",return_value="low"), \
                 patch.object(cli,"ping",side_effect=failing_ping), patch.object(cli.time,"time",return_value=1015):
                with self.assertRaises(core.WindowError):
                    cli.cycle("codex",Path(d),False,observation())
            self.assertEqual(cli.read_state(path)["accounts"]["a"]["outcome"], "unconfirmed")


if __name__ == "__main__":
    unittest.main()
