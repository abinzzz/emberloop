"""Stdlib-only Codex transport and conservative window scheduler."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time

MODEL = "gpt-5.6-luna"
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
WINDOW = 5 * 3600
BUFFER = 15


class WindowError(Exception):
    pass


class Server:
    """Short-lived stdio app-server; metadata requests never start model turns."""
    def __init__(self, codex="codex"):
        self.codex = codex
        self.seq = 0
        self.messages = queue.Queue()

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="codex-window-query-")
        self.proc = subprocess.Popen(
            [self.codex, "app-server", "--stdio"], cwd=self.tmp.name,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1,
        )
        def reader():
            try:
                for line in self.proc.stdout:
                    try:
                        self.messages.put(json.loads(line))
                    except ValueError:
                        continue
            finally:
                self.messages.put(None)
        threading.Thread(target=reader, daemon=True).start()
        try:
            self.call("initialize", {"clientInfo": {"name": "emberloop", "version": "0.2.0"}})
            self.send({"method": "initialized"})
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def send(self, message):
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def call(self, method, params=None, timeout=40):
        self.seq += 1
        request_id = self.seq
        self.send({"id": request_id, "method": method, "params": params or {}})
        deadline = time.monotonic() + timeout
        while True:
            try:
                message = self.messages.get(timeout=max(0, deadline-time.monotonic()))
            except queue.Empty:
                raise WindowError(f"Codex timed out during {method}") from None
            if message is None:
                raise WindowError(f"Codex app-server exited during {method}")
            if "method" in message and "id" in message:
                self.send({"id": message["id"], "error": {"code": -32601, "message": "Unsupported client request"}})
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    # Do not echo backend bodies, which can include account metadata.
                    raise WindowError(f"Codex rejected {method}; check codex login and CLI version")
                return message["result"]

    def __exit__(self, *_):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.proc.stdin.close()
        self.proc.stdout.close()
        self.tmp.cleanup()


def normalize_limits(data):
    buckets = data.get("rateLimitsByLimitId")
    if buckets:
        bucket = buckets.get("codex")
        if not bucket:
            raise WindowError("General Codex limit bucket unavailable; refusing a model-specific fallback")
    else:
        bucket = data.get("rateLimits") or {}
        if bucket.get("limitId") not in (None, "codex"):
            raise WindowError("General Codex limit bucket unavailable")
    windows = {}
    for key in ("primary", "secondary"):
        item = bucket.get(key)
        if not item:
            continue
        minutes, reset, used = (item.get(k) for k in ("windowDurationMins", "resetsAt", "usedPercent"))
        if minutes not in (300, 10080):
            continue
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
               for x in (reset, used)) or reset <= 0 or not 0 <= used <= 100:
            raise WindowError("Codex returned an invalid quota window")
        windows["five_hour" if minutes == 300 else "weekly"] = {"resets_at": reset, "used_percent": used}
    if "five_hour" not in windows:
        raise WindowError("No explicit 300-minute quota window is available for this account")
    return windows


def snapshot(server):
    account = server.call("account/read", {"refreshToken": False}).get("account") or {}
    if account.get("type") != "chatgpt":
        raise WindowError("Sign in with ChatGPT using codex login; API-key mode is not supported")
    # The hash scopes local scheduling state; no email is printed or saved.
    identity = account.get("id") or account.get("email")
    if not identity:
        raise WindowError("Codex did not provide an account identity")
    try:
        home = Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex")))
        identity = json.loads((home/"auth.json").read_text())["tokens"]["account_id"] or identity
    except (OSError, ValueError, KeyError, TypeError):
        pass
    key = hashlib.sha256(str(identity).encode()).hexdigest()[:24]
    return {"account": key, **normalize_limits(server.call("account/rateLimits/read")), "observed_at": time.time()}


def select_effort(server, model=MODEL):
    cursor = None
    while True:
        result = server.call("model/list", {"cursor": cursor, "limit": 100})
        for item in result.get("data", []):
            if item.get("model") == model:
                supported = {x["reasoningEffort"] for x in item.get("supportedReasoningEfforts", [])}
                for effort in EFFORTS:
                    if effort in supported:
                        return effort
                raise WindowError(f"No supported reasoning effort reported for {model}")
        cursor = result.get("nextCursor")
        if not cursor:
            raise WindowError(f"{model} unavailable; no automatic switch to a more expensive model")


def decide(current, previous, state, now):
    """Require two fresh observations of a moving, unused window before a turn."""
    five = current["five_hour"]
    weekly = current.get("weekly")
    if weekly and weekly["used_percent"] >= 100:
        return "weekly-exhausted"
    if now - state.get("last_attempt", -WINDOW) < WINDOW:
        return "cooldown"
    if five["used_percent"] > 0:
        return "active"
    if not previous or previous["account"] != current["account"]:
        return "observe"
    elapsed = current["observed_at"] - previous["observed_at"]
    movement = five["resets_at"] - previous["five_hour"]["resets_at"]
    if elapsed < 10 or elapsed > 120:
        return "observe"
    if previous["five_hour"]["used_percent"] != 0:
        return "observe"
    if (abs((five["resets_at"] - current["observed_at"]) - WINDOW) <= 90
            and abs(movement - elapsed) <= 5 and movement >= 5):
        return "start"
    return "active"


@contextlib.contextmanager
def locked_state(directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise WindowError("Another codex-window process owns this state directory") from None
        yield


def save_state(path, state):
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".state-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(state, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def cli_ping(codex, effort, model=MODEL):
    """Let Codex manage auth. Ignore user configuration and all document context."""
    with tempfile.TemporaryDirectory(prefix="codex-window-ping-") as directory:
        instruction = Path(directory) / "instructions.txt"
        instruction.write_text("Reply with exactly one character: 1. Do not use tools.")
        command = [codex, "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
                   "--skip-git-repo-check", "--sandbox", "read-only", "--json", "--color", "never",
                   "--model", model, "-C", directory,
                   "-c", 'model_reasoning_effort=' + json.dumps(effort),
                   "-c", 'model_instructions_file=' + json.dumps(str(instruction)),
                   "-c", 'project_doc_max_bytes=0', "-c", 'web_search="disabled"',
                   "-c", 'features.shell_tool=false', "-c", 'features.multi_agent=false',
                   "-c", 'model_reasoning_summary="none"', "-c", 'service_tier="default"', "1"]
        env = os.environ.copy()
        for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
            env.pop(key, None)
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                    text=True, timeout=120, env=env)
        except subprocess.TimeoutExpired:
            raise WindowError("Starter timed out; outcome uncertain, automatic retry suppressed for 5h") from None
        usage, answer = None, ""
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "turn.completed":
                usage = event.get("usage", {})
            if event.get("type") == "item.completed" and event.get("item", {}).get("type") == "agent_message":
                answer += event["item"].get("text", "")
        if result.returncode or usage is None:
            raise WindowError("Starter failed or completion unconfirmed; automatic retry suppressed for 5h")
        return {"model": model, "effort": effort, "usage": usage, "answer": answer[:32], "completed": True}


def ping(codex, effort, model=MODEL, transport="direct", expected_account=None):
    """Tiny complete SSE turn using ChatGPT auth; no API-key billing or tool schemas."""
    if transport == "cli":
        return cli_ping(codex, effort, model)
    import urllib.request
    import urllib.error
    home = Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex")))
    try:
        auth = json.loads((home/"auth.json").read_text())
        token = auth["tokens"]["access_token"]
        account_id = auth["tokens"]["account_id"]
        if not token or not account_id:
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        raise WindowError("Direct transport requires Codex file-based ChatGPT auth; use --transport cli for keychain auth") from None
    if expected_account and hashlib.sha256(str(account_id).encode()).hexdigest()[:24] != expected_account:
        raise WindowError("Codex account changed before dispatch; starter cancelled")
    body = {"model": model, "instructions": "Reply only 1.",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "1"}]}],
            "tools": [], "tool_choice": "none", "parallel_tool_calls": False,
            "reasoning": {"effort": effort}, "store": False, "stream": True}
    request = urllib.request.Request("https://chatgpt.com/backend-api/codex/responses",
        data=json.dumps(body).encode(), headers={"Authorization": "Bearer "+token,
        "ChatGPT-Account-Id": account_id, "Content-Type": "application/json",
        "Accept": "text/event-stream", "originator": "codex_cli_rs"})
    # Never forward bearer auth on redirects, and never auto-retry a POST.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None
    opener = urllib.request.build_opener(NoRedirect())
    deadline = time.monotonic()+120
    text_parts = []
    try:
        with opener.open(request, timeout=120) as response:
            for line in response:
                if time.monotonic() > deadline:
                    raise WindowError("Starter exceeded deadline; outcome uncertain; retry suppressed for 5h")
                if not line.startswith(b"data:"):
                    continue
                try:
                    event = json.loads(line[5:])
                except ValueError:
                    continue
                if event.get("type") == "response.output_text.delta":
                    text_parts.append(event.get("delta", ""))
                if event.get("type") in ("response.failed", "error", "response.incomplete"):
                    raise WindowError("Starter not completed; automatic retry suppressed for 5h")
                if event.get("type") == "response.completed":
                    result = event.get("response", {})
                    usage = result.get("usage")
                    if not isinstance(usage, dict):
                        raise WindowError("Completed response has no usage; retry suppressed for 5h")
                    answer = "".join(c.get("text", "") for item in result.get("output", [])
                                     if item.get("type") == "message" for c in item.get("content", [])
                                     if c.get("type") == "output_text")
                    counts = {k: usage[k] for k in ("input_tokens", "input_tokens_details", "output_tokens", "output_tokens_details", "total_tokens") if k in usage}
                    return {"model": model, "effort": effort, "transport": "direct",
                            "usage": counts, "answer": (answer or "".join(text_parts))[:32], "completed": True}
    except urllib.error.HTTPError as exc:
        raise WindowError(f"Starter HTTP {exc.code}; no retry. For 401, refresh with codex login; for 400, check CLI transport") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise WindowError("Starter connection failed; outcome uncertain; retry suppressed for 5h") from None
    raise WindowError("Stream ended without response.completed; retry suppressed for 5h")
