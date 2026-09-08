"""Command-line interface. Only watch/run may send a model request."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import time

from . import __version__
from .core import (BUFFER, MODEL, Server, WindowError, decide, locked_state,
                   ping, save_state, select_effort, snapshot)


def default_state():
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/codex-window"
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home()/".local/state"))) / "codex-window"


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def read_state(path):
    if not path.exists():
        return {"version": 1, "accounts": {}}
    try:
        state = json.loads(path.read_text())
        if state["version"] != 1 or not isinstance(state["accounts"], dict):
            raise ValueError()
        return state
    except (ValueError, KeyError, TypeError):
        raise WindowError("Invalid state file; refusing to discard duplicate-request protection") from None


def observe(codex):
    with Server(codex) as server:
        return snapshot(server)


def cycle(codex, directory, dry_run=False, previous=None, transport="direct"):
    path = directory / "state.json"
    state = read_state(path)
    current = observe(codex)
    account_state = state["accounts"].setdefault(current["account"], {})
    decision = decide(current, previous, account_state, time.time())
    if decision == "observe":
        time.sleep(BUFFER)
        previous, current = current, observe(codex)
        # Account switching during a confirmation must not reuse the first account's state.
        account_state = state["accounts"].setdefault(current["account"], {})
        decision = decide(current, previous, account_state, time.time())
    output = {"event": "check", "decision": decision, "dry_run": dry_run,
              "five_hour": current["five_hour"], "at": time.time()}
    if decision == "start":
        with Server(codex) as server:
            effort = select_effort(server)
            fresh = snapshot(server)
        if fresh["account"] != current["account"] or fresh["five_hour"]["used_percent"] > 0:
            output["decision"] = "changed-before-start"
        elif fresh.get("weekly", {}).get("used_percent", 0) >= 100:
            output["decision"] = "weekly-exhausted"
        elif dry_run:
            output.update(model=MODEL, effort=effort)
        else:
            # Persist BEFORE dispatch. Crash/timeout must never cause an immediate duplicate.
            account_state["last_attempt"] = time.time()
            account_state["outcome"] = "pending"
            save_state(path, state)
            try:
                result = ping(codex, effort, transport=transport, expected_account=current["account"])
                account_state["outcome"] = "completed"
                account_state["result"] = result
                save_state(path, state)
                output.update(event="starter-completed", **result)
                time.sleep(BUFFER)
                after = observe(codex)
                time.sleep(BUFFER)
                verified = observe(codex)
                anchored = (after["account"] == current["account"] == verified["account"]
                            and abs(after["five_hour"]["resets_at"] - verified["five_hour"]["resets_at"]) <= 2
                            and verified["five_hour"]["resets_at"] > time.time())
                account_state["window_verified"] = anchored
                output["window_verified"] = anchored
                current = verified
            except WindowError:
                if account_state["outcome"] == "pending":
                    account_state["outcome"] = "unconfirmed"
                raise
            finally:
                save_state(path, state)
    emit(output)
    return current


def main(argv=None):
    parser = argparse.ArgumentParser(description="Start unstarted Codex 5h windows with a tiny Luna turn.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in [("status", "Read live quota and reset dates; never generates tokens"),
                            ("run", "Check once; start only a confirmed unstarted window"),
                            ("watch", "Monitor and start each unstarted 5h window")]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--codex", default=os.environ.get("CODEX_WINDOW_CODEX", "codex"))
        if name == "status":
            p.add_argument("--json", action="store_true")
        else:
            p.add_argument("--transport", choices=["direct", "cli"], default="direct", help="direct minimizes context; cli uses the native harness")
            p.add_argument("--dry-run", action="store_true", help="Read-only: never sends a model request")
            p.add_argument("--state-dir", type=Path, default=default_state())
            if name == "watch":
                p.add_argument("--poll", type=int, default=300, help="Maximum seconds between reads (15–3600)")
    args = parser.parse_args(argv)
    codex = shutil.which(args.codex)
    if not codex:
        parser.error("Codex CLI not found; install Codex and run codex login first")
    if args.command == "watch" and not 15 <= args.poll <= 3600:
        parser.error("--poll must be between 15 and 3600")
    try:
        if args.command == "status":
            data = observe(codex)
            if args.json:
                emit(data)
            else:
                for key, label in [("five_hour", "5h"), ("weekly", "Weekly")]:
                    if key in data:
                        w = data[key]
                        reset = datetime.fromtimestamp(w["resets_at"]).astimezone().isoformat(timespec="seconds")
                        print(f"{label}: {100-w['used_percent']:g}% remaining | resets {reset}")
            return 0
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        with locked_state(args.state_dir):
            previous = None
            while True:
                try:
                    previous = cycle(codex, args.state_dir, args.dry_run, previous, args.transport)
                except (WindowError, OSError) as exc:
                    if args.command == "run":
                        raise
                    emit({"event": "error", "message": str(exc), "at": time.time()})
                    previous = None
                if args.command == "run":
                    return 0
                delay = args.poll
                if previous:
                    # Wake just after the actual reset, then confirm with fresh metadata.
                    delay = min(delay, max(BUFFER, previous["five_hour"]["resets_at"] - time.time() + BUFFER))
                time.sleep(delay)
    except KeyboardInterrupt:
        return 0
    except (WindowError, OSError) as exc:
        print(f"codex-window: {exc}", file=sys.stderr)
        return 1
