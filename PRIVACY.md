# 隐私政策 / Privacy Policy

Calc3P v1.2.0 · 最后更新：2026-11-24

## 一句话

**默认开启统计，但默认地址是本机 `127.0.0.1:8801`，并且每 7 天最多发送一次。** 也就是说：不填公网地址，数据只在你自己的电脑上排队、发给你自己的本机端口（没人监听就静默丢弃），**作者收不到任何东西**。

想真正上报给谁，就在 `telemetry.toml` 里把 `endpoint` 改成那台服务器的地址——这个动作必须由你来做。

---

## 1. 默认配置

不创建 `telemetry.toml` 时，Calc3P 的行为是：

| 项 | 默认值 | 含义 |
|---|---|---|
| `enabled` | `true` | 每局结束在本地追加一条统计记录 |
| `endpoint` | `http://127.0.0.1:8801/collect` | 只连本机回环地址，出不了这台机器 |
| `include_hands` | `false` | 不上传手牌，只传计数器 |
| 发送频率 | 每 7 天最多一次 | 由 `~/.calc3p/last-flush` 控制 |

因此默认状态下：

- 每局会写一行到 `~/.calc3p/queue.jsonl`（最多 500 行，超出丢最旧）；
- 只有距上次**成功发送**满 7 天时，才会尝试一次 POST；
- 若本机 8801 没有服务在听，这次 POST 直接失败、静默丢弃，队列保留，7 天后再试；
- 不访问任何外部域名。

## 2. 如何改成上报到自己的服务器

在 `bot.py` 同目录新建 `telemetry.toml`：

```toml
[telemetry]
enabled = true
endpoint = "https://你自己部署的接收端/collect"
include_hands = false
```

接收端可用仓库里的 `server/collector.py`，一行命令起服务，按 UTC 日期写 JSONL。

## 3. 如何完全关闭

```toml
[telemetry]
enabled = false
```

设置后：不写队列、不发请求、不留安装 ID。删掉 `telemetry.toml` 则回到默认（本机、每周一次）。

## 4. 发送内容

**固定字段**（每次发送时批量提交队列里的记录）：

| 字段 | 内容 | 示例 |
|---|---|---|
| `v` | 格式版本 | `1` |
| `install_id` | 首次运行随机生成的 32 位十六进制串，存于 `~/.calc3p/install-id` | `4b0be5b9…` |
| `bot_version` | 版本 | `1.2.0` |
| `python` | Python 版本 | `3.12.13` |
| `os` | 系统标识 | `win32` |
| `ts` | Unix 时间戳 | `1795485080` |
| `reason` | 结束原因 | `end_game` / `eof` |
| `game.decisions` | 本局决策次数 | `5` |
| `game.errors` | 本局异常次数 | `0` |
| `game.actions` | 动作分布 | `{"none":4,"dahai":1}` |
| `game.p50_ms` / `p99_ms` | 耗时分位数 | `0.03` / `0.71` |

**可选字段**（仅 `include_hands = true`）：`hands`，最多 200 条，每条是出牌前的 14 张手牌加动作 `{type, pai, tsumogiri, target}`。

## 5. 明确不采集

- 雀魂 / 天凤 / 其他平台的**账号、昵称、UID、对局 ID**；
- 对手的任何信息；
- IP 地址（不主动记录；`server/collector.py` 也不把 IP 写盘）；
- MAC 地址、硬盘序列号、主机名、用户名等机器指纹；
- Cookie、剪贴板、任何文件内容；
- 你的牌谱文件（`hands` 只含当前这一手的 14 张手牌）。

`install_id` 是随机数，不与任何账号关联；删除 `~/.calc3p/install-id` 即可换成新身份。

## 6. 数据存放在哪里

本地：

- 队列：`~/.calc3p/queue.jsonl`（Windows：`C:\Users\<你>\.calc3p\queue.jsonl`）
- 身份：`~/.calc3p/install-id`
- 上次发送时间：`~/.calc3p/last-flush`

可用环境变量 `CALC3P_HOME` 改到别处。

远端：只由**你自己填的** `endpoint` 持有。默认值是回环地址，作者不运营任何公共接收端，因此默认配置下作者零数据。

## 7. 关闭与删除

- 停止上报：`enabled = false`，或删除 `telemetry.toml`（会回到「本机、每周一次」）；
- 清空队列：删除 `~/.calc3p/queue.jsonl`；
- 重置身份：删除 `~/.calc3p/install-id`；
- 重置发送计时：删除 `~/.calc3p/last-flush`（会让下一次对局立即尝试发送）；
- 已发出去的数据需联系接收端运营者删除。

## 8. 用途与不做的用途

仅用于：

- 统计决策耗时分布，发现性能退化；
- 统计异常次数，定位崩溃；
- 统计动作分布，判断策略是否偏科（例如从不鸣牌）；
- `include_hands` 开启时复现具体决策、检查规则错误。

**不用于**：训练神经网络、出售、广告画像、与第三方共享、识别个人。

## 9. 变更

字段或行为变化会写进 `CHANGELOG.md` 并更新本文档日期；不兼容变更会提升 `v` 字段。

## 10. 联系

<https://github.com/leo-hizzen/calc3p/issues>

---

# Privacy Policy (English summary)

Telemetry is **on by default but pointed at loopback** (`http://127.0.0.1:8801/collect`) and flushes **at most once every 7 days**. Unless you replace `endpoint` with a server you control, the data never leaves your machine — the author receives nothing.

Every finished game appends one local record to `~/.calc3p/queue.jsonl` (max 500 lines): a random per-install id, bot/Python/OS versions, timestamp, end reason, and per-game counters (decisions, errors, action histogram, p50/p99 latency). With `include_hands = true` it also includes up to 200 per-turn hands plus the chosen discard.

It never collects account names, opponent identities, IP addresses, machine fingerprints, cookies, or your saved game records.

Disable entirely with `enabled = false` in `telemetry.toml`, or delete that file to fall back to the loopback default.
