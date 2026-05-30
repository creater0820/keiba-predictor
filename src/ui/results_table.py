"""推奨馬テーブル: 確率降順、信頼度バッジ、上位3頭を緑文字で強調。"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.analysis.combiner import RaceProbabilities

# 上位3頭の「順位」「馬名」に適用する文字色（緑）
TOP3_TEXT_COLOR = "#22c55e"
# 文字色を適用する列
TOP3_HIGHLIGHT_COLUMNS = ("順位", "馬名")


def confidence_badge(conf: float) -> str:
    """信頼度を色バッジ付きラベルにする。"""
    if conf >= 0.7:
        return f"🟢 高 ({conf:.2f})"
    if conf >= 0.4:
        return f"🟡 中 ({conf:.2f})"
    return f"🔴 低 ({conf:.2f})"


def build_results_df(result: RaceProbabilities) -> pd.DataFrame:
    """表示用 DataFrame（確率降順）を作る。"""
    rows = []
    for rank, h in enumerate(result.horses, start=1):
        rows.append({
            "順位": rank,
            "馬番": h.horse_number,
            "馬名": h.horse_name,
            "勝率%": h.win_pct,
            "信頼度": confidence_badge(h.confidence),
            "合成スコア": round(h.final_score, 1),
        })
    return pd.DataFrame(rows)


def style_results(df: pd.DataFrame):
    """上位3頭（順位1〜3）の「順位」「馬名」を緑の太字にする Styler を返す。

    背景色のハイライトは行わない（他の行と同じ背景）。テスト可能なよう
    render から分離している。
    """
    def _top3_text(row):
        # 順位1〜3 の対象列だけ緑の太字、それ以外は無装飾
        if row["順位"] <= 3:
            style = f"color: {TOP3_TEXT_COLOR}; font-weight: 700"
            return [style if col in TOP3_HIGHLIGHT_COLUMNS else "" for col in row.index]
        return ["" for _ in row.index]

    return (
        df.style
        .apply(_top3_text, axis=1)
        .format({"勝率%": "{:.1f}", "合成スコア": "{:.1f}"})
    )


def render_results_table(result: RaceProbabilities) -> None:
    """推奨馬テーブルを描画する（上位3頭の順位・馬名を緑文字で強調）。"""
    if not result.horses:
        st.info("表示できる出走馬がありません。")
        return

    df = build_results_df(result)
    styler = style_results(df)
    st.dataframe(styler, use_container_width=True, hide_index=True)

    # 上位3頭のサマリ
    top3 = result.horses[:3]
    cols = st.columns(len(top3))
    medals = ["🥇", "🥈", "🥉"]
    for col, h, medal in zip(cols, top3, medals):
        col.metric(
            f"{medal} {h.horse_number} {h.horse_name}",
            f"{h.win_pct:.1f}%",
            help=f"合成スコア {h.final_score:.1f} / 信頼度 {h.confidence:.2f}",
        )
