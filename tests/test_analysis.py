"""analysis 3 モジュール（純粋スコア関数）のユニットテスト。

すべてダミーデータで完結し、ネットワーク・DB・ファイルに触れない。

実行::

    pytest -v tests/test_analysis.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.analysis.pedigree_score import (  # noqa: E402
    PedigreeScoreInput,
    SireStat,
    score_pedigree,
)
from src.analysis.running_style_score import (  # noqa: E402
    RunningStyleScoreInput,
    score_running_style,
)
from src.analysis.track_bias_score import (  # noqa: E402
    TrackBiasScoreInput,
    score_track_bias,
)


# ===================== トラックバイアス =====================
def test_track_bias_alignment_increases_score():
    """バイアスと一致（外有利×差し馬を外枠）でスコアが 50 を超えること。"""
    score, conf, bd = score_track_bias(TrackBiasScoreInput(
        post_position=8, running_style="差し",
        inside_outside_bias=0.5, pace_bias=0.5, bias_n_races=6,
    ))
    assert score > 50
    assert conf == 1.0
    # breakdown は base + 各 component = score の形
    assert abs(bd["base"] + bd["frame_component"] + bd["pace_component"] - score) < 0.05


def test_track_bias_misalignment_decreases_score():
    """バイアスと逆（外有利なのに内枠の逃げ＋差し有利なのに逃げ）で 50 未満。"""
    score, _, _ = score_track_bias(TrackBiasScoreInput(
        post_position=1, running_style="逃げ",
        inside_outside_bias=0.5, pace_bias=0.5, bias_n_races=6,
    ))
    assert score < 50


def test_track_bias_neutral_returns_50_zero_conf():
    """中立バイアス（データ不足）は 50・confidence 0。"""
    score, conf, _ = score_track_bias(TrackBiasScoreInput(
        post_position=4, running_style="先行",
        inside_outside_bias=0.0, pace_bias=0.0, bias_n_races=0,
    ))
    assert score == 50.0
    assert conf == 0.0


def test_track_bias_unknown_style_lowers_confidence():
    """脚質不明でも frame は使えるが confidence は下がること。"""
    _, conf_known, _ = score_track_bias(TrackBiasScoreInput(
        2, "先行", 0.3, -0.3, bias_n_races=6))
    _, conf_unknown, _ = score_track_bias(TrackBiasScoreInput(
        2, "不明", 0.3, -0.3, bias_n_races=6))
    assert conf_unknown < conf_known


# ===================== 血統 =====================
def test_pedigree_strong_sire_scores_high():
    """好成績の父（十分な標本）で 50 を大きく超え、confidence 高。"""
    score, conf, bd = score_pedigree(PedigreeScoreInput(
        sire=SireStat(0.12, 800), dam_sire=SireStat(0.08, 300),
        global_avg_win_rate=0.075,
    ))
    assert score > 55
    assert conf == 1.0
    assert bd["blended_win_rate"] > bd["global_avg"]


def test_pedigree_small_sample_shrinks_to_neutral():
    """標本不足（<30）の高勝率はベイズで中立寄り・低 confidence になること。"""
    score, conf, bd = score_pedigree(PedigreeScoreInput(
        sire=SireStat(0.20, 5), dam_sire=None,
        global_avg_win_rate=0.075,
    ))
    # 生勝率 20% でも調整後は全体平均(7.5%)寄り
    assert bd["sire_adj"] < 0.11
    assert score < 60          # 中立(50)寄り
    assert conf < 0.2          # 低信頼


def test_pedigree_none_returns_neutral():
    """父・母父とも未取得（None）なら 50・confidence 0。"""
    score, conf, _ = score_pedigree(PedigreeScoreInput(
        sire=None, dam_sire=None, global_avg_win_rate=0.075,
    ))
    assert score == 50.0
    assert conf == 0.0


def test_pedigree_penalty_applied():
    """初ダート＋大幅距離変更のペナルティで減点されること。"""
    base_score, _, _ = score_pedigree(PedigreeScoreInput(
        sire=SireStat(0.10, 400), dam_sire=SireStat(0.09, 200),
        global_avg_win_rate=0.075,
    ))
    pen_score, _, bd = score_pedigree(PedigreeScoreInput(
        sire=SireStat(0.10, 400), dam_sire=SireStat(0.09, 200),
        global_avg_win_rate=0.075,
        is_first_surface=True, distance_change_m=500,
    ))
    assert pen_score < base_score
    assert bd["penalty"] == -13.0  # 8 + 5


# ===================== 脚質 =====================
def test_running_style_closer_favored_in_high_pace():
    """ハイペース想定（逃げ・先行多数）で追込馬が高得点。"""
    field = ["逃げ", "逃げ", "先行", "先行", "先行", "差し", "差し", "追込"]
    score, conf, bd = score_running_style(RunningStyleScoreInput(
        running_style="追込", post_position=2, field_styles=field,
    ))
    assert score > 50
    assert bd["pace_signed"] > 0  # ハイペース判定
    assert conf == 1.0


def test_running_style_outside_escaper_penalized():
    """大外の逃げ馬は枠ペナルティで減点されること。"""
    field = ["逃げ", "逃げ", "先行", "先行", "先行", "差し", "差し", "追込"]
    score, _, bd = score_running_style(RunningStyleScoreInput(
        running_style="逃げ", post_position=8, field_styles=field,
    ))
    assert bd["frame_penalty"] == -10.0
    assert score < 50


def test_running_style_unknown_returns_50_zero_conf():
    """脚質不明は 50・confidence 0。"""
    field = ["逃げ", "先行", "差し", "追込"]
    score, conf, _ = score_running_style(RunningStyleScoreInput(
        running_style="不明", post_position=5, field_styles=field,
    ))
    assert score == 50.0
    assert conf == 0.0


def test_running_style_breakdown_sums_to_score():
    """breakdown の base+fit+frame = score。"""
    field = ["逃げ", "先行", "差し", "差し", "追込", "差し"]
    score, _, bd = score_running_style(RunningStyleScoreInput(
        running_style="差し", post_position=3, field_styles=field,
    ))
    assert abs(bd["base"] + bd["fit_component"] + bd["frame_penalty"] - score) < 0.05
