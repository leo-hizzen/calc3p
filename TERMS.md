# 使用条款 / Terms of Use

Calc3P v1.0.0 · 最后更新：2026-11-24

## 1. 这是什么

Calc3P 是一个**纯计算的立直麻将（三人麻将）决策引擎**，以 Akagi 的 mjai bot 形式运行。它不含神经网络、不含训练得到的权重，所有决策来自显式的向听搜索、进张统计与期望值打分。

它输出的是**建议**。是否采纳、如何出牌，由你决定。

## 2. 许可

源码以 MIT 许可证发布（见 `LICENSE`），版权人「愚」。你可以自由使用、修改、再分发，包括商业用途，条件是保留版权声明与许可声明。

## 3. 使用风险由你承担

麻将是对抗性游戏，任何 AI 都不可能保证战绩。你理解并同意：

- 本软件**按「现状」提供**，不附带任何明示或暗示的担保，包括但不限于适销性、特定用途适用性、不侵权；
- 作者不对任何战绩、段位变化、点数增减、账号处罚、比赛结果负责；
- 使用本软件所产生的一切后果（包括因违反第三方平台规则而导致的封禁、扣分、禁言）由**你本人**承担。

## 4. 第三方平台规则

Calc3P 通过 Akagi 读取对局数据。**Akagi 本身以及任何实时分析／自动出牌工具，都可能违反雀魂（Mahjong Soul）、天凤等平台的用户协议**，历史上存在封号先例。

你需要自行判断并承担风险。作者不鼓励、不指导、也不为任何形式的自动化对局或代打行为背书。用于牌谱复盘、离线分析、规则研究是相对低风险的使用方式；用于实时对局辅助或自动出牌风险更高，请自行评估。

本项目与雀魂、天凤、Akagi 的开发者均无关联，未获其授权或认可。

## 5. 可接受用途

- 个人学习麻将理论、验证向听/进张/打点计算；
- 离线复盘自己的牌谱；
- 二次开发、教学、学术研究；
- 与他人分享改进版本（保留 MIT 声明）。

## 6. 不可接受用途

- 将本软件作为服务出售而对源码或修改保持闭源（MIT 允许再分发，但你需要遵守 MIT 的声明保留要求）；
- 声称本软件由雀魂/天凤官方提供或认可；
- 用于骚扰、作弊对战、或其他违反当地法律的行为；
- 移除或伪造版权与许可声明。

## 7. 遥测

默认**不采集任何数据**。只有你在 bot 目录主动创建 `telemetry.toml` 并配置 `endpoint` 后，才会向你指定的地址上报统计信息。完整字段见 [`PRIVACY.md`](PRIVACY.md)。

作者不运营任何接收端，因此不掌握你上报的数据。

## 8. 无支持义务

作者没有义务提供支持、修复、更新或回应 issue，但通常乐意回应。使用前请先读 `README.md` 的「已知限制」一节。

## 9. 条款变更

条款可能随版本更新。继续使用新版本即视为接受新条款；重大变更会在 `CHANGELOG.md` 中标注。

## 10. 适用法律

本条款不指定特定司法管辖。若你所在地区的法律禁止本条款的某一部分，该部分在该地区无效，其余部分仍然有效。

---

# Terms of Use (English summary)

Calc3P is a **calculation-based** riichi mahjong (three-player) decision engine distributed as an Akagi mjai bot. It contains no neural network and no trained weights; every decision comes from explicit shanten search, tile-acceptance counting, and expected-value scoring. It produces **suggestions only**.

Released under the MIT License, copyright "愚". Provided **as is**, without warranty of any kind. You bear all consequences of use, including any account penalties resulting from violating the terms of service of Mahjong Soul, Tenhou, or any other platform — real-time assistance and automated play have led to bans before, and this project is not affiliated with or endorsed by any of those platforms.

Telemetry is **off by default**. It starts only if you create `telemetry.toml` with your own `endpoint`. See `PRIVACY.md` for exact fields. The author operates no collection server.
