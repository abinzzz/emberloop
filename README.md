# codex-window

Start the next Codex five-hour usage window with one tiny GPT-5.6 Luna turn, as soon as the previous window expires and a fresh window is confirmed unstarted.

A Python CLI with no third-party runtime packages. Uses your existing ChatGPT login from Codex. Metadata checks do not ask a model to generate anything. No API key is required.

## Install with Homebrew

```sh
brew tap abinzzz/tap
brew install codex-window
```

Install the official Codex CLI separately if needed (`brew install --cask codex` on macOS), then sign in:

```sh
codex login
codex-window status
codex-window run --dry-run
```

Requires Python 3.10+ and a recent Codex CLI with `account/rateLimits/read`, `model/list`, and the GPT-5.6 Luna catalog entry. Tested with Codex CLI 0.146.0 on macOS. Homebrew installs Python automatically. Your account must expose an explicit 300-minute quota window and have access to Luna.

## Run automatically

```sh
brew services start codex-window
brew services info codex-window
```

Stop with:

```sh
brew services stop codex-window
```

The service runs as your user; do not use sudo. For custom `CODEX_HOME` or a custom Codex binary, run `watch` under your own service environment instead. Homebrew service PATH includes the Homebrew bin directory and `/usr/local/bin`.

Other commands:

```sh
codex-window status --json      # Read-only quota/reset snapshot
codex-window run --dry-run      # Confirm eligibility, without sending a turn
codex-window run                # Check once, send only if eligible
codex-window watch              # Foreground monitor
codex-window watch --poll 60    # Check at most every 60 seconds
codex-window watch --transport cli  # Explicit native CLI alternative
```

`run` and `watch` are authorization to send the minimal request when eligible. Installing the package alone does not start monitoring. `status` and `--dry-run` never generate model tokens.

## How it works

1. Read account and quota metadata through the official local `codex app-server` protocol.
2. Follow the general `codex` bucket and the explicit 300-minute window. Refuse missing/unknown data rather than guessing from another model's quota.
3. Wait until shortly after the server reset time (15-second buffer). Polling is bounded to five minutes by default, and an earlier known reset shortens the next wait.
4. Confirm an unused, unstarted window with two fresh observations at least 15 seconds apart: reset time must move with wall-clock time and remain approximately five hours away. A rounded 0% window with a fixed reset time is already active and must not be pinged.
5. Query the account's model catalog. Require `gpt-5.6-luna` and choose its lowest supported reasoning effort (currently `low`). Never silently fall back to an expensive model.
6. Persist an attempt record before dispatch. Send one complete, streamed turn asking for `1`, then check that the next window's reset timestamp is stable.

The default transport sends a tiny request directly to the ChatGPT-backed Codex Responses endpoint with an empty tool list and no project/history context. It waits for `response.completed`; receiving the first token is not enough. It does not request Fast mode. This backend endpoint is **undocumented and may change**. `--transport cli` uses the native Codex CLI instead, with an empty working directory, user config/rules ignored, document context disabled, and an ephemeral session; it has substantially more fixed input overhead.

The moving-window detection follows observed community behavior, not a guaranteed OpenAI contract. If the backend changes, the tool conservatively skips windows it cannot confirm. It does not increase your quota, redeem reset credits, or force a reset of an active window.

## Measured cost

One local test on 2026-09-08, Luna with `low`:

| Transport | Input tokens | Output tokens | Reasoning tokens |
|---|---:|---:|---:|
| Direct (default) | 16 | 5 | 0 |
| Native CLI | 9,651 | 5 | 0 |

These are observed request counts, not guaranteed minima. A one-character visible answer can still be billed as several output tokens. Actual usage is recorded from the completed response. Credit prices cannot be converted directly into exact 5h/weekly percentage deductions.

## Reliability and state

- A process lock prevents overlapping runners using the same state directory.
- Attempts are written atomically **before** the request. A crash, timeout, or ambiguous response suppresses automatic retries for five hours, including after restart.
- Exhausted weekly allowance blocks a starter, even if its displayed reset timestamp has passed; live metadata must recover first.
- State is separated by a hashed account identity. Credentials and email addresses are not written to state or logs.
- Direct transport reads `CODEX_HOME/auth.json` (default `~/.codex/auth.json`) only into memory. Credentials are sent only to `https://chatgpt.com/backend-api/codex/responses`; redirects are refused. Codex handles metadata authentication. If direct auth expires, refresh your Codex login; POST requests are never retried automatically.
- Keychain-only authentication can use `--transport cli`.
- Sleep/offline periods cannot run requests. On wake/reconnection, monitoring resumes and rechecks live state; missed windows are not replayed.
- The default macOS state directory is `~/Library/Application Support/codex-window`; Linux uses `$XDG_STATE_HOME/codex-window` or `~/.local/state/codex-window`.
- Homebrew service logs are in `$(brew --prefix)/var/log/codex-window.log` and `codex-window.error.log`. Foreground commands emit JSON events.

Use one runner/state directory per account. Do not delete state while requests may be running: doing so removes duplicate-request protection. A request can complete but window verification can remain inconclusive; the five-hour cooldown still applies.

## Development

```sh
python3 -m unittest discover -s tests -v
python3 -m codex_window status
python3 -m pip install .
```

Tests simulate resets, stale snapshots, account changes, failed streams, restart cooldowns, and dry runs. They never use real credentials or send model requests.

## References

Independent implementation inspired by:

- [onWatch quota starter](https://github.com/onllm-dev/onWatch/blob/main/docs/CODEX_SETUP.md#auto-quota-starter-beta): detection of unstarted windows and full streamed completion.
- [codex-shift](https://github.com/alexiiio/codex-shift): small initialization turns and catalog-based reasoning selection.
- [Codex app-server documentation](https://learn.chatgpt.com/docs/app-server).

Not affiliated with OpenAI. MIT licensed.
