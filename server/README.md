# 遥测接收端

一个**只用标准库**的最小实现，接收 Calc3P 每局结束时上报的那条记录，按 UTC 日期写入 JSONL。

作者不运营公共接收端，你需要自己跑一个（本机、VPS、容器都行）。

## 跑起来

```bash
python collector.py --host 0.0.0.0 --port 8801 --out ./data --token 换成你的口令
```

- `--host 0.0.0.0` 才能被外部访问；只在局域网用就保持默认 `127.0.0.1`。
- `--token` 为空时不校验身份；**对外暴露时务必设置**。
- 日志里不记录客户端 IP。

端点：

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/collect` 或 `/v1/collect` | 接收上报 |
| GET | `/healthz` | `{"ok":true,"records":N}` |
| GET | `/stats` | 记录数、去重后的 install 数、已有日期文件 |

## 客户端配置

在玩家自己的 bot 目录建 `telemetry.toml`：

```toml
[telemetry]
enabled = true
endpoint = "https://你的域名/collect"
include_hands = false
```

如果用 `--token`，客户端当前版本不发 `Authorization` 头（token 校验会返回 401）。需要鉴权时，请在反向代理层做（见下）。

## 放在 nginx 后面

```nginx
location /collect {
    proxy_pass http://127.0.0.1:8801/collect;
    client_max_body_size 8m;
    # 可选：在代理层加 Basic Auth 或 IP 白名单
}
```

## 数据长什么样

`data/2026-11-24.jsonl`，每行一条：

```json
{"v":1,"install_id":"4b0be5b9…","bot_version":"1.1.0","python":"3.12.13","os":"win32","ts":1795485080,"reason":"end_game","game":{"decisions":5,"errors":0,"actions":{"none":4,"dahai":1},"p50_ms":0.03,"p99_ms":0.71},"_received":1795485081}
```

开启 `include_hands = true` 时多一个 `hands` 数组（最多 200 条，每条含 14 张手牌与动作）。

## 快速统计

```bash
# 各版本用量
jq -r '.bot_version' data/*.jsonl | sort | uniq -c

# 安装数
jq -r '.install_id' data/*.jsonl | sort -u | wc -l

# 平均决策耗时
jq -s 'map(.game.p50_ms) | add / length' data/*.jsonl

# 异常总数
jq -s 'map(.game.errors) | add' data/*.jsonl
```

没有 `jq` 的话，用 `python -c` 读同一批文件也一样。

## 边界

- 不做去重、不做限流、不做持久化队列——这是最小实现，不是生产服务。
- 单条记录超过 8 MB 会被拒（正常远小于此）。
- 客户端失败即静默：本端挂了不会影响任何人打牌。
- 想删某人的数据，直接在 JSONL 里按 `install_id` 过滤重写即可。

## 合规提醒

接收端会持有玩家上报的对局统计（以及可选的手牌）。请在你自己的隐私说明里写清楚：存哪、存多久、谁能看、怎么删。别把原始数据连 IP 一起公开。
