"""根拠の可視化: 3 要素スコアの寄与を plotly 積み上げ横棒グラフで表示。"""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from src.analysis.combiner import RaceProbabilities

# 3 要素の色（凡例）
FACTOR_COLORS = {
    "トラックバイアス": "#4C78A8",
    "血統": "#F58518",
    "脚質": "#54A24B",
}


def render_factor_breakdown(result: RaceProbabilities) -> None:
    """各馬の 3 要素スコア寄与を積み上げ横棒で表示する。"""
    if not result.horses:
        st.info("表示できるデータがありません。")
        return

    only_top5 = st.checkbox("上位5頭のみ表示", value=len(result.horses) > 8)
    horses = result.horses[:5] if only_top5 else result.horses

    # 重み付き寄与 = weight * score（積み上げると final_score になる）
    w = result.weights
    names = [f"{h.horse_number} {h.horse_name}" for h in horses][::-1]  # 上位を上に

    contributions = {
        "トラックバイアス": [w.track_bias * h.breakdown["track_bias_score"] for h in horses][::-1],
        "血統": [w.pedigree * h.breakdown["pedigree_score"] for h in horses][::-1],
        "脚質": [w.running_style * h.breakdown["running_style_score"] for h in horses][::-1],
    }

    fig = go.Figure()
    for factor, vals in contributions.items():
        # ホバーに breakdown の素のスコア・信頼度を出す
        customdata = _hover_data(horses, factor)[::-1]
        fig.add_trace(go.Bar(
            y=names, x=vals, name=factor, orientation="h",
            marker_color=FACTOR_COLORS[factor],
            customdata=customdata,
            hovertemplate=(
                f"<b>{factor}</b><br>"
                "素スコア: %{customdata[0]:.1f}<br>"
                "信頼度: %{customdata[1]:.2f}<br>"
                "寄与: %{x:.1f}<extra></extra>"
            ),
        ))

    fig.update_layout(
        barmode="stack",
        xaxis_title="重み付き寄与（合計＝合成スコア）",
        height=max(300, 60 * len(horses)),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=10, r=10, t=40, b=10),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("各バーの長さ＝重み×素スコア。積み上げた合計が合成スコアになります。")


def _hover_data(horses, factor: str):
    """ホバー用 [素スコア, 信頼度] のリスト。"""
    key = {
        "トラックバイアス": ("track_bias_score", "track_bias_conf"),
        "血統": ("pedigree_score", "pedigree_conf"),
        "脚質": ("running_style_score", "running_style_conf"),
    }[factor]
    return [[h.breakdown[key[0]], h.breakdown[key[1]]] for h in horses]
