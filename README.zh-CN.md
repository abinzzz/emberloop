<p align="center"><img src="assets/hero.svg" alt="Emberloop — 每五小时，一粒火种。" width="100%"></p>

<p align="center">
<a href="https://github.com/abinzzz/emberloop/actions/workflows/tests.yml"><img src="https://github.com/abinzzz/emberloop/actions/workflows/tests.yml/badge.svg" alt="测试状态"></a>
<a href="https://github.com/abinzzz/emberloop/releases"><img src="https://img.shields.io/github/v/release/abinzzz/emberloop?color=f09552" alt="最新版本"></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-89929b" alt="MIT 许可证"></a>
</p>

<p align="center"><strong>每五小时，一粒火种。</strong><br>等待 Codex 额度窗口重置，确认尚未启动，再发出一次极小的 Luna 请求。</p>
<p align="center"><a href="README.md">English</a> · <a href="#快速开始">快速开始</a> · <a href="docs/usage.md">完整参考（英文）</a></p>

---

## 为什么是 Emberloop？

**Emberloop 用一次极小请求，自动启动尚未开始的 Codex 五小时窗口，省去手动“点火”。**

![没有 Emberloop：12 点首次使用，约 17 点重置；开启后：10 点后自动启动，约 15 点重置。](assets/why-emberloop.zh.svg)

- **🔥 极小请求**：使用 Luna 及其最低支持的推理档，只要求回复 `1`。
- **⏱ 跟随服务端时间**：读取真实重置时间，并用两次查询确认窗口状态。
- **🪶 轻量运行**：仅依赖 Python 标准库，沿用现有 Codex 的 ChatGPT 登录。
- **🔒 防止重复消耗**：进程锁、先落盘再发送、失败后五小时冷却。
- **🍺 Homebrew 安装**：支持前台运行和后台服务。

> [!NOTE]
> 它只启动已确认“尚未开始”的窗口，不会增加额度、重置活动窗口或兑换重置券。窗口识别基于已观察到的服务端行为，并非 OpenAI 保证不变的接口约定。

## 快速开始

### 1 · 安装

```sh
brew install abinzzz/tap/emberloop
```

需要较新的 [Codex CLI](https://github.com/openai/codex)、ChatGPT 登录、Luna 访问权限，以及带有 5h 限额的账户。Homebrew 会安装 Python。CI 覆盖 macOS 和 Linux；目前不支持 Windows。

### 2 · 看看当前窗口

```sh
codex login                 # 已登录可跳过
emberloop status
emberloop run --dry-run
```

状态查询和 dry-run 不会请求模型生成内容。

### 3 · 开启后台运行

```sh
brew services start emberloop
```

仅安装不会自动启动服务。电脑必须处于唤醒且联网状态，才能按时执行。

```sh
brew services info emberloop   # 查看服务
brew services stop emberloop   # 停止服务
emberloop watch               # 也可以在终端前台运行
```

## 一轮如何开始

```text
读取额度 → 等待重置 → 两次确认未启动 → 保存尝试记录
    ↑                                      ↓
    └──── 验证新的窗口时间 ← 一次极短 Luna 请求
```

重置时间跟随时钟移动、且接近五小时后的未使用窗口，才会成为候选。**如果重置时间固定，即使用量显示 0%，也不会当成未启动窗口。**

请求发出前先记录状态，等待完整响应结束，再验证新的重置时间是否固定。失败、超时或结果不确定时，五小时内不自动重试。

## 到底消耗多少？

2026 年 9 月 8–9 日，使用 Luna + `low` 的本地实测：

| 方式 | 输入 tokens | 输出 tokens（含推理） | 合计 |
|---|---:|---:|---:|
| **直接请求 · 样本 1** | **16** | **5** | **21** |
| **直接请求 · 样本 2** | **16** | **19** | **35** |
| 原生 CLI · 样本 | 9,651 | 5 | 9,656 |

第二个直接请求样本包含 12 个推理 tokens。实际用量会波动；这些是样本，不是理论最低值，也不能直接换算为 5h／每周额度下降比例。可见回复只有一个字符，也可能产生多个输出 tokens。

> [!IMPORTANT]
> 默认直接请求使用尚未公开的 ChatGPT 后端接口，后续可能需要适配。可显式选择 `--transport cli` 使用原生 Codex，但上下文开销更高。不会自动切换到更贵的模型或请求方式。

## 常用命令

| 命令 | 作用 |
|---|---|
| `emberloop status` | 查看剩余额度和本地时区的重置时间 |
| `emberloop status --json` | 获取 JSON 状态 |
| `emberloop run --dry-run` | 只检查，不发送模型请求 |
| `emberloop run` | 检查一次，仅符合条件时发送 |
| `emberloop watch` | 持续监控 |
| `emberloop watch --poll 60` | 将最大查询间隔设为 60 秒 |
| `emberloop watch --transport cli` | 使用原生 CLI 方式 |

默认最大查询间隔为五分钟；已知重置时间更近时，会提前唤醒检查。睡眠或断网后会重新获取状态，不会补发错过的历史窗口。

## 从 codex-window 升级

```sh
brew services stop codex-window
brew uninstall codex-window
brew install abinzzz/tap/emberloop
brew services start emberloop
```

**保留原有状态目录**，避免丢失防重复触发记录：

- macOS：`~/Library/Application Support/codex-window`
- Linux：`$XDG_STATE_HOME/codex-window` 或 `~/.local/state/codex-window`

内部 Python 模块名和 `CODEX_WINDOW_CODEX` 环境变量保留兼容。Python 包仍提供 `codex-window` 命令别名；新的 Homebrew 包使用 `emberloop` 命令。同一账户请只运行一个实例，并使用同一个状态目录。

日志可通过以下命令查看：

```sh
tail -f "$(brew --prefix)/var/log/emberloop.log"
```

错误日志为同目录下的 `emberloop.error.log`。认证文件不会写入日志；直接请求拒绝重定向。更多认证、状态及故障处理细节见[完整参考](docs/usage.md)。

## 开发与贡献

欢迎提交问题和聚焦明确的 PR。请附上操作系统、Codex 版本、执行命令及脱敏后的错误，不要上传认证文件或 token。

```sh
git clone https://github.com/abinzzz/emberloop.git
cd emberloop
python3 -m unittest discover -s tests -v
python3 -m pip install .
```

测试模拟窗口状态、失败响应及重复请求保护，不读取真实凭证，也不调用模型。

灵感来自 [onWatch](https://github.com/onllm-dev/onWatch) 和 [codex-shift](https://github.com/alexiiio/codex-shift)。

[MIT 许可证](LICENSE) · 独立社区项目 · 与 OpenAI 无隶属关系
