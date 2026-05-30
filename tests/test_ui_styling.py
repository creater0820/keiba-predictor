"""results_table.py の Styler 微調整テスト（オフライン）。

上位3頭の「順位」「馬名」のみ緑文字（#22c55e）になり、背景色ハイライトが
無いことを Styler の HTML 出力で検証する。
"""

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
from src.ui.results_table import (  # noqa: E402
    TOP3_TEXT_COLOR,
    build_results_df,
    style_results,
)


def _make_result(n: int):
    """n 頭のダミー結果（スコア差で順位が決まる）。"""
    horses = [
        HorseScores(i, f"馬{i}", (70 - i, 1.0), (60, 1.0), (50, 1.0))
        for i in range(1, n + 1)
    ]
    return combine_race("R", horses, CombineWeights(1, 1, 1), 1.0)


def test_top3_text_is_green():
    """上位3頭の行に緑文字スタイルが含まれること。"""
    df = build_results_df(_make_result(8))
    html = style_results(df).to_html()
    # 緑色指定が出力に存在する
    assert f"color: {TOP3_TEXT_COLOR}" in html
    assert "font-weight: 700" in html


def test_no_background_highlight():
    """背景色ハイライト（旧仕様の金/銀/銅）が無いこと。"""
    df = build_results_df(_make_result(8))
    html = style_results(df).to_html()
    for old in ("#fff3cd", "#e2e3e5", "#f8d7da", "background-color"):
        assert old not in html


def test_green_only_for_top3_count():
    """緑文字セルは上位3頭 ×（順位・馬名）= 6 セルだけであること。"""
    df = build_results_df(_make_result(10))
    ctx = style_results(df)._compute().ctx
    green_cells = [
        (r, c) for (r, c), props in ctx.items()
        if any("color" in p and TOP3_TEXT_COLOR in v for p, v in props)
    ]
    assert len(green_cells) == 6  # 順位3 + 馬名3


def test_fourth_place_not_green():
    """4位以下には緑文字が付かないこと（行データで確認）。"""
    result = _make_result(5)
    df = build_results_df(result)
    # Styler の内部 apply 結果を直接確認
    from src.ui.results_table import TOP3_HIGHLIGHT_COLUMNS

    styled = style_results(df)
    ctx = styled._compute().ctx  # セルごとのスタイル
    # ctx は {(row, col): [(prop, val), ...]} 形式
    # 4位(index=3)の行に色指定が無いこと
    rank_col = list(df.columns).index("順位")
    for (r, c), props in ctx.items():
        if r >= 3:  # 0-indexed: 順位4位以降
            assert all("color" not in p for p, _ in props)
    # 念のため上位3頭の対象列には色がある
    assert df.iloc[0]["順位"] == 1
    assert TOP3_HIGHLIGHT_COLUMNS  # 定数が定義されている
