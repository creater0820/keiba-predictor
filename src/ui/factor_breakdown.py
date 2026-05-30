"""根拠の可視化: 3 要素スコアの寄与（積み上げ棒）＋ 各馬の評価理由（自然文）。"""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from src.analysis.combiner import RaceProbabilities
from src.analysis.reasoning import (
    reason_pedigree,
    reason_running_style,
    reason_track_bias,
)

# 3 要素の色（凡例）
FACTOR_COLORS = {
    "トラックバイアス": "#4C78A8",
    "血統": "#F58518",
    "脚質": "#54A24B",
}


def render_factor_breakdown(result: RaceProbabilities) -> None:
    """積み上げ横棒グラフ＋各馬の評価理由カードを表示する。"""
    if not result.horses:
        st.info("表示できるデータがありません。")
        return

    only_top5 = st.checkbox("上位5頭のみ表示", value=len(result.horses) > 8)
    horses = result.horses[:5] if only_top5 else result.horses

    _render_chart(horses, result.weights)

    st.divider()
    st.markdown("## 各馬の評価理由")
    st.caption("スコアは中立(50)からの差。`+`が大きいほどその要素で高評価。")
    render_horse_reasoning(horses)


def _render_chart(horses, w) -> None:
    """3 要素の重み付き寄与を積み上げ横棒で表示する。"""
    names = [f"{h.horse_number} {h.horse_name}" for h in horses][::-1]  # 上位を上に
    contributions = {
        "トラックバイアス": [w.track_bias * h.breakdown["track_bias_score"] for h in horses][::-1],
        "血統": [w.pedigree * h.breakdown["pedigree_score"] for h in horses][::-1],
        "脚質": [w.running_style * h.breakdown["running_style_score"] for h in horses][::-1],
    }
    fig = go.Figure()
    for factor, vals in contributions.items():
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


def render_horse_reasoning(horses) -> None:
    """各馬を expander で折りたたみ、3 要素の理由を自然文で表示する。

    上位5頭はデフォルト展開、6位以下は折りたたみ。details が無い馬でも
    reasoning 側が安全な文言を返すため落ちない。
    """
    for rank, h in enumerate(horses, start=1):
        badge = _conf_badge(h.confidence)
        star = "⭐ " if rank <= 3 else ""
        label = f"{rank}位  {star}{h.horse_number} {h.horse_name}　（{h.win_pct:.1f}% / {badge}）"
        with st.expander(label, expanded=(rank <= 5)):
            details = h.details or {}
            _factor_block(
                "📊", "トラックバイアス",
                h.breakdown.get("track_bias_score", 50.0),
                reason_track_bias(details.get("track_bias", {})),
            )
            _factor_block(
                "🧬", "血統",
                h.breakdown.get("pedigree_score", 50.0),
                reason_pedigree(details.get("pedigree", {})),
            )
            _factor_block(
                "🏃", "脚質",
                h.breakdown.get("running_style_score", 50.0),
                reason_running_style(details.get("running_style", {})),
            )


def _factor_block(icon: str, name: str, score: float, reason: str) -> None:
    """1 要素を「アイコン＋名前＋色付きスコア差」＋「理由文」の 2 行で描く。"""
    delta = score - 50.0
    dtxt = f"{delta:+.1f}"
    if delta >= 10:
        colored = f":green[`{dtxt}`]"
    elif delta <= -10:
        colored = f":red[`{dtxt}`]"
    else:
        colored = f":gray[`{dtxt}`]"
    st.markdown(f"{icon} **{name}** {colored}")
    st.markdown(f"> {reason}")


def _conf_badge(conf: float) -> str:
    if conf >= 0.7:
        return "🟢 高"
    if conf >= 0.4:
        return "🟡 中"
    return "🔴 低"


def _hover_data(horses, factor: str):
    """ホバー用 [素スコア, 信頼度] のリスト。"""
    key = {
        "トラックバイアス": ("track_bias_score", "track_bias_conf"),
        "血統": ("pedigree_score", "pedigree_conf"),
        "脚質": ("running_style_score", "running_style_conf"),
    }[factor]
    return [[h.breakdown[key[0]], h.breakdown[key[1]]] for h in horses]
