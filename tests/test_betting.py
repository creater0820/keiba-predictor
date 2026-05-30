"""betting/suggester.py のユニットテスト（ダミーデータ・I/O なし）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.analysis.combiner import (  # noqa: E402
    CombineWeights,
    HorseScores,
    combine_race,
)
from src.betting.suggester import EV_THRESHOLD, suggest_bets  # noqa: E402

EQUAL_W = CombineWeights(1 / 3, 1 / 3, 1 / 3)


def _race_from_probs(prob_map: dict[int, float]):
    """馬番→勝率 を与えて RaceProbabilities を直接組む（スコア経由せず）。"""
    # score=log(p) なら softmax(t=1) が厳密に p を再現する
    import math

    horses = []
    for num, p in prob_map.items():
        s = math.log(max(p, 1e-9))
        horses.append(HorseScores(num, f"H{num}", (s, 1.0), (s, 1.0), (s, 1.0)))
    return combine_race("R", horses, EQUAL_W, 1.0)


def test_with_odds_positive_ev_recommended():
    """EV>+10% の馬が単勝候補に入ること。"""
    race = _race_from_probs({1: 0.5, 2: 0.3, 3: 0.2})
    # 馬1: prob0.5, odds3.0 → EV=0.5。候補になる。
    odds = {1: 3.0, 2: 2.0, 3: 2.0}
    s = suggest_bets(race, odds, bankroll=1000)
    assert s.has_odds is True
    tans = [r for r in s.rows if r.bet_type == "単勝"]
    assert any(1 in r.horses for r in tans)
    assert all(r.ev_pct > EV_THRESHOLD * 100 for r in tans)


def test_with_odds_no_positive_ev():
    """どの馬も EV<=閾値なら単勝提案なし（馬連は出る）。"""
    race = _race_from_probs({1: 0.5, 2: 0.3, 3: 0.2})
    odds = {1: 1.5, 2: 1.2, 3: 1.1}  # 全て妙味なし
    s = suggest_bets(race, odds, bankroll=1000)
    assert not any(r.bet_type == "単勝" for r in s.rows)
    assert any(r.bet_type == "馬連" for r in s.rows)


def test_without_odds_uses_place_and_quinella():
    """オッズなしは上位3頭の複勝＋上位2頭の馬連。"""
    race = _race_from_probs({1: 0.4, 2: 0.3, 3: 0.2, 4: 0.1})
    s = suggest_bets(race, odds=None, bankroll=1200)
    assert s.has_odds is False
    place = [r for r in s.rows if r.bet_type == "複勝"]
    quinella = [r for r in s.rows if r.bet_type == "馬連"]
    assert len(place) == 3
    assert len(quinella) == 1


def test_zero_bankroll_returns_no_bets():
    """予算0なら提案なし。"""
    race = _race_from_probs({1: 0.6, 2: 0.4})
    s = suggest_bets(race, odds={1: 2.0, 2: 3.0}, bankroll=0)
    assert s.rows == []


def test_empty_field_returns_no_bets():
    """出走馬なしなら提案なし。"""
    race = combine_race("R", [], EQUAL_W, 1.0)
    s = suggest_bets(race, odds=None, bankroll=1000)
    assert s.rows == []


def test_all_odds_missing_falls_back_to_no_odds_mode():
    """odds が空 dict ならオッズなしモードにフォールバック。"""
    race = _race_from_probs({1: 0.5, 2: 0.3, 3: 0.2})
    s = suggest_bets(race, odds={}, bankroll=900)
    assert s.has_odds is False
    assert any(r.bet_type == "複勝" for r in s.rows)


def test_extreme_probability_one_horse_dominant():
    """1頭が99%でも、単勝EVが出れば候補・金額は100円単位で正。"""
    race = _race_from_probs({1: 0.99, 2: 0.005, 3: 0.005})
    odds = {1: 1.5, 2: 50.0, 3: 50.0}  # 馬1: EV=0.99*1.5-1=0.485
    s = suggest_bets(race, odds, bankroll=1000)
    tans = [r for r in s.rows if r.bet_type == "単勝"]
    assert any(1 in r.horses for r in tans)
    assert all(r.amount % 100 == 0 and r.amount >= 100 for r in s.rows)


def test_amounts_are_100_yen_units():
    """全提案の金額が100円単位であること。"""
    race = _race_from_probs({1: 0.4, 2: 0.35, 3: 0.25})
    s = suggest_bets(race, odds=None, bankroll=1000)
    assert all(r.amount % 100 == 0 for r in s.rows)
