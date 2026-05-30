"""買い目提案（純粋関数）。確率分布（と任意のオッズ）から推奨ベットを作る。

オッズあり:
    期待値 EV = prob * odds - 1。EV > EV_THRESHOLD の馬を単勝候補に。
    上位2頭の馬連も提案。Kelly 基準の軽量版 f = EV/(odds-1) * KELLY_FRACTION で配分。
オッズなし:
    上位3頭の複勝 + 上位2頭の馬連流しを均等配分で提案。

I/O は持たない。オッズの取得は呼び出し側（pipeline / scripts）が行い、dict で渡す。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.analysis.combiner import RaceProbabilities

# 単勝候補に採用する EV のしきい値（+10%）
EV_THRESHOLD = 0.1
# Kelly のかけ率（軽量版: 1/4 ケリー）
KELLY_FRACTION = 0.25


@dataclass
class BetRow:
    """1 つの買い目。"""

    bet_type: str          # "単勝" / "複勝" / "馬連"
    horses: list[int]      # 対象の馬番
    amount: int            # 推奨金額（円）
    ev_pct: float | None   # 期待値（%）。オッズ不明なら None
    reason: str            # 推奨理由


@dataclass
class BettingSuggestion:
    """1 レース分の買い目提案。"""

    has_odds: bool
    bankroll: int
    rows: list[BetRow] = field(default_factory=list)
    note: str = ""


def suggest_bets(
    probs: RaceProbabilities,
    odds: dict | None,      # horse_number(int) -> 単勝オッズ(float)
    bankroll: int = 1000,
) -> BettingSuggestion:
    """買い目を提案する。

    Args:
        probs: 確率分布（確率降順済み）。
        odds: 馬番 → 単勝オッズ。None または空ならオッズなしモード。
        bankroll: 1 レース予算（円）。

    Returns:
        BettingSuggestion。
    """
    horses = probs.horses
    if not horses or bankroll <= 0:
        return BettingSuggestion(
            has_odds=bool(odds), bankroll=max(0, bankroll), rows=[],
            note="出走馬がない、または予算が0のため提案なし。",
        )

    has_odds = bool(odds)
    if has_odds:
        return _suggest_with_odds(probs, odds, bankroll)
    return _suggest_without_odds(probs, bankroll)


def _suggest_with_odds(probs, odds, bankroll) -> BettingSuggestion:
    """オッズあり: EV>閾値の単勝（1/4ケリー配分）＋上位2頭の馬連。"""
    rows: list[BetRow] = []

    # --- 単勝候補（EV>閾値）---
    candidates = []
    for h in probs.horses:
        o = odds.get(h.horse_number)
        if o is None or o <= 1.0:
            continue
        ev = h.win_probability * o - 1.0
        if ev > EV_THRESHOLD:
            kelly_f = (ev / (o - 1.0)) * KELLY_FRACTION
            candidates.append((h, o, ev, max(0.0, kelly_f)))

    # ケリー比率で予算配分（合計が予算を超えないよう正規化）
    f_sum = sum(c[3] for c in candidates)
    for h, o, ev, kelly_f in candidates:
        if f_sum <= 0:
            break
        amount = int(bankroll * (kelly_f / max(f_sum, kelly_f)) / 100) * 100  # 100円単位
        amount = max(100, amount)
        rows.append(BetRow(
            bet_type="単勝", horses=[h.horse_number], amount=amount,
            ev_pct=round(ev * 100, 1),
            reason=f"勝率{h.win_pct:.1f}% × オッズ{o:.1f} → EV +{ev*100:.0f}%（1/4ケリー）",
        ))

    # --- 上位2頭の馬連 ---
    if len(probs.horses) >= 2:
        a, b = probs.horses[0], probs.horses[1]
        rows.append(BetRow(
            bet_type="馬連", horses=sorted([a.horse_number, b.horse_number]),
            amount=100,
            ev_pct=None,
            reason=f"上位2頭（{a.horse_number}・{b.horse_number}）の組み合わせ",
        ))

    note = "" if rows else "EV>+10% の馬がなく、妙味のある単勝はありませんでした。"
    if not any(r.bet_type == "単勝" for r in rows):
        note = "EV>+10% の単勝候補なし（馬連のみ提案）。" + note
    return BettingSuggestion(has_odds=True, bankroll=bankroll, rows=rows, note=note)


def _suggest_without_odds(probs, bankroll) -> BettingSuggestion:
    """オッズなし: 上位3頭の複勝＋上位2頭の馬連を均等配分。"""
    rows: list[BetRow] = []
    top = probs.horses[:3]

    # 予算を「複勝3点 + 馬連1点」に均等割り（100円単位）
    n_bets = len(top) + (1 if len(probs.horses) >= 2 else 0)
    per = max(100, int(bankroll / max(1, n_bets) / 100) * 100)

    for h in top:
        rows.append(BetRow(
            bet_type="複勝", horses=[h.horse_number], amount=per, ev_pct=None,
            reason=f"勝率{h.win_pct:.1f}%（上位）→ 複勝で堅実に",
        ))
    if len(probs.horses) >= 2:
        a, b = probs.horses[0], probs.horses[1]
        rows.append(BetRow(
            bet_type="馬連", horses=sorted([a.horse_number, b.horse_number]),
            amount=per, ev_pct=None,
            reason=f"上位2頭（{a.horse_number}・{b.horse_number}）の馬連",
        ))

    return BettingSuggestion(
        has_odds=False, bankroll=bankroll, rows=rows,
        note="オッズ未取得のため、確率ベースの均等配分で提案しています。",
    )
