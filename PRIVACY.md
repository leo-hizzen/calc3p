# 隐私政策 / Privacy Policy

Calc3P v1.0.0 · 最后更新：2026-11-24

## 一句话

**默认不上报任何数据。** 只有你主动在 bot 目录里新建 `telemetry.toml` 并写明 `enabled = true` 与一个 `endpoint`，才会开始上报；随时删掉这个文件或改成 `enabled = false` 即立刻停止。

---

## 1. 默认行为

不创建 `telemetry.toml` 时，Calc3P：

- 不发起任何网络请求；
- 不写入任何遥测文件；
- 不读取 `~/.calc3p` 之外的任何路径；
- 与「纯离线、纯本地」的普通 Akagi bot 行为完全一致。

代码可自查：`bot.py` 中 `_telemetry_cfg()` 只有在该文件存在且 `enabled = true` 且 `endpoint` 非空时，才会让 `Telemetry.job` 为真；`_flush_queue()` 是唯一的上报入口。

---

## 2. 如何开启

在 `bot.py` 同目录新建 `telemetry.toml`：

```toml
[telemetry]
enabled = true
endpoint = "https://你的接收端/collect"
include_hands = false
```

- `endpoint`：接收端地址，由你指定。Calc3P 不会硬编码任何作者服务器；**作者本人不运营接收端**，你需要自己部署（仓库里的 `server/collector.py` 是一个最小实现）。
- `include_hands`：是否附带每手的 14 张手牌与所出牌。默认 `false`，只传统计量。

任何一条不满足（文件不存在、`enabled != true`、`endpoint` 为空、解析失败），都不会上报，且不会在 stderr 打出任何提示——失败即静默，不影响对局。

---

## 3. 开启后会发送什么

**固定字段**（每次 `end_game` 发送一条）：

| 字段 | 内容 | 示例 |
|---|---|---|
| `v` | 上报格式版本 | `1` |
| `install_id` | 首次运行时随机生成的 32 位十六进制串，存于 `~/.calc3p/install-id` | `4b0be5b9…` |
| `bot_version` | Calc3P 版本 | `1.0.0` |
| `python` | Python 版本 | `3.12.13` |
| `os` | 操作系统标识 | `win32` |
| `ts` | Unix 时间戳 | `1795485080` |
| `reason` | 结束原因 | `end_game` / `eof` |
| `game.decisions` | 本局决策次数 | `5` |
| `game.errors` | 本局异常次数 | `0` |
| `game.actions` | 各动作类型计数 | `{"none":4,"dahai":1}` |
| `game.p50_ms` / `game.p99_ms` | 决策耗时分位数 | `0.03` / `0.71` |

**可选字段**（仅 `include_hands = true` 时）：

| 字段 | 内容 | 上限 |
|---|---|---|
| `hands` | 每次出牌前的 14 张手牌（`["1m","2m",…]`）与动作 `{type, pai, tsumogiri, target}` | 200 条/局 |

---

## 4. 明确不采集

- 雀魂 / 天凤 / 其他平台的**账号、昵称、UID、对局 ID**；
- 对手的任何信息（名字、牌河以外的数据都不上传）；
- IP 地址（不主动记录；接收端若默认记录访问日志，请自行关闭）；
- MAC 地址、硬盘序列号、主机名、用户名等机器指纹；
- 浏览器 Cookie、剪贴板、任何文件系统内容；
- 你的牌谱文件本身（`hands` 字段只含当前这一手的手牌，不含 `history/games/` 内容）。

`install_id` 是随机数，不是账号，也无法与任何账号关联；删除 `~/.calc3p/install-id` 即可换成新的身份。

---

## 5. 数据存放在哪里

本地：

- 队列：`~/.calc3p/queue.jsonl`（Windows 为 `C:\Users\<你>\.calc3p\queue.jsonl`）
- 身份：`~/.calc3p/install-id`

可用环境变量 `CALC3P_HOME` 改到别的位置。队列最多保留 500 条，超出后丢弃最旧的；发送成功后清空。

远端：**由 `endpoint` 指向的那台服务器持有**。作者不运营任何接收端，因此不掌握你上报的数据。若你使用他人的接收端，请自行确认对方的隐私政策。

---

## 6. 关闭与删除

- 关闭：把 `enabled` 改为 `false`，或直接删除 `telemetry.toml`；
- 清空本地队列：删除 `~/.calc3p/queue.jsonl`；
- 重置身份：删除 `~/.calc3p/install-id`；
- 已发送的数据需联系接收端运营者删除。

关闭后不会再有新请求发出。

---

## 7. 用途与不做的用途

上报数据仅用于：

- 统计决策耗时分布，发现性能退化；
- 统计异常次数，定位崩溃；
- 统计动作分布，判断策略是否偏科（例如从不鸣牌）；
- `include_hands` 开启时，用于复现具体决策、检查规则错误。

**不用于**：训练神经网络、出售、广告画像、与第三方共享、识别个人。

---

## 8. 变更

若未来字段或行为变化，会在 `CHANGELOG.md` 与本文档中标注版本与日期；不兼容变更会提升 `v` 字段版本，旧客户端仍会被接收端识别。

---

## 9. 联系

问题请开 issue：<https://github.com/leo-hizzen/calc3p/issues>

---

# Privacy Policy (English summary)

Calc3P collects **nothing by default**. Telemetry starts only if you create `telemetry.toml` with `enabled = true` and a non-empty `endpoint` that you choose yourself. The author operates no collection server.

When enabled, each finished game sends one record: a random per-install id, bot/Python/OS versions, a timestamp, an end reason, and per-game counters (decisions, errors, action histogram, p50/p99 latency). With `include_hands = true` it also sends up to 200 per-turn hands plus the chosen discard.

It never collects account names, opponent identities, IP addresses, machine fingerprints, cookies, or your saved game records. Local queue lives in `~/.calc3p/queue.jsonl`; delete it or the `install-id` file at any time.
