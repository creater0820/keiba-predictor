"""サイドバー UI: 日付・レース選択、重み・温度スライダー、再取得ボタン。

レース一覧の取得は呼び出し側（app.py）から渡される load_races 関数に委ねる
（st.cache_data でラップされている前提）。サイドバー自身はデータ取得方法を
知らない。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import streamlit as st


@dataclass
class SidebarState:
    """サイドバーの入力結果。"""

    date: str                 # YYYYMMDD
    venue: str
    race_no: int
    race_label: str
    weights: dict             # {"bias","pedigree","style"}
    temperature: float
    force_refresh: bool
    race_id: str = ""
    races: list = field(default_factory=list)
    error: str = ""


def render_sidebar(load_races) -> SidebarState:
    """サイドバーを描画し、選択状態を返す。

    Args:
        load_races: load_races(date_str, force_refresh) -> list[RaceListEntry]。

    Returns:
        SidebarState。
    """
    st.sidebar.header("⚙️ 予想設定")

    # --- 日付（デフォルト: 明日）---
    default_date = date.today() + timedelta(days=1)
    picked = st.sidebar.date_input("開催日", value=default_date)
    date_str = picked.strftime("%Y%m%d")

    # --- データ再取得ボタン（押下時のみキャッシュ無視）---
    force_refresh = st.sidebar.button("🔄 データ再取得（キャッシュ無視）")

    # --- レース一覧の取得 ---
    races = []
    error = ""
    try:
        races = load_races(date_str, force_refresh)
    except Exception as exc:  # 取得失敗してもアプリは落とさない
        error = f"レース一覧の取得に失敗しました: {exc}"

    venue = ""
    race_no = 0
    race_label = ""
    race_id = ""

    if races:
        venues = sorted({r.venue for r in races})
        venue = st.sidebar.selectbox("会場", venues)
        venue_races = [r for r in races if r.venue == venue]
        # ラベル: "11R 東京優駿(ダービー) 15:40 芝2400m"
        labels = [
            f"{r.race_no}R {r.race_name} {r.start_time} "
            f"{_surface_jp(r.surface)}{r.distance_m}m"
            for r in venue_races
        ]
        idx = st.sidebar.selectbox(
            "レース", range(len(venue_races)),
            format_func=lambda i: labels[i],
        )
        chosen = venue_races[idx]
        race_no = chosen.race_no
        race_label = labels[idx]
        race_id = chosen.race_id
    elif not error:
        error = f"{date_str} のレースが見つかりませんでした。"

    # --- 重みスライダー ---
    st.sidebar.subheader("重み（合計1.0に自動正規化）")
    w_bias = st.sidebar.slider("トラックバイアス", 0.0, 1.0, 0.33, 0.01)
    w_ped = st.sidebar.slider("血統", 0.0, 1.0, 0.33, 0.01)
    w_style = st.sidebar.slider("脚質", 0.0, 1.0, 0.33, 0.01)
    total = w_bias + w_ped + w_style
    if total > 0:
        st.sidebar.caption(
            f"正規化後: 馬場 {w_bias/total:.0%} / 血統 {w_ped/total:.0%} / "
            f"脚質 {w_style/total:.0%}"
        )
    else:
        st.sidebar.caption("すべて0 → 均等(1/3ずつ)で計算します")

    # --- temperature ---
    temperature = st.sidebar.slider(
        "temperature（小さいほど確率が尖る）", 0.5, 3.0, 1.0, 0.1
    )

    return SidebarState(
        date=date_str, venue=venue, race_no=race_no, race_label=race_label,
        weights={"bias": w_bias, "pedigree": w_ped, "style": w_style},
        temperature=temperature, force_refresh=force_refresh,
        race_id=race_id, races=races, error=error,
    )


def _surface_jp(surface: str) -> str:
    return {"turf": "芝", "dirt": "ダ", "jump": "障"}.get(surface, "")
