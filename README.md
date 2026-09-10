<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/hero.dark.svg">
    <img src="assets/hero.svg" alt="Emberloop" width="100%">
  </picture>
</p>

<h3 align="center">A tiny spark. An earlier reset.</h3>

<p align="center">
  Automatically start an unused Codex five-hour window with one tiny request.<br>
  Set it up once. Let the next cycle begin.
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> &nbsp;·&nbsp;
  <a href="#the-loop">How it works</a> &nbsp;·&nbsp;
  <a href="docs/usage.md">Reference</a> &nbsp;·&nbsp;
  <a href="README.zh-CN.md">简体中文</a>
</p>

<br>

## Same quota. An earlier start.

Your next five-hour window may stay unstarted until you send a request. Emberloop checks for that state and starts it for you, so the next reset can arrive sooner.

<picture>
  <source media="(prefers-color-scheme: dark) and (max-width: 600px)" srcset="assets/why-emberloop.en.mobile.dark.svg">
  <source media="(max-width: 600px)" srcset="assets/why-emberloop.en.mobile.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/why-emberloop.en.dark.svg">
  <img src="assets/why-emberloop.en.svg" alt="Example: first use at 12:00 leads to a reset around 17:00. Auto-start shortly after 10:00 moves it to around 15:00. Both windows last five hours." width="100%">
</picture>

*One example: the previous window ends at 10:00. Start it shortly after 10:00 instead of waiting until noon, and the next reset moves from around 17:00 to around 15:00.*

Emberloop only starts a window confirmed to be unused and unstarted. It does not add quota, reset an active window, or redeem reset credits. Detection follows observed server behavior, not a guaranteed OpenAI contract.

<br>

## Quick start

You need a recent [Codex CLI](https://github.com/openai/codex), a ChatGPT login, access to GPT-5.6 Luna, and an account with a five-hour quota window. Homebrew installs Python for you. CI covers macOS and Linux; Windows is not currently supported.

### 01 &nbsp; Install

```sh
brew install abinzzz/tap/emberloop
```

### 02 &nbsp; Check your window

```sh
codex login                 # Skip if already signed in
emberloop status
emberloop run --dry-run
```

`status` and `--dry-run` read metadata without generating model tokens.

### 03 &nbsp; Start the service

```sh
brew services start emberloop
```

Installing alone does not start the service. Your computer must be awake and online for a request to run.

<details>
<summary>Service controls &amp; foreground mode</summary>

```sh
brew services info emberloop
brew services stop emberloop

# Or keep it in your terminal:
emberloop watch
```

Run the service as your user, without `sudo`. For custom `CODEX_HOME` or a custom Codex executable, use foreground mode or your own service environment. See the [full reference](docs/usage.md).

</details>

<br>

## Small by design

**One short reply.** Luna is asked to return `1`, using its lowest supported reasoning effort. The default direct transport sends no tool definitions, conversation history, or project files.

**No extra runtime packages.** Python's standard library does the work. Emberloop uses your existing Codex ChatGPT login; no API key is required.

**One recorded attempt.** A process lock prevents overlapping runners sharing state. Attempts are saved before dispatch; a failed or uncertain request suppresses automatic retries for five hours.

### Measured in tokens

Local observations with Luna and `low`, September 8–9, 2026:

| Transport | Input | Output¹ | Total tokens |
|---|---:|---:|---:|
| Direct · sample 1 | 16 | 5 | **21** |
| Direct · sample 2 | 16 | 19 | **35** |
| Native CLI · sample | 9,651 | 5 | **9,656** |

¹ Output includes reasoning; the second direct sample used 12 reasoning tokens. Counts vary. These observations are not guaranteed minima or quota-percentage savings. A one-character reply can still use several output tokens.

> [!IMPORTANT]
> The default direct transport uses an **undocumented ChatGPT-backed endpoint** that may change. Choose `--transport cli` explicitly to use the native Codex harness with reduced context and higher input overhead. Emberloop never silently switches to a more expensive model or transport.

<br>

## The loop

1. **Read.** Fetch live quota metadata and follow the server's reset time.
2. **Confirm.** Check twice that the unused window is still unstarted.
3. **Start.** Save the attempt, send one tiny Luna turn, and wait for the complete response.
4. **Verify.** Check that the new reset time is stable, then wait for the next cycle.

An unused window whose reset time moves with the clock is a candidate. A fixed reset time means the window is already active, even when usage rounds to 0%. Missing live data is never treated as free capacity.

The default maximum polling interval is five minutes; a known earlier reset shortens the wait. After sleep or an offline period, Emberloop rechecks live state instead of replaying missed windows.

## Everyday commands

| Command | Purpose |
|---|---|
| `emberloop status` | Remaining quota and local reset dates |
| `emberloop status --json` | Machine-readable snapshot |
| `emberloop run --dry-run` | Check eligibility without a model turn |
| `emberloop run` | Check once; start only if eligible |
| `emberloop watch` | Monitor continuously |
| `emberloop watch --poll 60` | Set a 60-second maximum polling interval |
| `emberloop watch --transport cli` | Use the native Codex harness |

`run` and `watch` can consume quota. Both require fresh live metadata.

<br>

## A few practical details

Credentials come from your existing Codex login and are not stored in Emberloop logs. Direct requests go to the fixed ChatGPT-backed Codex endpoint; redirects are refused. Scheduling state uses a hashed account identity.

<details>
<summary>Logs &amp; local state</summary>

```sh
tail -f "$(brew --prefix)/var/log/emberloop.log"
```

Errors are written to `emberloop.error.log` in the same directory. Foreground mode emits JSON events.

Emberloop retains its original state location to preserve duplicate-request protection:

- macOS: `~/Library/Application Support/codex-window`
- Linux: `$XDG_STATE_HOME/codex-window` or `~/.local/state/codex-window`

Use one runner and one state directory per account.

</details>

<details>
<summary>Upgrading from codex-window</summary>

```sh
brew services stop codex-window
brew uninstall codex-window
brew install abinzzz/tap/emberloop
brew services start emberloop
```

**Keep the existing state directory** to preserve duplicate-request protection. The internal Python module and `CODEX_WINDOW_CODEX` environment variable remain compatible. The Python package retains `codex-window` as a command alias; the new Homebrew formula provides `emberloop`.

</details>

<details>
<summary>Development &amp; contributing</summary>

Bug reports and focused pull requests are welcome. Include your OS, Codex version, command, and redacted error; never attach authentication files or tokens.

```sh
git clone https://github.com/abinzzz/emberloop.git
cd emberloop
python3 -m unittest discover -s tests -v
python3 -m pip install .
emberloop --help
```

Tests simulate window transitions, stale snapshots, failed streams, account changes, and duplicate prevention. They do not use real credentials or send model requests.

</details>

<br>

---

Inspired by [onWatch](https://github.com/onllm-dev/onWatch) and [codex-shift](https://github.com/alexiiio/codex-shift). Built around the [Codex app-server protocol](https://learn.chatgpt.com/docs/app-server).

[Reference](docs/usage.md) &nbsp;·&nbsp; [Changelog](CHANGELOG.md) &nbsp;·&nbsp; [Releases](https://github.com/abinzzz/emberloop/releases) &nbsp;·&nbsp; [Tests](https://github.com/abinzzz/emberloop/actions/workflows/tests.yml) &nbsp;·&nbsp; [MIT license](LICENSE)

<sub>Independent community project. Not affiliated with OpenAI.</sub>
