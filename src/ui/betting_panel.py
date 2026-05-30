"""買い目提案パネル: suggester.py の出力を表で表示する。"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.betting.suggester import BettingSuggestion


def render_betting_panel(suggestion: BettingSuggestion) -> None:
    """買い目提案を表示する。"""
    if suggestion is None:
        st.info("買い目提案はありません。")
        return

    mode = "オッズ連動（期待値ベース）" if suggestion.has_odds else "確率ベース（オッズ未取得）"
    st.caption(f"モード: {mode} / 予算: {suggestion.bankroll:,}円")

    if not suggestion.rows:
        st.info(suggestion.note or "提案できる買い目がありませんでした。")
        return

    rows = []
    for r in suggestion.rows:
        rows.append({
            "券種": r.bet_type,
            "馬番": "-".join(map(str, r.horses)),
            "金額": f"{r.amount:,}円",
            "期待値": f"+{r.ev_pct:.0f}%" if r.ev_pct is not None else "—",
            "理由": r.reason,
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    total = sum(r.amount for r in suggestion.rows)
    st.caption(f"合計投資額: {total:,}円")
    if suggestion.note:
        st.caption("ℹ️ " + suggestion.note)
    st.warning("※ 買い目はあくまで参考情報です。投資判断は自己責任で。", icon="⚠️")
