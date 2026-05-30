"""推奨馬テーブル: 確率降順、信頼度バッジ、上位3頭ハイライト。"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.analysis.combiner import RaceProbabilities


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


def render_results_table(result: RaceProbabilities) -> None:
    """推奨馬テーブルを描画する（上位3頭ハイライト）。"""
    if not result.horses:
        st.info("表示できる出走馬がありません。")
        return

    df = build_results_df(result)

    def _highlight_top3(row):
        # 順位1〜3 の行に背景色（金・銀・銅）
        colors = {1: "#fff3cd", 2: "#e2e3e5", 3: "#f8d7da"}
        c = colors.get(row["順位"], "")
        return [f"background-color: {c}" if c else "" for _ in row]

    styler = (
        df.style
        .apply(_highlight_top3, axis=1)
        .format({"勝率%": "{:.1f}", "合成スコア": "{:.1f}"})
    )
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
