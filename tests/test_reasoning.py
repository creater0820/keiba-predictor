"""src/analysis/reasoning.py の文言生成テスト（純粋関数・ダミー breakdown）。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.analysis.pedigree_score import (  # noqa: E402
    PedigreeScoreInput,
    SireStat,
    score_pedigree,
)
from src.analysis.reasoning import (  # noqa: E402
    reason_pedigree,
    reason_running_style,
    reason_track_bias,
)
from src.analysis.running_style_score import (  # noqa: E402
    RunningStyleScoreInput,
    score_running_style,
)
from src.analysis.track_bias_score import (  # noqa: E402
    TrackBiasScoreInput,
    score_track_bias,
)


def _tb(**kw):
    return score_track_bias(TrackBiasScoreInput(**kw))[2]


def _ped(**kw):
    return score_pedigree(PedigreeScoreInput(**kw))[2]


def _rs(**kw):
    return score_running_style(RunningStyleScoreInput(**kw))[2]


# ===================== トラックバイアス（5パターン）=====================
def test_tb_inside_front_advantage():
    bd = _tb(post_position=2, running_style="先行", inside_outside_bias=-0.33,
             pace_bias=-0.33, bias_n_races=6, bias_data_date="20260530", bias_venue="東京")
    txt = reason_track_bias(bd)
    assert "内枠" in txt and "前残り" in txt
    assert "5/30" in txt and "東京" in txt
    assert "内外-0.33" in txt  # 数値が文中にある


def test_tb_outside_closer_advantage():
    bd = _tb(post_position=8, running_style="追込", inside_outside_bias=0.4,
             pace_bias=0.4, bias_n_races=6)
    txt = reason_track_bias(bd)
    assert "外枠" in txt
    assert "差し" in txt or "末脚" in txt


def test_tb_misaligned_is_unfavorable():
    bd = _tb(post_position=1, running_style="逃げ", inside_outside_bias=0.5,
             pace_bias=0.5, bias_n_races=6)
    txt = reason_track_bias(bd)
    assert "不利" in txt


def test_tb_no_data_is_neutral():
    bd = _tb(post_position=4, running_style="先行", inside_outside_bias=0.0,
             pace_bias=0.0, bias_n_races=0)
    assert "データ不足" in reason_track_bias(bd)
    assert "中立" in reason_track_bias(bd)


def test_tb_pace_value_two_decimals():
    bd = _tb(post_position=3, running_style="差し", inside_outside_bias=-0.2,
             pace_bias=0.25, bias_n_races=5)
    txt = reason_track_bias(bd)
    # ペース値が小数2桁（+0.25 等）
    assert re.search(r"ペース[+\-]\d\.\d{2}", txt)


# ===================== 血統（6パターン）=====================
def test_ped_strong_both():
    bd = _ped(sire=SireStat(0.24, 128), dam_sire=SireStat(0.18, 92),
              global_avg_win_rate=0.10, sire_name="ディープインパクト",
              dam_sire_name="サンデーサイレンス", distance_m=2400,
              context={"surface": "turf", "distance_bucket": "-2600"})
    txt = reason_pedigree(bd)
    assert "ディープインパクト" in txt and "サンデーサイレンス" in txt
    assert "24%" in txt and "n=128" in txt   # 勝率%（整数）+ サンプル n=
    assert "芝2400m" in txt


def test_ped_win_rate_is_integer_percent():
    bd = _ped(sire=SireStat(0.123, 200), dam_sire=None, global_avg_win_rate=0.08,
              sire_name="X", distance_m=1600, context={"surface": "dirt"})
    txt = reason_pedigree(bd)
    # 12% のように整数%（小数を含まない % 表記）
    assert re.search(r"勝率\d+%", txt)
    assert "ダート" in txt


def test_ped_small_sample_bayes_noted():
    bd = _ped(sire=SireStat(0.30, 5), dam_sire=None, global_avg_win_rate=0.08,
              sire_name="少標本父", distance_m=2000, context={"surface": "turf"})
    txt = reason_pedigree(bd)
    assert "ベイズ補正" in txt


def test_ped_no_data():
    bd = _ped(sire=None, dam_sire=None, global_avg_win_rate=0.08)
    assert "中立評価" in reason_pedigree(bd)


def test_ped_first_surface_penalty_noted():
    bd = _ped(sire=SireStat(0.1, 200), dam_sire=SireStat(0.09, 100),
              global_avg_win_rate=0.08, is_first_surface=True, distance_m=1800,
              sire_name="父", dam_sire_name="母父", context={"surface": "dirt"})
    assert "初の馬場" in reason_pedigree(bd)


def test_ped_sample_size_is_integer():
    bd = _ped(sire=SireStat(0.15, 77), dam_sire=None, global_avg_win_rate=0.08,
              sire_name="父", distance_m=1400, context={"surface": "turf"})
    txt = reason_pedigree(bd)
    m = re.search(r"n=(\d+)", txt)
    assert m and m.group(1) == "77"


# ===================== 脚質（6パターン）=====================
def _field_hi():
    return ["逃げ", "逃げ", "先行", "先行", "差し", "差し", "追込", "先行"]


def _field_slow():
    return ["差し", "差し", "追込", "追込", "差し", "先行", "逃げ", "差し"]


def test_rs_closer_in_hi_pace_favorable():
    bd = _rs(running_style="追込", post_position=2, field_styles=_field_hi(),
             style_n_races=6, avg_position_ratio=0.85)
    txt = reason_running_style(bd)
    assert "追込" in txt and "ハイ" in txt
    assert "有利" in txt
    assert "0.85" in txt and "85%" in txt   # 位置率と補足


def test_rs_position_ratio_has_supplement():
    bd = _rs(running_style="先行", post_position=3, field_styles=_field_hi(),
             style_n_races=5, avg_position_ratio=0.35)
    txt = reason_running_style(bd)
    # 専門用語に括弧で補足（先頭から35%地点）
    assert "先頭から35%地点" in txt


def test_rs_outside_escaper_penalty():
    bd = _rs(running_style="逃げ", post_position=8, field_styles=_field_hi(),
             style_n_races=4, avg_position_ratio=0.08)
    txt = reason_running_style(bd)
    assert "枠順減点" in txt


def test_rs_unknown_style():
    bd = _rs(running_style="不明", post_position=5, field_styles=_field_hi())
    assert "推定できない" in reason_running_style(bd)


def test_rs_pace_label_present():
    bd = _rs(running_style="逃げ", post_position=3, field_styles=_field_slow(),
             style_n_races=5, avg_position_ratio=0.1)
    txt = reason_running_style(bd)
    assert "想定" in txt and "ペース" in txt


def test_rs_no_avg_ratio_falls_back():
    # avg 無し（キャッシュ命中相当）でも n から推定文言が出る
    bd = _rs(running_style="差し", post_position=4, field_styles=_field_hi(),
             style_n_races=7, avg_position_ratio=None)
    txt = reason_running_style(bd)
    assert "直近7走から推定" in txt


# ===================== 防御性 =====================
def test_all_handle_empty_breakdown():
    """空 dict でも例外を出さず文字列を返すこと。"""
    assert isinstance(reason_track_bias({}), str)
    assert isinstance(reason_pedigree({}), str)
    assert isinstance(reason_running_style({}), str)
