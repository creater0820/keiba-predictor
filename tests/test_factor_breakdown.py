"""factor_breakdown.py の smoke テスト + combiner の details 伝播確認。"""

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
from src.analysis.pedigree_score import PedigreeScoreInput, SireStat, score_pedigree
from src.analysis.running_style_score import RunningStyleScoreInput, score_running_style
from src.analysis.track_bias_score import TrackBiasScoreInput, score_track_bias
from src.ui import factor_breakdown


def _make_result():
    """details 付きの RaceProbabilities をスコア関数経由で組み立てる。"""
    field = ["逃げ", "先行", "差し", "差し"]
    horse_scores = []
    for i in range(1, 4):
        tb = score_track_bias(TrackBiasScoreInput(
            post_position=i, running_style="先行", inside_outside_bias=-0.3,
            pace_bias=-0.3, bias_n_races=6, bias_data_date="20260531", bias_venue="東京"))
        ped = score_pedigree(PedigreeScoreInput(
            sire=SireStat(0.12, 100), dam_sire=SireStat(0.09, 40),
            global_avg_win_rate=0.08, sire_name="父", dam_sire_name="母父",
            distance_m=2400, context={"surface": "turf", "distance_bucket": "-2600"}))
        rs = score_running_style(RunningStyleScoreInput(
            running_style="先行", post_position=i, field_styles=field,
            style_n_races=5, avg_position_ratio=0.3))
        horse_scores.append(HorseScores(
            i, f"馬{i}", (tb[0], tb[1]), (ped[0], ped[1]), (rs[0], rs[1]),
            track_bias_detail=tb[2], pedigree_detail=ped[2], running_style_detail=rs[2]))
    return combine_race("R", horse_scores, CombineWeights(0.34, 0.33, 0.33), 1.0)


def test_details_flow_through_combiner():
    """combine_race が各馬に 3 要素の details を載せること。"""
    result = _make_result()
    h = result.horses[0]
    assert set(h.details.keys()) == {"track_bias", "pedigree", "running_style"}
    # 文言生成に必要なキーが含まれる
    assert "sire_name" in h.details["pedigree"]
    assert "matched_advantage" in h.details["track_bias"]
    assert "estimated_pace" in h.details["running_style"]
    # breakdown はフラットなまま（DataFrame 化可能）
    assert all(not isinstance(v, (list, dict)) for v in h.breakdown.values())


def test_render_horse_reasoning_no_exception():
    """render_horse_reasoning が例外を出さずに呼べること（smoke）。"""
    result = _make_result()
    # Streamlit ランタイム外でも例外を投げないこと（警告は許容）
    factor_breakdown.render_horse_reasoning(result.horses)


def test_render_factor_breakdown_no_exception():
    """render_factor_breakdown（チャート＋カード）が例外なく呼べること（smoke）。"""
    result = _make_result()
    factor_breakdown.render_factor_breakdown(result)


def test_factor_block_handles_missing_details():
    """details が空でも reasoning が安全文言を返し、描画が落ちないこと。"""
    result = _make_result()
    for h in result.horses:
        h.details = {}  # details を消す
    factor_breakdown.render_horse_reasoning(result.horses)


def _make_result_n(n: int):
    """n 頭の RaceProbabilities（スコア差で順位が決まる）。"""
    field = ["逃げ", "先行", "差し", "差し"]
    hs = []
    for i in range(1, n + 1):
        s = 90 - i  # 馬1 が最高 → 順位1
        tb = score_track_bias(TrackBiasScoreInput(
            post_position=(i % 8) + 1, running_style="先行", inside_outside_bias=-0.3,
            pace_bias=-0.3, bias_n_races=6, bias_data_date="20260531", bias_venue="東京"))
        ped = score_pedigree(PedigreeScoreInput(
            sire=SireStat(0.12, 100), dam_sire=None, global_avg_win_rate=0.08,
            sire_name="父", distance_m=2400, context={"surface": "turf"}))
        rs = score_running_style(RunningStyleScoreInput(
            running_style="先行", post_position=(i % 8) + 1, field_styles=field,
            style_n_races=5, avg_position_ratio=0.3))
        # 順位を作るため track_bias スコアを i で変える
        hs.append(HorseScores(
            i, f"馬{i}", (s, 1.0), (ped[0], ped[1]), (rs[0], rs[1]),
            track_bias_detail=tb[2], pedigree_detail=ped[2], running_style_detail=rs[2]))
    return combine_race("R", hs, CombineWeights(0.34, 0.33, 0.33), 1.0)


def test_all_horses_shown_top3_expanded(monkeypatch):
    """18頭全て表示され、上位3頭は展開・4位以下は折りたたみであること。"""
    result = _make_result_n(18)
    calls = []

    class _Ctx:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_expander(label, expanded=False):
        calls.append((label, expanded))
        return _Ctx()

    monkeypatch.setattr(factor_breakdown.st, "expander", fake_expander)
    factor_breakdown.render_horse_reasoning(result.horses)

    assert len(calls) == 18  # 全頭分の expander
    expanded_flags = [exp for _, exp in calls]
    assert expanded_flags[:3] == [True, True, True]      # 上位3頭は展開
    assert all(exp is False for exp in expanded_flags[3:])  # 4位以下は折りたたみ


def test_render_factor_breakdown_full_field_no_exception():
    """18頭でも render_factor_breakdown が例外なく動くこと（smoke）。"""
    result = _make_result_n(18)
    factor_breakdown.render_factor_breakdown(result)
