# 三麻规则适配清单

这份文档记录 Calc3P 对「立直三麻」与「立直四麻」全部差异的处理方式。每条都对应一个真实踩过的 bug，不是抄规则书。

---

## 1. 牌种：没有 2m–8m

三麻只保留万子的 1 和 9，共 27 种、108 张。

| 花色 | 牌种 | 张数 |
|---|---|---|
| 万子 | 1m、9m | 8 |
| 筒子 | 1p–9p | 36 |
| 索子 | 1s–9s | 36 |
| 字牌 | 东南西北白发中 | 28 |
| **合计** | **27 种** | **108 张** |

代码里的落点：

```python
def _is_dead_tile(i: int) -> bool:
    """三麻里不存在的牌种。"""
    return 1 <= i <= 7          # 2m–8m
```

影响三处：

1. `_improving_tiles` / `_winning_tiles` 遍历 34 种时要跳过这 7 种，否则进张数虚高。
2. `_shanten` 必须传 `is_three_player=True`。传 `False` 时库会允许 `1m 2m 3m` 组顺子，向听普遍偏乐观——实测同一手牌两种算法差 1 个向听。
3. `1m` / `9m` 永远组不成顺子，浮牌价值等同于字牌（客风档 150），而不是「能组 123 的普通幺九」。

> **幽灵刻子的坑**：模拟副露时要往 `tiles_34` 里补 padding。用 2m–8m 当 padding 会触发库的 `Invalid tile for three player`，因为 `is_three_player=True` 时校验器直接拒收这些牌种。改用字牌（27–33）做 padding，两种规则下都合法。

---

## 2. 不能吃

三麻没有吃。合法鸣牌只剩：碰、明杠、暗杠、加杠。

代码里 `decide_after_others_dahai` 只判断荣和、大明杠、碰。

---

## 3. 无食い断幺

四麻副露后断幺九仍是役。**三麻不是**——副露后断幺不算役，只能靠其他役种和牌。

```python
def _has_viable_yaku(state, hand34, melds) -> bool:
    all_simple = all(...)  # 全部为 2–8 的数牌
    if all_simple and not melds:   # 门清才算断幺
        return True
    ...
```

早期版本写成 `if all_simple: return True`，导致 bot 在没有其他役的情况白碰，碰完发现无役，只能硬打。

---

## 4. 拔北

北可以「拔」出，每张 +1 番（Nuki Dora），拔完从牌山补一张。

### 拔不拔的判断

```python
# 拔北后手牌 13 张，与「打一张后也是 13 张」比向听
kita_sh = _shanten(hand 去北)
best_discard_sh = min(所有候选打牌后的向听)
slack = 1 if dora_tiles >= 2 else 0      # 手牌宝牌 >= 2 张时允许退一档
拔 = kita_sh <= best_discard_sh + slack
```

早期版本比较的是「14 张手牌的向听」，等于拿未拔北的状态跟拔北后比，永远拔——即使拔完退两档。

### 状态机的坑

bot 返回 `{"type":"kita"}` 时已经自行 `_remove("N")`。Akagi 随后会回一个 kita echo，如果 `consume` 里还按「手里有北就减一张」处理，手牌会凭空少一张。修法是加 `pending_kita` 标志：

```python
if self.pending_kita:
    self.pending_kita = False       # 这次 echo 是回应我们自己的拔北，已经扣过了
elif self.hand34[_HONOR["N"]] > 0:
    ...
```

### 北不是役牌

三麻的北刻**没有** Yakuhai（不是自风、不是场风、不是三元）。只有拔北产生的宝牌。

---

## 5. 自风随场风旋转

四麻里「东场庄家自风 = 东」是巧合。正确关系是：

```
自风 = order[(base + 相对座次) % 3]
其中 order = [EAST, SOUTH, WEST], base = order.index(场风)
```

南场、oya = 0 时的座位表：

| 座位 | 自风 |
|---|---|
| 0（庄） | 南 |
| 1 | 西 |
| 2 | 东 |

早期版本用绝对座次 `[EAST,SOUTH,WEST][(actor - oya) % 3]`，南场把庄家自风算成东，全家错一位，整局风牌估值全错。

---

## 6. 自摸损与庄家倍率

三麻闲家自摸要「损」一半（两家各付 1/2、庄家付 1/2 后不再加），庄家自摸三家各付 1/3。

