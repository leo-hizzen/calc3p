# Akagi 3P calculation bot — no training, no neural net.
#
# Strategy (three-player only):
#   * Every candidate discard is scored by an explicit expected-value model:
#         EV = P(win) * E[points]  -  P(deal-in) * E[loss]
#   * P(win) comes from a shanten DP over the remaining wall, using the count
#     of live tiles that improve the hand (ukeire).  When already tenpai the
#     wait count is exact, so the number shown is the real self-draw chance.
#   * P(deal-in) comes from a danger model driven by declared riichi, melds,
#     genbutsu / suji, and visible tile counts.
#   * Calls: only pon / daiminkan exist in 3p (no chi).  A call is taken only
#     when it strictly lowers shanten and a yaku path survives.
#   * Kita (north extraction) is evaluated as a free draw plus one dora.

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass, field

from mahjong.constants import EAST, NORTH, SOUTH, WEST
from mahjong.hand_calculating.hand import HandCalculator
from mahjong.hand_calculating.hand_config import HandConfig, OptionalRules
from mahjong.meld import Meld
from mahjong.shanten import Shanten
from mahjong.tile import TilesConverter

NUM_PLAYERS = 3
NOTIFY_PREFIX = "@@AKAGI_NOTIFY@@ "

_HONOR = {"E": 27, "S": 28, "W": 29, "N": 30, "P": 31, "F": 32, "C": 33}
_HONOR_REV = {v: k for k, v in _HONOR.items()}
_WIND_MJAI = {EAST: "E", SOUTH: "S", WEST: "W", NORTH: "N"}

# 2m..8m (indices 1..7) never exist in three-player mahjong, which makes them
# a safe source of ghost melds when padding an open hand for shanten counting.
_GHOST_SLOTS = [27, 28, 29, 30, 31, 32, 33]  # honours: legal sanma padding

_SHANTEN = Shanten()
_CALC = HandCalculator()
_OPTS = OptionalRules(has_open_tanyao=False, has_aka_dora=True, is_three_player=True)


