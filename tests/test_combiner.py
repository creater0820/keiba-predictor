"""combiner.py（スコア合成 → 確率分布）のユニットテスト。

すべてダミーデータで完結（I/O なし）。エッジケースを重点的に検証する。

実行::

    pytest -v tests/test_combiner.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.analysis.combiner import (  # noqa: E402
    CombineWeights,
    HorseScores,
    combine_race,
)


def _hs(num, name, s_bias, s_ped, s_style, c=1.0):
    """テスト用 HorseScores 生成（信頼度は一律 c）。"""
    return HorseScores(
        horse_number=num, horse_name=name,
        track_bias=(s_bias, c), pedigree=(s_ped, c), running_style=(s_style, c),
    )


EQUAL_W = CombineWeights(1 / 3, 1 / 3, 1 / 3)


# ===================== 基本動作 =====================
def test_probabilities_sum_to_one():
    """全馬の勝率合計が 1.0 になること。"""
    horses = [_hs(1, "A", 70, 60, 50), _hs(2, "B", 40, 55, 65), _hs(3, "C", 50, 50, 50)]
    race = combine_race("R", horses, EQUAL_W, temperature=1.0)
    assert abs(sum(h.win_probability for h in race.horses) - 1.0) < 1e-9


def test_higher_score_higher_probability():
    """合成スコアが高い馬ほど勝率が高く、降順ソートされること。"""
    horses = [_hs(1, "Low", 30, 30, 30), _hs(2, "High", 90, 90, 90), _hs(3, "Mid", 50, 50, 50)]
    race = combine_race("R", horses, EQUAL_W, temperature=1.0)
    # 先頭が最高勝率
    assert race.horses[0].horse_name == "High"
    assert race.horses[0].win_probability > race.horses[-1].win_probability


def test_weight_normalization_not_summing_to_one():
    """重みが合計 1 でなくても、正規化されて結果が変わらないこと。"""
    horses = [_hs(1, "A", 80, 40, 60), _hs(2, "B", 40, 80, 50)]
    r1 = combine_race("R", horses, CombineWeights(1, 1, 1), 1.0)       # 合計3
    r2 = combine_race("R", horses, CombineWeights(10, 10, 10), 1.0)    # 合計30
    p1 = {h.horse_number: h.win_probability for h in r1.horses}
    p2 = {h.horse_number: h.win_probability for h in r2.horses}
    assert abs(p1[1] - p2[1]) < 1e-9
    # 正規化済み重みは 1/3 ずつ
    assert abs(r1.weights.track_bias - 1 / 3) < 1e-9


def test_weighting_changes_result():
    """重みを変えると確率分布が変わること。"""
    horses = [_hs(1, "BiasHorse", 90, 30, 30), _hs(2, "PedHorse", 30, 90, 30)]
    bias_heavy = combine_race("R", horses, CombineWeights(0.8, 0.1, 0.1), 1.0)
    ped_heavy = combine_race("R", horses, CombineWeights(0.1, 0.8, 0.1), 1.0)
    # バイアス重視なら BiasHorse、血統重視なら PedHorse が上位
    assert bias_heavy.horses[0].horse_name == "BiasHorse"
    assert ped_heavy.horses[0].horse_name == "PedHorse"


# ===================== 総合信頼度 =====================
def test_confidence_is_weighted_average():
    """総合信頼度が component confidence の重み付き加重平均であること。"""
    h = HorseScores(1, "A", track_bias=(50, 0.9), pedigree=(50, 0.6), running_style=(50, 0.3))
    race = combine_race("R", [h], CombineWeights(0.5, 0.3, 0.2), 1.0)
    # 0.5*0.9 + 0.3*0.6 + 0.2*0.3 = 0.69
    assert abs(race.horses[0].confidence - 0.69) < 1e-9


def test_low_confidence_does_not_dampen_probability():
    """信頼度が低くても確率は final_score のみで決まる（二重補正しない）。"""
    # 2 頭が完全に同じスコア。片方だけ信頼度が低い。
    h_hi = HorseScores(1, "HiConf", (70, 1.0), (70, 1.0), (70, 1.0))
    h_lo = HorseScores(2, "LoConf", (70, 0.0), (70, 0.0), (70, 0.0))
    race = combine_race("R", [h_hi, h_lo], EQUAL_W, 1.0)
    probs = {h.horse_name: h.win_probability for h in race.horses}
    # スコア同一 → 確率は均等（信頼度差は確率に影響しない）
    assert abs(probs["HiConf"] - probs["LoConf"]) < 1e-9


# ===================== エッジケース =====================
def test_all_same_score_is_uniform():
    """全馬同スコア → 均等分布になること。"""
    horses = [_hs(i, f"H{i}", 50, 50, 50) for i in range(1, 6)]
    race = combine_race("R", horses, EQUAL_W, 1.0)
    for h in race.horses:
        assert abs(h.win_probability - 1 / 5) < 1e-9


def test_single_horse_probability_one():
    """1 頭のみ → 勝率 1.0。"""
    race = combine_race("R", [_hs(1, "Solo", 80, 20, 50)], EQUAL_W, 1.0)
    assert len(race.horses) == 1
    assert abs(race.horses[0].win_probability - 1.0) < 1e-9


def test_empty_field():
    """出走馬ゼロ → 空の結果。"""
    race = combine_race("R", [], EQUAL_W, 1.0)
    assert race.horses == []


def test_low_temperature_sharpens():
    """温度が低いほど確率が尖る（最上位の確率が上がる）こと。"""
    horses = [_hs(1, "A", 80, 80, 80), _hs(2, "B", 60, 60, 60), _hs(3, "C", 40, 40, 40)]
    sharp = combine_race("R", horses, EQUAL_W, temperature=0.3)
    flat = combine_race("R", horses, EQUAL_W, temperature=3.0)
    assert sharp.horses[0].win_probability > flat.horses[0].win_probability


def test_extreme_temperatures_guarded():
    """温度 0 や負値でも 0.1 にガードされ、確率が正常（合計1・NaN なし）。"""
    horses = [_hs(1, "A", 90, 90, 90), _hs(2, "B", 10, 10, 10)]
    for t in (0.0, -5.0, 1e-9):
        race = combine_race("R", horses, EQUAL_W, temperature=t)
        assert race.temperature == 0.1  # 0.1 にガードされる
        total = sum(h.win_probability for h in race.horses)
        assert abs(total - 1.0) < 1e-9
        assert all(0.0 <= h.win_probability <= 1.0 for h in race.horses)


def test_high_temperature_approaches_uniform():
    """温度が非常に大きいと均等分布に近づくこと。"""
    horses = [_hs(1, "A", 90, 90, 90), _hs(2, "B", 10, 10, 10)]
    race = combine_race("R", horses, EQUAL_W, temperature=1000.0)
    for h in race.horses:
        assert abs(h.win_probability - 0.5) < 0.03  # ほぼ均等に近づく


def test_zero_weights_fallback_to_uniform_weights():
    """重み全ゼロ → 均等重み(1/3)にフォールバックすること。"""
    horses = [_hs(1, "A", 80, 40, 60), _hs(2, "B", 40, 80, 50)]
    race = combine_race("R", horses, CombineWeights(0, 0, 0), 1.0)
    assert abs(race.weights.track_bias - 1 / 3) < 1e-9
    assert abs(race.weights.pedigree - 1 / 3) < 1e-9
    assert abs(race.weights.running_style - 1 / 3) < 1e-9
    # 確率は正常
    assert abs(sum(h.win_probability for h in race.horses) - 1.0) < 1e-9


# ===================== 出力形式 =====================
def test_breakdown_is_dataframe_friendly():
    """breakdown が平坦な dict で、to_rows() が list[dict] を返すこと。"""
    horses = [_hs(1, "A", 70, 60, 50), _hs(2, "B", 40, 55, 65)]
    race = combine_race("R", horses, EQUAL_W, 1.0)
    rows = race.to_rows()
    assert isinstance(rows, list) and isinstance(rows[0], dict)
    # 3 スコアの内訳キーが含まれる
    for key in ("track_bias_score", "pedigree_score", "running_style_score",
                "final_score", "win_pct", "confidence"):
        assert key in rows[0]
    # 値はすべてスカラー（DataFrame 化しやすい）
    assert all(not isinstance(v, (list, dict)) for v in rows[0].values())


def test_win_pct_helper():
    """win_pct が win_probability*100 と一致すること。"""
    race = combine_race("R", [_hs(1, "A", 80, 80, 80), _hs(2, "B", 20, 20, 20)], EQUAL_W, 1.0)
    h = race.horses[0]
    assert abs(h.win_pct - h.win_probability * 100) < 0.01