这不是自己算的，全交给 `mahjong` 库：

```python
config = HandConfig(
    is_tsumo=..., is_riichi=...,
    player_wind=..., round_wind=...,
    is_three_player=True,
    tsumi_number=state.honba,        # 本场
    kyoutaku_number=state.kyotaku,   # 供托
    num_nuki_dora=state.nuki,
)
```

**关键**：`tsumi_number` 和 `kyoutaku_number` 两个参数早期一个都没传，导致满贯一律算成 8000、供托永远不加。

---

## 7. 本场与供托

| 项目 | 效果 |
|---|---|
| 本场 | 每本场，和牌方从放铳方多收 100 点/本场（自摸则每家 +100） |
| 供托 | 每 1000 点供托，和牌方额外收 1000 |

三麻自摸时「本场」是每家 +100（不是 +300），库按 `is_three_player` 自动处理，但前提是参数传对了。

---

## 8. 赤宝牌的位置

`mahjong` 库约定：**赤 5 固定占该牌种的 slot 0**。

| 牌 | 牌种 index | 136 编码基准 | 赤 5 的 id |
|---|---|---|---|
| 5m | 4 | 16–19 | 16 |
| 5p | 13 | 52–55 | 52 |
| 5s | 22 | 88–91 | 88 |

早期 `_hand34_to_136` 用 `idx * 4 + slot` 从 slot 0 起分配，于是**任何牌种的第一个 5 都被判成赤**——手里 5p + 5s 白送 `Aka Dora (2)`。

修法：

```python
n_aka = min(aka_in_hand_of_this_suit, count)
slots = [0] * n_aka + [min(1 + k, 3) for k in range(count - n_aka)]
```

非赤从 slot 1 起，真赤才占 slot 0。同时 `win_136` 不能再取 `win_idx * 4`，要从实际分配的 id 里找：

```python
same = [t for t in tiles136 if t // 4 == win_idx]
win_136 = same[0] if same else win_idx * 4
```

---

## 9. 宝牌循环

四麻的宝牌指示是 `1→2→…→9→1`。**三麻的万子是 `1m→9m→1m`**，因为中间没有 2m–8m。

`mahjong` 库不知道这件事，所以要自己映射：

```python
_TENHOU_3P_DORA = {
    0: 8, 8: 0,                    # 1m <-> 9m
    **{i: i + 1 for i in range(9, 17)},   # 1p..8p -> 2p..9p
    17: 9,                          # 9p -> 1p
    **{i: i + 1 for i in range(18, 26)},  # 1s..8s
    26: 18,                         # 9s -> 1s
    **{i: (i - 26) % 7 + 27 for i in range(27, 34)},  # 字牌循环
}
```

算点数时用 `_synthetic_indicator()` 给库喂一个「四麻意义下的前一指示牌」，让库自己算出正确的三麻宝牌。

---

## 10. 点数上限

三麻的满贯线比四麻高（因为少一家、自摸损）：

| 等级 | 闲家荣和 | 庄家荣和 |
|---|---|---|
| 满贯 | 8000 | 12000 |
| 跳满 | 12000 | 18000 |
| 倍满 | 16000 | 24000 |
| 三倍满 | 24000 | **36000** |
| 役满 | 32000 | 48000 |

庄家三倍满 36000 是合法的——曾误判为「超过 32000 不可能」，实际查库输出 `yaku_level='sanbaiman'`、`main=36000`，完全正确。

---

## 11. 向听与和牌形的库调用要点

```python
# 向听：必须 is_three_player=True
Shanten().calculate_shanten(tiles_34, is_three_player=True)

# 和牌点数：必须传「含和牌张的 14 张」
_hand_value(state, hand34_full_14, aka, win_idx, is_tsumo, nuki, riichi)
# 传 13 张 → HandResponse(error='hand_not_winning')
```

`HandResponse` 不是 dict，取字段用属性：`.han` `.fu` `.cost` `.yaku` `.error` `.is_open_hand` `.fu_details`。

`cost` 结构：

```python
{'main': 8000, 'additional': 0, 'total': 8000, 'for_tsumo': ..., 'for_ron': ...}
```

对每种和牌张取 `max(自摸, 荣和)` 的 `total`，再按各张剩余枚数加权平均。