def notify(level: str, title: str, body: str | None = None, *, id: str | None = None) -> None:
    payload: dict[str, object] = {"level": level, "title": title}
    if body is not None:
        payload["body"] = body
    if id is not None:
        payload["id"] = id
    sys.stderr.write(NOTIFY_PREFIX + json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stderr.flush()


def _normalize(tile: str) -> str:
    return tile[:-1] if tile.endswith("r") else tile


def _to34(tile: str) -> int:
    t = _normalize(tile)
    if t in _HONOR:
        return _HONOR[t]
    n = int(t[0])
    if n == 0:
        n = 5
    suit = t[1]
    return {"m": 0, "p": 9, "s": 18}[suit] + (n - 1)


def _from34(idx: int) -> str:
    if idx >= 27:
        return _HONOR_REV[idx]
    suit = "mps"[idx // 9]
    return f"{(idx % 9) + 1}{suit}"


def _is_yaochuu(idx: int) -> bool:
    if idx >= 27:
        return True
    n = idx % 9
    return n == 0 or n == 8


def _is_honor(idx: int) -> bool:
    return idx >= 27


# ---------------------------------------------------------------- dora mapping
#
# Three-player mahjong drops 2m..8m, so the dora cycle is not the 34-tile one.
# Rather than reimplement score calculation we hand mahjong a synthetic
# *four-player* indicator that maps onto the correct three-player dora.

_TENHOU_3P_DORA = {
    # m suit: only 1m and 9m exist
    0: 8, 8: 0,
    # p / s: normal
    9: 10, 10: 11, 11: 12, 12: 13, 13: 14, 14: 15, 15: 16, 16: 17, 17: 9,
    18: 19, 19: 20, 20: 21, 21: 22, 22: 23, 23: 24, 24: 25, 25: 26, 26: 18,
    # honors: E S W N cycle, then P F C cycle
    27: 28, 28: 29, 29: 30, 30: 27,
    31: 32, 32: 33, 33: 31,
}


def _synthetic_indicator(indicator_idx: int) -> int:
    """Return a 4p indicator whose 4p successor is the 3p dora of `indicator`."""
    target = _TENHOU_3P_DORA[indicator_idx]
    if target >= 27:
        return (target - 27 - 1) % 7 + 27
    if target < 9:
        # 1m -> prev is 9m; 9m -> prev is 8m
        return 8 if target == 0 else target - 1
    base = 9 if target < 18 else 18
    r = target - base
    return base + (r - 1) % 9


def _dora_count(hand34: list[int], melds: list[dict], indicators: list[str]) -> int:
    counts = [0] * 34
    for i, n in enumerate(hand34):
        counts[i] += n
    for m in melds:
        for t in m["tiles"]:
            counts[_to34(t)] += 1
    total = 0
    for ind in indicators:
        idx = _to34(ind)
        dora = _TENHOU_3P_DORA[idx]
        total += counts[dora]
    return total


# ---------------------------------------------------------------- hand helpers

def _shanten(hand34: list[int], num_melds: int) -> int:
    total = sum(hand34) + num_melds * 3
    if total not in (13, 14):
        return 8
    if num_melds == 0:
        try:
            return _SHANTEN.calculate_shanten(hand34, is_three_player=True)
        except ValueError:
            # 2m..8m can only appear through a malformed state; treat it as no
            # information rather than killing the process mid-hand.
            return 8
    # Melds already on the table have to be represented as finished sets, but
    # the library has no "open melds" argument. Padding with 2m..8m is not an
    # option under sanma rules: the validator rejects those tiles, and worse,
    # treating them as legal would let the search complete 123m runs that can
    # never exist. Honours fill the same role and are legal in both rule sets,
    # so a ghost triplet is a ghost triplet.
    padded = list(hand34)
    placed = 0
    for g in _GHOST_SLOTS:
        if placed >= num_melds:
            break
        if padded[g] != 0:
            continue  # never overwrite a real tile that happens to sit here
        padded[g] = 3
        placed += 1
    if placed < num_melds:
        return 8
    return _SHANTEN.calculate_shanten(padded, is_three_player=True)


def _live_count(state: "State", idx: int) -> int:
    return max(0, 4 - state.visible_count(idx))


def _improving_tiles(hand34: list[int], num_melds: int) -> list[tuple[int, int]]:
    """[(tile_idx, live_count)] that strictly reduce shanten when drawn."""
    sh = _shanten(hand34, num_melds)
    out: list[tuple[int, int]] = []
    for idx in range(34):
        if _is_dead_tile(idx):
            continue
        if hand34[idx] >= 4:
            continue
        hand34[idx] += 1
        new_sh = _shanten(hand34, num_melds)
        hand34[idx] -= 1
        if new_sh < sh:
            out.append((idx, 0))
    return out


def _is_dead_tile(idx: int) -> bool:
    """2m..8m do not exist in three-player mahjong."""
    return 1 <= idx <= 7


def _winning_tiles(hand34: list[int], num_melds: int) -> list[tuple[int, int]]:
    sh = _shanten(hand34, num_melds)
    if sh != 0:
        return []
    out: list[tuple[int, int]] = []
    for idx in range(34):
        if _is_dead_tile(idx) or hand34[idx] >= 4:
            continue
        hand34[idx] += 1
        new_sh = _shanten(hand34, num_melds)
        hand34[idx] -= 1
        if new_sh == -1:
            out.append((idx, 0))
    return out


def _hand34_to_136(hand34: list[int], aka: dict[str, int]) -> list[int]:
    """Expand a 34-index count vector into the library's 136-tile ids.

    The library encodes the red five as slot 0 of each five (id 16/52/88), so
    ordinary copies must start at slot 1. Reusing slot 0 for every five made
    each 5 score as aka dora even when no red tile was held.
    """
    out: list[int] = []
    aka_left = dict(aka)
    for idx, n in enumerate(hand34):
        if n <= 0:
            continue
        base = idx * 4
        suit = "mps"[idx // 9] if idx < 27 else ""
        n_aka = 0
        if idx in (4, 13, 22) and suit:
            n_aka = min(n, aka_left.get(suit, 0))
            aka_left[suit] = aka_left.get(suit, 0) - n_aka
        slots = [0] * n_aka
        for k in range(n - n_aka):
            slots.append(min(1 + k, 3))
        for s in slots:
            out.append(base + s)
    return out


def _meld_to_mahjong(meld: dict, tiles136: list[int]) -> Meld:
    if meld["type"] == "pon":
        return Meld(meld_type=Meld.PON, tiles=tiles136, opened=True)
    if meld["type"] == "daiminkan":
        return Meld(meld_type=Meld.KAN, tiles=tiles136, opened=True)
    if meld["type"] == "kakan":
        return Meld(meld_type=Meld.SHOUMINKAN, tiles=tiles136, opened=True)
    if meld["type"] == "ankan":
        return Meld(meld_type=Meld.KAN, tiles=tiles136, opened=False)
    raise ValueError(f"unknown meld type: {meld['type']}")


def _hand_value(
    state: "State",
    hand34_full: list[int],
    aka: dict[str, int],
    win_idx: int,
    is_tsumo: bool,
    nuki: int = 0,
    riichi: bool = False,
):
    hand136 = _hand34_to_136(hand34_full, aka)
    # The library wants every tile of the hand, melded ones included, so the
    # meld ids have to be handed out alongside the concealed ones without
    # colliding with them.
    used = set(hand136)
    melds: list[Meld] = []
    for m in state.melds:
        ids: list[int] = []
        for t in m["tiles"]:
            base = _to34(t) * 4
            free = [base + s for s in range(4) if base + s not in used]
            chosen = free[0] if free else base
            used.add(chosen)
            ids.append(chosen)
        melds.append(_meld_to_mahjong(m, ids))
    tiles136 = hand136 + [i for m in melds for i in m.tiles]
    # win_tile must be one of the ids actually handed to the library; slot 0 is
    # reserved for red fives now, so it is not always the winning tile's id.
    same = [t for t in hand136 if t // 4 == win_idx]
    win_136 = same[0] if same else win_idx * 4
    dora_136 = [_synthetic_indicator(_to34(d)) * 4 for d in state.dora_indicators]
    cfg = HandConfig(
        is_tsumo=is_tsumo,
        is_riichi=riichi,
        player_wind=state.seat_wind(),
        round_wind=state.round_wind(),
        # Honba and riichi sticks are real points on top of the hand value; the
        # library folds them into cost["total"] when they are passed in.
        kyoutaku_number=state.kyotaku,
        tsumi_number=state.honba,
        options=_OPTS,
        num_nuki_dora=nuki,
    )
    return _CALC.estimate_hand_value(
        tiles=tiles136,
        win_tile=win_136,
        melds=melds or None,
        dora_indicators=dora_136 or None,
        config=cfg,
    )


def _yaku_ok(res) -> bool:
    return res is not None and res.cost is not None and res.han is not None and res.han > 0


# ---------------------------------------------------------------- probability

def _win_probability(shanten: int, live_tiles: int, wall: int, turns: int) -> float:
    """Chance this hand wins before the round ends.

    Tenpai is computed exactly: each draw hits one of `live_tiles` out of the
    live wall, so 1-(1-p)^turns, discounted for the seats that get there first.
    Non-tenpai hands use an empirical sanma table scaled by how many tiles
    actually advance the hand — a pure binomial would badly overstate them
    because it ignores that rivals finish too.
    """
    if turns <= 0 or wall <= 0 or live_tiles <= 0:
        return 0.0
    turns = min(turns, wall)
    if shanten <= 0:
        p = min(1.0, live_tiles / wall)
        return min(0.95, (1.0 - (1.0 - p) ** turns) * 0.82)
    base = {1: 0.42, 2: 0.28, 3: 0.17, 4: 0.10, 5: 0.06}.get(shanten, 0.02)
    ref = {1: 22.0, 2: 30.0, 3: 36.0}.get(shanten, 40.0)
    # Keep the live-tile factor near neutrality. A 0.35..1.5 window let a wide
    # 3-shanten beat a narrow 2-shanten on draw count alone; efficiency is
    # ordered by shanten first, so this factor only breaks ties inside a tier.
    scale = min(1.30, max(0.50, live_tiles / ref))
    t_scale = min(1.25, max(0.55, turns / 18.0))
    return min(0.85, base * scale * t_scale)


def _turns_left(state: "State") -> int:
    # Three-player: 108 tiles, 13*3 dealt, 1 dora indicator, 14 dead wall.
    # Every remaining wall tile is one draw split across three seats.
    return max(0, state.left_tiles // NUM_PLAYERS)


# ---------------------------------------------------------------- danger

def _suji_ok(river: list[str], idx: int) -> bool:
    if _is_honor(idx) or idx < 0:
        return False
    base = 9 if idx < 18 else 18
    r = idx - base
    for t in river:
        ti = _to34(t)
        if ti < 9 or 9 <= ti < 18:
            tri = ti % 9
        elif ti < 27:
            tri = ti - 18
        else:
            continue
        if abs(tri - r) == 3:
            return True
    return False


def _danger(state: "State", idx: int) -> float:
    """Approximate deal-in probability for tile `idx`, over all opponents."""
    p_safe = 1.0
    for seat in range(NUM_PLAYERS):
        if seat == state.actor_id:
            continue
        river = state.rivers[seat]
        river_idx = [_to34(t) for t in river]
        riichi = state.opponent_riichi[seat]
        melded = state.opponent_melds[seat]
        if idx in river_idx:
            p = 0.0
        elif idx in (0, 8) and riichi:
            # Only 1m and 9m exist in the manzu suit: no ryanmen or penchan wait
            # can ever hold them, so against riichi they are much safer than an
            # ordinary terminal (which still sits on the 123 / 789 side wait).
            p = 0.03
        elif riichi:
            if _is_honor(idx):
                # Honour tiles cannot form ryanmen waits, so they look safer than
                # middle tiles — but tanki and shanpon on honours are common, and
                # a live honour is still roughly as dangerous as a 1/9 side wait.
                # Grade by how many copies are already visible: a tile we can see
                # three of is nearly dead, one we alone hold is fully live.
                seen = state.visible_count(idx)
                unseen = max(0, 4 - seen)
                if unseen == 0:
                    p = 0.0
                elif unseen == 1:
                    p = 0.025
                elif unseen == 2:
                    p = 0.075
                elif unseen == 3:
                    p = 0.115
                else:
                    p = 0.13
            elif _suji_ok(river, idx):
                p = 0.04
            else:
                r = idx % 9
                p = 0.16 if 2 <= r <= 6 else 0.09
        elif melded:
            p = 0.07 if not _is_honor(idx) and 2 <= (idx % 9) <= 6 else 0.04
        else:
            # Nobody is in riichi and nobody has melded: the only difference
            # between a safe discard and a dangerous one is how much the tile is
            # worth to somebody else. A dragon is a yaku by itself, a seat wind
            # is a yaku for that seat (and the next player getting a free call
            # costs us the draw, so it counts for more), the round wind is a
            # yaku for two seats, and a guest wind is worth nothing beyond the
            # pon itself.
            if idx == _HONOR["N"]:
                p = 0.02  # sanma north is thrown away for dora, never kept
            elif _is_honor(idx):
                if idx in (_HONOR["P"], _HONOR["F"], _HONOR["C"]):
                    p = 0.045
                else:
                    owners = [
                        s
                        for s in range(NUM_PLAYERS)
                        if s != state.actor_id and state.seat_wind_of(s) == idx
                    ]
                    if owners:
                        nxt = (state.actor_id + 1) % NUM_PLAYERS
                        p = 0.042 if nxt in owners else 0.032
                    elif state.round_wind() == idx:
                        p = 0.035
                    else:
                        p = 0.02
            else:
                p = 0.025
        p_safe *= 1.0 - p
    return 1.0 - p_safe


# ---------------------------------------------------------------- state

@dataclass
class State:
    actor_id: int = 0
    oya: int = 0
    bakaze: str = "E"
    kyoku: int = 1
    honba: int = 0
    kyotaku: int = 0
    hand34: list[int] = field(default_factory=lambda: [0] * 34)
    aka_in_hand: dict[str, int] = field(default_factory=lambda: {"m": 0, "p": 0, "s": 0})
    melds: list[dict] = field(default_factory=list)
    rivers: list[list[str]] = field(default_factory=lambda: [[] for _ in range(NUM_PLAYERS)])
    river_called: list[list[bool]] = field(default_factory=lambda: [[] for _ in range(NUM_PLAYERS)])
    opponent_riichi: list[bool] = field(default_factory=lambda: [False] * NUM_PLAYERS)
    opponent_melds: list[int] = field(default_factory=lambda: [0] * NUM_PLAYERS)
    last_self_tsumo: str = ""
    reach_declared: bool = False
    reach_accepted: bool = False
    left_tiles: int = 55
    dora_indicators: list[str] = field(default_factory=list)
    nuki: int = 0
    pending_reach: int = -1
    pending_kita: bool = False

    def consume(self, e: dict) -> None:
        t = e.get("type")
        if t == "start_game":
            if "id" in e:
                self.actor_id = int(e["id"])
            notify("info", "Calc 3P ready", f"Seat {self.actor_id}. Pure calculation, no model.")
        elif t == "start_kyoku":
            self.oya = e["oya"]
            self.bakaze = e["bakaze"]
            self.kyoku = e["kyoku"]
            self.honba = e["honba"]
            self.kyotaku = e["kyotaku"]
            self.hand34 = [0] * 34
            self.aka_in_hand = {"m": 0, "p": 0, "s": 0}
            for tile in e["tehais"][self.actor_id]:
                self._add(tile)
            self.melds = []
            self.rivers = [[] for _ in range(NUM_PLAYERS)]
            self.river_called = [[] for _ in range(NUM_PLAYERS)]
            self.opponent_riichi = [False] * NUM_PLAYERS
            self.opponent_melds = [0] * NUM_PLAYERS
            self.last_self_tsumo = ""
            self.reach_declared = False
            self.reach_accepted = False
            self.left_tiles = 55
            self.dora_indicators = [e["dora_marker"]]
            self.nuki = 0
            # Set when we answered kita ourselves, so the echo event does not
            # consume a second north from a hand that held more than one.
            self.pending_kita = False
            self.pending_reach = -1
        elif t == "dora":
            self.dora_indicators.append(e["dora_marker"])
        elif t == "tsumo":
            if e["actor"] == self.actor_id:
                self._add(e["pai"])
                self.last_self_tsumo = e["pai"]
            self.left_tiles = max(0, self.left_tiles - 1)
        elif t == "dahai":
            actor = e["actor"]
            self.rivers[actor].append(e["pai"])
            self.river_called[actor].append(False)
            if actor == self.actor_id:
                self._remove(e["pai"])
                self.last_self_tsumo = ""
        elif t == "reach":
            if e["actor"] != self.actor_id:
                self.opponent_riichi[e["actor"]] = True
        elif t == "reach_accepted":
            if e["actor"] == self.actor_id:
                self.reach_accepted = True
            else:
                self.opponent_riichi[e["actor"]] = True
        elif t == "kita":
            # The replacement draw (a tsumo event) is what consumes a wall
            # tile, so this event must not decrement it again.
            if e["actor"] == self.actor_id:
                if self.pending_kita:
                    self.pending_kita = False
                elif self.hand34[_HONOR["N"]] > 0:
                    self._remove("N")
                    self.nuki += 1
                self.last_self_tsumo = ""
        elif t in ("pon", "daiminkan"):
            target = e["target"]
            if self.river_called[target]:
                self.river_called[target][-1] = True
            if e["actor"] == self.actor_id:
                for c in e["consumed"]:
                    self._remove(c)
                self.last_self_tsumo = ""
                self.melds.append({
                    "type": t,
                    "tiles": list(e["consumed"]) + [e["pai"]],
                    "from_seat": target,
                    "called_tile": e["pai"],
                })
            else:
                self.opponent_melds[e["actor"]] += 1
        elif t == "kakan":
            if e["actor"] == self.actor_id:
                self._remove(e["pai"])
                for m in self.melds:
                    if m["type"] == "pon" and _normalize(m["tiles"][0]) == _normalize(e["pai"]):
                        m["type"] = "kakan"
                        m["tiles"].append(e["pai"])
                        break
            else:
                self.opponent_melds[e["actor"]] += 1
        elif t == "ankan":
            if e["actor"] == self.actor_id:
                for c in e["consumed"]:
                    self._remove(c)
                self.melds.append({"type": "ankan", "tiles": list(e["consumed"]), "from_seat": e["actor"]})
            else:
                self.opponent_melds[e["actor"]] += 1

    def _add(self, tile: str) -> None:
        if tile == "?":
            return
        self.hand34[_to34(tile)] += 1
        if tile.endswith("r"):
            self.aka_in_hand[_normalize(tile)[1]] += 1

    def _remove(self, tile: str) -> None:
        if tile == "?":
            return
        idx = _to34(tile)
        if self.hand34[idx] > 0:
            self.hand34[idx] -= 1
        if tile.endswith("r") and self.aka_in_hand[_normalize(tile)[1]] > 0:
            self.aka_in_hand[_normalize(tile)[1]] -= 1

    def seat_wind(self) -> int:
        return self.seat_wind_of(self.actor_id)

    def seat_wind_of(self, seat: int) -> int:
        # Seats rotate with the round wind: in a South round the dealer's own
        # wind is South, not East. Sanma only ever uses East/South/West.
        order = [EAST, SOUTH, WEST]
        rw = self.round_wind()
        base = order.index(rw) if rw in order else 0
        return order[(base + (seat - self.oya)) % NUM_PLAYERS]

    def round_wind(self) -> int:
        return {"E": EAST, "S": SOUTH, "W": WEST}.get(self.bakaze, EAST)

    def is_closed(self) -> bool:
        return all(m["type"] == "ankan" for m in self.melds)

    def visible_count(self, idx: int) -> int:
        n = self.hand34[idx]
        for m in self.melds:
            for t in m["tiles"]:
                if _to34(t) == idx:
                    n += 1
        for s in range(NUM_PLAYERS):
            for t in self.rivers[s]:
                if _to34(t) == idx:
                    n += 1
        for ind in self.dora_indicators:
            if _to34(ind) == idx:
                n += 1
        return n


# ---------------------------------------------------------------- evaluation

@dataclass
class Score:
    idx: int
    shanten: int
    live: int
    win_p: float
    value: float
    danger: float
    ev: float


def _estimate_value(
    state: State, hand13: list[int], win_idx: int, assume_riichi: bool = False
) -> float:
    """Expected points if this hand wins on `win_idx` (tsumo assumption).

    A closed hand that is not yet riichi can always declare riichi, which is
    itself a yaku. Valuing a yaku-less closed tenpai at 0 would make the bot
    abandon tenpai for a 1-shanten hand, so riichi is priced in as available.
    """
    riichi = state.reach_accepted or assume_riichi
    full = list(hand13)
    full[win_idx] += 1
    best = 0.0
    for tsumo in (True, False):
        try:
            res = _hand_value(state, full, state.aka_in_hand, win_idx, tsumo, state.nuki, riichi)
        except Exception:
            continue
        if _yaku_ok(res):
            best = max(best, float(res.cost["total"]))
    return best


def _score_discard(state: State, idx: int, hand14: list[int]) -> Score:
    hand13 = list(hand14)
    hand13[idx] -= 1
    num_melds = len(state.melds)
    sh = _shanten(hand13, num_melds)
    wall = max(1, state.left_tiles)
    turns = max(1, _turns_left(state))
    danger = _danger(state, idx)
    # A closed hand may still declare riichi, so a yaku-less tenpai must not be
    # valued at zero (that would make it lose to any 1-shanten discard).
    assume_riichi = state.is_closed() and not state.reach_declared and not state.reach_accepted

    if sh == 0:
        waits = _winning_tiles(hand13, num_melds)
        live = sum(_live_count(state, w) for w, _ in waits)
        # Weight the expected value by how often each wait actually wins it.
        tot = 0.0
        wsum = 0.0
        for w, _ in waits:
            lc = _live_count(state, w)
            if lc <= 0:
                continue
            pts = _estimate_value(state, hand13, w, assume_riichi)
            tot += pts * lc
            wsum += lc
        value = tot / wsum if wsum else 0.0
        win_p = _win_probability(0, live, wall, turns)
    else:
        imp = _improving_tiles(hand13, num_melds)
        live = sum(_live_count(state, i) for i, _ in imp)
        # Price the hand the way the rules do: expected han (the yaku this shape
        # leans towards, plus the dora actually in hand) run through the
        # standard han/fu score table. The flat 2600/1800 constants this
        # replaces ignored dora entirely, so a hand holding two dora looked
        # exactly as cheap as one holding none and the bot saw no reason to
        # keep them.
        dora_n = (
            sum(hand14[i] for i in range(34) if _is_dora(state, i))
            + state.nuki
            + sum(state.aka_in_hand.values())
        )
        value = _standard_points(
            round(_expected_han(state, hand13) + dora_n),
            30,
            state.oya == state.actor_id,
        )
        win_p = _win_probability(sh, live, wall, turns)

    # `danger` is already a probability: multiply it by the loss once.
    ev = win_p * value - danger * 4200.0
    # Lone honours are interchangeable to the shape search but not in value: a
    # dragon pair is a cheap yaku, your own wind can only be completed by you,
    # and the round wind is wanted by every seat too — so shed it first.
    if sh > 0:
        # Scaled so that keeping a tile is worth less than advancing one
        # shanten: moving 2-shanten to 1-shanten is worth roughly 400 points of
        # extra win expectation, so the whole structure table has to fit under
        # that or the bot plays keep-away instead of pushing the hand forward.
        ev -= 0.6 * _float_value(state, idx, hand14)
    return Score(idx=idx, shanten=sh, live=live, win_p=win_p, value=value, danger=danger, ev=ev)


def _sequence_uses(hand34: list[int], idx: int) -> bool:
    """True when a finished run in the hand already spends a copy of `idx`.

    Counting `hand34[idx] == 2` as "a pair" is wrong when one of the copies is
    needed by a run (e.g. 789p uses one of the 9p). Such a tile is really
    "sequence member + one spare", and discarding it does not break the run.
    """
    if idx >= 27:
        return False
    r = idx % 9
    base = idx - r
    for start in (r - 2, r - 1, r):
        if start < 0 or start > 6:
            continue
        if all(hand34[base + start + k] > 0 for k in range(3)):
            return True
    return False


def _neighbour_count(hand14: list[int], idx: int) -> int:
    """How many tiles within +-2 of `idx` we still hold in the same suit."""
    if idx >= 27:
        return 0
    lo = (idx // 9) * 9
    hi = lo + 8
    return sum(
        1 for d in (-2, -1, 1, 2) if lo <= idx + d <= hi and hand14[idx + d] > 0
    )


def _sequence_shape(hand14: list[int], idx: int) -> str | None:
    """How `idx` sits inside a two-tile sequence block, if it sits in one.

    `ryanmen` waits on two kinds of tile, `kanchan` on the gap, and `penchan`
    only on the single tile that completes 123 / 789.
    """
    if idx >= 27:
        return None
    r = idx % 9
    lo = idx - r
    hi = lo + 8

    def has(j: int) -> bool:
        return lo <= j <= hi and hand14[j] > 0

    right1, left1 = has(idx + 1), has(idx - 1)
    if right1 or left1:
        # 12 and 89 are the only two-tile blocks a sequence cannot extend on
        # both sides, so they are worth distinctly less than 23..78.
        if right1 and r in (0, 7):
            return "penchan"
        if left1 and (r - 1) in (0, 7):
            return "penchan"
        return "ryanmen"
    if has(idx + 2) or has(idx - 2):
        return "kanchan"
    return None


def _is_dora(state: "State", idx: int) -> bool:
    """True when `idx` is a dora under sanma's own indicator cycle."""
    for ind in state.dora_indicators:
        if ind == "?":
            continue
        if _TENHOU_3P_DORA[_to34(ind)] == idx:
            return True
    return False


def _rank_scores(scores: list[Score]) -> list[Score]:
    """Order discards by shanten first, expected value second.

    Ranking by EV alone let a 3-shanten hand with many live tiles outrank a
    2-shanten hand with few (real log 20:29:56: 7p ev -138.6 beat S ev -151.1
    although S left the hand two shanten closer). Tile efficiency is
    lexicographic: one step closer to tenpai wins, and only a discard that
    leaves no live tiles at all drops out of the top tier.
    """
    viable = [s for s in scores if s.live > 0] or scores
    best_sh = min(s.shanten for s in viable)
    return sorted(viable, key=lambda s: (s.shanten != best_sh, -s.ev))


def _float_value(state: "State", idx: int, hand14: list[int]) -> float:
    """Future value given up by discarding `idx`.

    Shanten only counts how many tiles are still missing; it cannot see what is
    already built. This prices the structure the discard would destroy — from a
    finished triplet down to a useless guest wind — plus dora and red-five
    value, which the shanten number ignores entirely.
    """
    value = 0.0
    # Dora and the red five are points regardless of shape.
    if _is_dora(state, idx):
        value += 600.0
    if idx in (4, 13, 22) and state.aka_in_hand.get("mps"[idx // 9], 0) > 0:
        value += 800.0

    n = hand14[idx]
    if n >= 3:
        # A triplet is already a finished set: taking one copy turns it back into
        # a mere pair, and the shape search cannot see that loss because a pair
        # still counts as progress. A yaku triplet is worse still — breaking it
        # can strip the hand of its only yaku.
        round_w = state.round_wind()
        seat_w = state.seat_wind()
        if idx >= 31 or idx == round_w or idx == seat_w:
            return value + 900.0
        return value + 600.0
    if n == 2:
        # A pair whose copies are also consumed by a finished sequence is really
        # "sequence member + one spare". Discarding one copy keeps the run alive,
        # so the real loss is smaller than breaking a free-standing pair.
        if _sequence_uses(hand14, idx):
            return value + 350.0
        # Breaking a pair costs the head. With one or two pairs in the hand there
        # is little to fall back on, so that copy is worth keeping.
        pairs = sum(1 for c in hand14 if c >= 2)
        if pairs == 1:
            return value + 900.0
        if pairs == 2:
            return value + 600.0
        # A third pair is still a live triplet/head candidate. Pricing it below an
        # isolated 2/8 (which was 400) made the bot break pairs while keeping dead
        # tiles -- the exact complaint that produced this branch.
        return value + 450.0
    if n != 1:
        return value

    if _is_honor(idx):
        round_w = state.round_wind()
        seat_w = state.seat_wind()
        # A lone honour only pays off by drawing two more copies (~3 live tiles
        # at best), while a middle tile has 8-11 tiles that improve it. Comparing
        # 2497 replayed decisions against mortal3p, this branch over-valued lone
        # honours by far the most: in 444 of the 1130 top-1 disagreements the bot
        # discarded a middle or terminal tile while mortal3p threw the honour.
        if idx >= 31:  # dragons: a pair is already a yaku, but a lone one is not
            return value + 340.0
        if idx == round_w and idx == seat_w:
            return value + 400.0  # double wind
        if idx == seat_w:
            return value + 330.0  # own wind: one pair and it is a yaku
        if idx == round_w:
            return value + 200.0  # round wind: every opponent wants it as well
        return value + 70.0  # guest wind: nearly worthless

    shape = _sequence_shape(hand14, idx)
    if shape == "ryanmen":
        return value + 700.0  # 8 live tiles
    if shape == "kanchan":
        return value + 450.0  # 4 live tiles, in the gap
    if shape == "penchan":
        return value + 350.0  # 4 live tiles, one kind only

    r = idx % 9
    # Sanma keeps only 1m and 9m of the manzu suit, so neither can ever join a
    # sequence: an isolated one is worth about a guest wind, not a normal
    # terminal. 1p, by contrast, still grows on 2p/3p.
    if idx in (0, 8):
        base = 150.0
    elif 2 <= r <= 6:
        base = 400.0  # 3-7: sequence core, but a lone copy still needs a partner
    elif r in (1, 7):
        base = 320.0  # 2 and 8: one direction left, weaker than 3-7
    else:
        base = 260.0  # terminals: pair or 123 only
    # Neighbours mean the tile can still grow in more than one direction, which
    # the pairwise shape check above cannot see (e.g. 3345).
    base += 60.0 * _neighbour_count(hand14, idx)
    return value + base


def _is_safe(state: State, idx: int) -> bool:
    for seat in range(NUM_PLAYERS):
        if seat == state.actor_id:
            continue
        if idx in [_to34(t) for t in state.rivers[seat]]:
            return True
    return False


def _standard_points(han: int, fu: int, dealer: bool) -> float:
    """Ron value from the standard han/fu score table.

    Sanma uses the same table as four-player mahjong. The sanma-only rules
    (no 2-8 man, north extraction, tsumo loss) change what a hand can become
    and how a tsumo splits, but not what a given han/fu ron is worth.
    """
    if han >= 13:
        base = 8000
    elif han >= 11:
        base = 6000
    elif han >= 8:
        base = 4000
    elif han >= 6:
        base = 3000
    elif han >= 5:
        base = 2000
    elif han == 4 and fu >= 40:
        base = 2000
    elif han == 3 and fu >= 70:
        base = 2000
    elif han <= 0:
        return 0.0
    else:
        base = fu * (2 ** (2 + han))
    return float(base * (6 if dealer else 4))


def _expected_han(state: "State", hand13: list[int]) -> float:
    """Expected han for this shape if it reaches tenpai and wins.

    Shanten treats every tile as interchangeable and every hand as equally
    worth completing, so it happily keeps an unplayable pile of isolated
    tiles. This prices the money-making shapes — honitsu/chinitsu, tanyao,
    chiitoitsu and ittsu — as han, so that _standard_points can turn them into
    real points. Partial progress counts fractionally: four pairs is not yet
    chiitoitsu but it is closer to it than three.
    """
    total = sum(hand13) + len(state.melds) * 3
    if total <= 0:
        return 1.0

    suits = [0, 0, 0]
    honours = 0
    for i in range(34):
        n = hand13[i]
        if n == 0:
            continue
        if i >= 27:
            honours += n
        else:
            suits[i // 9] += n
    for m in state.melds:
        for t in m["tiles"]:
            idx = _to34(t)
            if idx >= 27:
                honours += 1
            else:
                suits[idx // 9] += 1

    # Baseline: a closed hand that gets there is almost always at least riichi
    # (1) plus menzen tsumo or pinfu (1) -- call it 2.5 to also cover the dora
    # that turn up. An open hand has already spent a yaku on the call.
    han = 2.5 if state.is_closed() else 1.5

    # Chinitsu (6 closed) / honitsu (3 closed): sanma hands collapse into one
    # suit far more often than in four-player mahjong.
    main = max(suits)
    off_suit = total - main - honours
    if main >= 10 and off_suit <= 2:
        han += 5.0 * min(1.0, (main - 9) / 4.0)
    elif main >= 9 and off_suit <= 4:
        han += 1.2 * min(1.0, (main - 8) / 4.0)

    # Tanyao: sanma has no kuitan, so this only exists in a closed hand.
    yaochuu = sum(hand13[i] for i in (0, 8, 9, 17, 18, 26)) + honours
    if state.is_closed():
        if yaochuu == 0:
            han += 1.0
        elif yaochuu <= 2:
            han += 0.3

    # Chiitoitsu (2): each pair past the third is real progress towards it.
    if not state.melds:
        pairs = sum(1 for c in hand13 if c >= 2)
        if pairs >= 4:
            han += 0.6 * (pairs - 3)

    # Ittsu (2): two of the three runs already complete is a real head start.
    for lo in (0, 9, 18):
        blocks = sum(
            1
            for start in (0, 3, 6)
            if all(hand13[lo + start + k] > 0 for k in range(3))
        )
        if blocks >= 2:
            han += 1.0 * (blocks - 1)

    return han


def _opponent_riichi_active(state: State) -> bool:
    return any(state.opponent_riichi[s] for s in range(NUM_PLAYERS) if s != state.actor_id)


# ---------------------------------------------------------------- decisions

def decide_after_own_tsumo(state: State, last: dict) -> dict:
    actor = state.actor_id
    drawn = last["pai"]
    drawn_idx = _to34(drawn)
    hand14 = list(state.hand34)

    # 1. Tsumo
    try:
        res = _hand_value(state, hand14, state.aka_in_hand, drawn_idx, True, state.nuki, state.reach_accepted)
    except Exception:
        res = None
    if _yaku_ok(res):
        return {"type": "hora", "actor": actor, "target": actor, "pai": drawn}

    # 2. Kita (north extraction): one dora for a free replacement draw.
    # Compare 13-tile hands only: the north is taken instead of some discard,
    # so it is worth it whenever the hand is no further from tenpai than the
    # best normal discard would leave it (plus a fresh draw).
    north = _HONOR["N"]
    if hand14[north] > 0 and not state.reach_accepted:
        best_discard_sh = 8
        for i in range(34):
            if hand14[i] <= 0:
                continue
            t2 = list(hand14)
            t2[i] -= 1
            s2 = _shanten(t2, len(state.melds))
            if s2 < best_discard_sh:
                best_discard_sh = s2
        test = list(hand14)
        test[north] -= 1
        kita_sh = _shanten(test, len(state.melds))
        # Kitadori is worth one shanten step once the hand already holds two
        # dora or more: the north itself becomes another one.
        dora_tiles = sum(1 for i in range(34) if hand14[i] > 0 and _is_dora(state, i))
        slack = 1 if dora_tiles >= 2 else 0
        if kita_sh <= best_discard_sh + slack:
            # Consume the north ourselves: the kita echo can arrive after the
            # replacement tile, and the tile already left the hand.
            state._remove("N")
            state.nuki += 1
            state.pending_kita = True
            sh_now = _shanten(test, len(state.melds))
            return {
                "type": "kita",
                "actor": actor,
                "meta": {
                    "show": {
                        "title": f"Calc 3P · 拔北 · {sh_now}向听",
                        "items": [
                            {
                                "label": "拔北",
                                "pais": ["N"],
                                "value": "拔北",
                                "note": f"宝牌 +1（已拔 {state.nuki}）",
                            },
                            {
                                "label": "补牌后",
                                "value": f"{sh_now}向听",
                                "note": "13 张等补一张",
                            },
                        ],
                    }
                },
            }

    # 3. Ankan
    if not state.reach_accepted:
        sh14 = _shanten(hand14, len(state.melds))
        for i in range(34):
            if _is_dead_tile(i) or hand14[i] < 4:
                continue
            test = list(hand14)
            test[i] = 0
            if _shanten(test, len(state.melds) + 1) <= sh14:
                consumed = [_from34(i)] * 4
                return {"type": "ankan", "actor": actor, "consumed": consumed}

    # 4. Riichi already accepted: tsumogiri
    if state.reach_accepted:
        return {"type": "dahai", "actor": actor, "pai": drawn, "tsumogiri": True}

    # 5. Riichi declaration
    if state.is_closed() and not state.reach_declared and state.left_tiles >= 4:
        best = None
        for i in range(34):
            if _is_dead_tile(i) or hand14[i] == 0:
                continue
            test = list(hand14)
            test[i] -= 1
            if _shanten(test, len(state.melds)) != 0:
                continue
            waits = _winning_tiles(test, len(state.melds))
            live = sum(_live_count(state, w) for w, _ in waits)
            value = 0.0
            value_plain = 0.0
            for w, _ in waits:
                value = max(value, _estimate_value(state, test, w, True))
                value_plain = max(value_plain, _estimate_value(state, test, w, False))
            if best is None or (live, value) > (best[0], best[1]):
                best = (live, value, i, value_plain)
        # Riichi whenever the wait is real: it guarantees a yaku, so a closed
        # tenpai must never be held damaten just because its value looks low.
        # Chasing someone else's riichi is the exception: replaying 37 sanma
        # games, this branch fired on 196 tenpai and the human kept damaten on
        # 98 of them, nearly all with an existing riichi and a narrow or cheap
        # wait. So a chase now needs a wide wait or a real payout.
        if best:
            n_riichi = sum(1 for x in state.opponent_riichi if x)
            ok = best[0] >= 2
            # A hand that already wins without riichi may also stay damaten.
            # Sanma pays a lot for a wide wait, so a narrow or cheap wait keeps
            # the option of improving the hand instead of freezing it.
            if best[3] > 0 and not (best[0] >= 4 or best[1] >= 5200.0):
                ok = False
            if n_riichi and not (best[0] >= 4 or best[1] >= 8000.0):
                ok = False
        else:
            ok = False
        if ok:
            tile = _from34(best[2])
            state.pending_reach = best[2]
            return {
                "type": "reach",
                "actor": actor,
                "pai": tile,
                "meta": {"show": {
                    "title": f"Calc 3P · 立直 · {best[0]}张待ち",
                    "items": [
                        {"label": "宣言后打", "pais": [tile], "value": tile,
                         "note": f"打点 {best[1]:.0f}"},
                    ],
                }},
            }

    # 6. Discard: shanten first, then EV (see _rank_scores).
    scores = [_score_discard(state, i, hand14) for i in range(34) if not _is_dead_tile(i) and hand14[i] > 0]

    if _opponent_riichi_active(state):
        # Fold hard when every aggressive line is negative EV.
        safe = [s for s in scores if _is_safe(state, s.idx)]
        best_any = _rank_scores(scores)[0]
        if best_any.ev < 0 and safe:
            pick = max(safe, key=lambda s: (s.shanten, s.live))
        else:
            pick = best_any
    else:
        pick = _rank_scores(scores)[0]

    # Diagnostic: warn when a reachable tenpai was passed over for a slower
    # hand. Akagi only surfaces stderr in bot.log, which is enough to audit.
    tenpai = [s for s in scores if s.shanten == 0]
    if tenpai and pick.shanten != 0:
        print(
            f"# passed tenpai: best_tenpai_ev={max(t.ev for t in tenpai):.0f} "
            f"(live={max(t.live for t in tenpai)}) picked={_from34(pick.idx)}"
            f" sh={pick.shanten} ev={pick.ev:.0f}",
            file=sys.stderr,
        )

    return _dahai_action(state, pick, hand14, drawn, scores)


def _pick_weights(cands: list["Score"], temp: float = 340.0) -> list[float]:
    """Softmax over EV: how strongly each candidate is preferred.

    A candidate that costs a shanten must not win on EV alone — the ranking
    already demotes it, so the weight has to as well, otherwise the panel shows
    57% next to a third-place tile and looks broken.
    """
    if not cands:
        return []
    best_sh = min(c.shanten for c in cands)
    adj = [c.ev - (c.shanten - best_sh) * 600.0 for c in cands]
    top = max(adj)
    ex = [math.exp((a - top) / temp) for a in adj]
    total = sum(ex)
    return [e / total if total > 0 else 1.0 / len(cands) for e in ex]


def _label_of(state: State, idx: int, hand14: list[int]) -> str:
    """Tile label, using the red-five notation when that is the copy we hold."""
    pai = _from34(idx)
    if idx in (4, 13, 22) and state.aka_in_hand.get("mps"[idx // 9], 0) > 0:
        if hand14[idx] - state.aka_in_hand.get("mps"[idx // 9], 0) == 0:
            pai = f"5{'mps'[idx // 9]}r"
    return pai


def _dahai_action(
    state: State, pick: Score, hand14: list[int], drawn: str, all_scores: list[Score] | None = None
) -> dict:
    actor = state.actor_id
    pai = _label_of(state, pick.idx, hand14)

    ranked = _rank_scores(all_scores or [pick])[:3]
    weights = _pick_weights(ranked)
    print(
        "# dahai pick=%s ev=%.1f top=%s hand=%s"
        % (
            pai,
            pick.ev,
            [(  # noqa: E501
                _from34(s.idx),
                round(s.ev, 1),
                s.shanten,
                s.live,
                round(s.win_p * 100, 1),
                round(s.danger * 100, 1),
            ) for s in ranked],
            "".join(_from34(i)[:2] * min(c, 4) for i, c in enumerate(hand14) if c),
        ),
        file=sys.stderr,
        flush=True,
    )
    items = []
    for i, (cand, w) in enumerate(zip(ranked, weights), 1):
        tile = _label_of(state, cand.idx, hand14)
        items.append(
            {
                "label": f"何切{i}",
                "pais": [tile],
                "value": f"{w * 100:.0f}%",
                "note": (
                    f"和 {cand.win_p * 100:.1f}% 铳 {cand.danger * 100:.1f}% "
                    f"进 {cand.live} 值 {cand.value:.0f}"
                ),
            }
        )
    items.append({"label": "期待", "value": f"{pick.value:.0f}"})

    return {
        "type": "dahai",
        "actor": actor,
        "pai": pai,
        "tsumogiri": pai == drawn,
        "meta": {"show": {"title": f"Calc 3P · {pick.shanten}向听", "items": items}},
    }


def decide_after_own_pon(state: State, last: dict) -> dict:
    """After calling pon we must discard at once, with no draw to replace it."""
    hand = list(state.hand34)
    scores = [
        _score_discard(state, i, hand) for i in range(34)
        if not _is_dead_tile(i) and hand[i] > 0
    ]
    if not scores:
        return {"type": "none"}
    pick = _rank_scores(scores)[0]
    return _dahai_action(state, pick, hand, "", scores)


def decide_after_others_dahai(state: State, last: dict) -> dict:
    actor = state.actor_id
    discarder = last["actor"]
    discarded = last["pai"]
    didx = _to34(discarded)
    own_river = {_to34(t) for t in state.rivers[actor]}

    # Ron
    hand14 = list(state.hand34)
    hand14[didx] += 1
    if didx not in own_river and _shanten(hand14, len(state.melds)) == -1:
        try:
            res = _hand_value(state, hand14, state.aka_in_hand, didx, False, state.nuki, state.reach_accepted)
        except Exception:
            res = None
        if _yaku_ok(res):
            return {"type": "hora", "actor": actor, "target": discarder, "pai": discarded}

    if state.reach_accepted:
        return {"type": "none"}

    current = _shanten(state.hand34, len(state.melds))
    decline = None

    # Daiminkan
    if state.hand34[didx] >= 3:
        post = list(state.hand34)
        post[didx] -= 3
        sh_post = _shanten(post, len(state.melds) + 1)
        if sh_post < current:
            consumed = [_from34(didx)] * 3
            return {"type": "daiminkan", "actor": actor, "target": discarder, "pai": discarded, "consumed": consumed}
        decline = ("杠", sh_post, True)

    # Pon — three-player has no chi, so this is the only call besides kan.
    if state.hand34[didx] >= 2:
        post = list(state.hand34)
        post[didx] -= 2
        best_after = None
        for j in range(34):
            if _is_dead_tile(j) or post[j] == 0:
                continue
            t = list(post)
            t[j] -= 1
            sh = _shanten(t, len(state.melds) + 1)
            if best_after is None or sh < best_after[0]:
                best_after = (sh, t)
        if best_after:
            hypothetical = [{
                "type": "pon",
                "tiles": [_from34(didx), _from34(didx), discarded],
                "from_seat": discarder,
                "called_tile": discarded,
            }]
            viable = _has_viable_yaku(state, best_after[1], hypothetical)
            if best_after[0] < current and viable:
                consumed = [_from34(didx), _from34(didx)]
                if didx in (13, 22) and state.aka_in_hand.get("mps"[didx // 9], 0) > 0:
                    consumed = [f"5{'mps'[didx // 9]}r", _from34(didx)]
                return {"type": "pon", "actor": actor, "target": discarder, "pai": discarded, "consumed": consumed}
            decline = ("碰", best_after[0], viable)

    if decline:
        kind, sh_post, viable = decline
        reason = "碰后无役" if not viable else "碰后不进张"
        if kind == "杠":
            reason = "杠后不进张"
        return {
            "type": "none",
            "meta": {"show": {
                "title": f"Calc 3P · 不{kind} · {reason}",
                "items": [
                    {"label": kind, "pais": [discarded] * (2 if kind == "碰" else 3),
                     "value": f"{sh_post}向听", "note": "无役" if not viable else "未推进"},
                    {"label": "不" + kind, "value": f"{current}向听", "note": "保持"},
                ],
            }},
        }
    return {"type": "none"}


def _has_viable_yaku(state: State, hand13: list[int], extra_melds: list[dict]) -> bool:
    all_melds = list(state.melds) + extra_melds
    sw = state.seat_wind() - EAST + 27
    rw = _to34(state.bakaze)

    def yakuhai(i: int) -> bool:
        return i >= 31 or i == sw or i == rw

    for m in all_melds:
        if yakuhai(_to34(m["tiles"][0])):
            return True
    for i in range(34):
        if hand13[i] >= 3 and yakuhai(i):
            return True
    all_simple = all(hand13[i] == 0 or not _is_yaochuu(i) for i in range(34))
    if all_simple:
        for m in all_melds:
            if any(_is_yaochuu(_to34(t)) for t in m["tiles"]):
                all_simple = False
                break
    # Sanma is kuitan-nashi, so tanyao is only a yaku for a closed hand.
    if all_simple and not all_melds:
        return True
    suits = [False, False, False]
    for i in range(27):
        if hand13[i] > 0:
            suits[i // 9] = True
    for m in all_melds:
        for t in m["tiles"]:
            i = _to34(t)
            if i < 27:
                suits[i // 9] = True
    if sum(suits) <= 1:
        return True
    # tenpai with any yaku via full scoring
    if _shanten(hand13, len(all_melds)) == 0:
        for w in range(34):
            if _is_dead_tile(w) or hand13[w] >= 4:
                continue
            full = list(hand13)
            full[w] += 1
            if _shanten(full, len(all_melds)) != -1:
                continue
            # pon-only melds are easy to reconstruct for scoring
            for tsumo in (False,):
                try:
                    res = _hand_value(state, full, state.aka_in_hand, w, tsumo, state.nuki, state.reach_accepted)
                except Exception:
                    res = None
                if _yaku_ok(res):
                    return True
    return False


def decide_after_others_kakan(state: State, last: dict) -> dict:
    actor = state.actor_id
    declarer = last["actor"]
    tile = last["pai"]
    tidx = _to34(tile)
    own_river = {_to34(t) for t in state.rivers[actor]}
    hand14 = list(state.hand34)
    hand14[tidx] += 1
    if tidx not in own_river and _shanten(hand14, len(state.melds)) == -1:
        try:
            res = _hand_value(state, hand14, state.aka_in_hand, tidx, False, state.nuki, state.reach_accepted)
        except Exception:
            res = None
        if _yaku_ok(res):
            return {"type": "hora", "actor": actor, "target": declarer, "pai": tile}
    return {"type": "none"}


# ---------------------------------------------------------------- loop

def decide_after_own_reach_echo(state: State) -> dict:
    """Second half of a riichi declaration: Akagi echoes the reach and expects
    the declaration discard. The first half already picked the tile."""
    actor = state.actor_id
    idx = state.pending_reach
    state.pending_reach = -1
    if idx < 0 or state.hand34[idx] <= 0:
        idx = _to34(state.last_self_tsumo) if state.last_self_tsumo else -1
    if idx < 0:
        # Never leave a declared reach unanswered. native_bot drops riichi from
        # the ranking when it cannot predict the discard; we already emitted the
        # reach, so fall back to any legal tile instead of stalling the game.
        for i in range(34):
            if state.hand34[i] > 0:
                idx = i
                break
    if idx < 0:
        return {"type": "none"}
    pai = _from34(idx)
    if idx in (13, 22) and state.aka_in_hand.get("mps"[idx // 9], 0) > 0:
        if state.hand34[idx] - state.aka_in_hand.get("mps"[idx // 9], 0) == 0:
            pai = f"5{'mps'[idx // 9]}r"
    state.reach_accepted = True
    return {"type": "dahai", "actor": actor, "pai": pai, "tsumogiri": pai == state.last_self_tsumo}


def react(state: State, line: str) -> str:
    events = json.loads(line)
    if not events:
        return '{"type":"none"}'
    for e in events:
        state.consume(e)
    last = events[-1]
    t = last.get("type")
    actor = state.actor_id
    if t == "tsumo" and last.get("actor") == actor:
        action = decide_after_own_tsumo(state, last)
    elif t == "reach" and last.get("actor") == actor:
        action = decide_after_own_reach_echo(state)
    elif t == "pon" and last.get("actor") == actor:
        action = decide_after_own_pon(state, last)
    elif t == "dahai" and last.get("actor") != actor:
        action = decide_after_others_dahai(state, last)
    elif t == "kakan" and last.get("actor") != actor:
        action = decide_after_others_kakan(state, last)
    else:
        action = {"type": "none"}
    return json.dumps(action, separators=(",", ":"))


def _initial_actor_id() -> int:
    if len(sys.argv) > 1:
        try:
            return int(sys.argv[1])
        except ValueError:
            pass
    env = os.environ.get("AKAGI_PLAYER_ID")
    if env is not None:
        try:
            return int(env)
        except ValueError:
            pass
    return 0


def main() -> None:
    # Akagi reads stdout as UTF-8 JSON; the Windows console default would
    # mangle the meta labels otherwise.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    state = State(actor_id=_initial_actor_id())
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            resp = react(state, line)
        except Exception as exc:  # never crash the loop
            sys.stderr.write(f"calc3p error: {exc}\n")
            sys.stderr.flush()
            resp = '{"type":"none"}'
        sys.stdout.write(resp + "\n")
        sys.stdout.flush()
        try:
            evs = json.loads(line)
        except Exception:
            continue
        if any(ev.get("type") == "end_game" for ev in evs):
            break


if __name__ == "__main__":
    main()

