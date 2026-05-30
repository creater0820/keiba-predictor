"""Streamlit エントリポイント。

スクレイパ → storage → analysis → combiner → UI を 1 画面に束ねる。
データ取得は st.cache_data（TTL=12h）でラップして重複コールを抑える。
エラーが出てもアプリは落とさず st.error で表示する。

起動:
    streamlit run app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# プロジェクトルートを import パスに追加
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src import pipeline  # noqa: E402
from src.betting.suggester import suggest_bets  # noqa: E402
from src.scraper.client import NetkeibaClient  # noqa: E402
from src.scraper.race_list import fetch_race_list  # noqa: E402
from src.ui.betting_panel import render_betting_panel  # noqa: E402
from src.ui.factor_breakdown import render_factor_breakdown  # noqa: E402
from src.ui.results_table import render_results_table  # noqa: E402
from src.ui.sidebar import render_sidebar  # noqa: E402

CACHE_TTL = 12 * 3600  # 12 時間


# ---------------------------------------------------------------------------
# キャッシュ付きデータ取得
# ---------------------------------------------------------------------------
@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _load_races(date_str: str, force: bool):
    """指定日のレース一覧を取得（キャッシュ）。"""
    client = NetkeibaClient()
    try:
        return fetch_race_list(client, date_str)
    finally:
        client.close()


def load_races(date_str: str, force: bool):
    """force 時はキャッシュをクリアしてから取得。"""
    if force:
        _load_races.clear()
    return _load_races(date_str, force)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _predict(date, venue, race_no, w_bias, w_ped, w_style, temperature, force):
    """予想を実行（キャッシュ）。force は cache key に含めて再計算を促す。"""
    return pipeline.predict_race(
        date=date, venue=venue, race_no=race_no,
        weights={"bias": w_bias, "pedigree": w_ped, "style": w_style},
        temperature=temperature, force_refresh=force,
    )


# ---------------------------------------------------------------------------
# 画面
# ---------------------------------------------------------------------------
def main() -> None:
    st.set_page_config(page_title="競馬予想（確率分布）", page_icon="🏇", layout="wide")
    st.title("🏇 競馬予想 — 確率分布で推奨馬を算出")
    st.caption("netkeiba 公開情報 / トラックバイアス・血統・脚質の3要素を重み付け合成")

    sb = render_sidebar(load_races)

    if sb.error:
        st.error(sb.error)
    if not sb.race_id:
        st.info("サイドバーで開催日・会場・レースを選んでください。")
        return

    if sb.force_refresh:
        _predict.clear()

    st.subheader(f"📍 {sb.venue} {sb.race_label}")

    # --- 予想実行 ---
    try:
        with st.spinner("データ取得＆予想を計算中…（初回はネット取得のため数分かかることがあります）"):
            result = _predict(
                sb.date, sb.venue, sb.race_no,
                sb.weights["bias"], sb.weights["pedigree"], sb.weights["style"],
                sb.temperature, sb.force_refresh,
            )
    except Exception as exc:  # 落とさず表示
        st.error(f"予想の計算に失敗しました: {exc}")
        return

    # データソース表示
    meta = result.meta
    st.caption(
        f"🗓 データ取得: {meta.get('race_name','')} / "
        f"馬場: {_surface_jp(meta.get('surface',''))}{meta.get('distance_m','')}m "
        f"{meta.get('track_condition','')} / 馬場傾向: {meta.get('bias_describe','')}"
    )

    tab1, tab2, tab3 = st.tabs(["🏆 推奨馬", "📊 根拠の可視化", "💰 買い目提案"])

    with tab1:
        render_results_table(result)

    with tab2:
        render_factor_breakdown(result)

    with tab3:
        bankroll = st.number_input("1レース予算（円）", min_value=100, max_value=100000,
                                   value=1000, step=100)
        # UI ではオッズ未取得のため確率ベース提案（オッズは将来対応）
        suggestion = suggest_bets(result, odds=None, bankroll=int(bankroll))
        render_betting_panel(suggestion)

    st.divider()
    st.caption("⚠️ 本アプリは個人利用・学習目的。投資判断は自己責任です。")


def _surface_jp(surface: str) -> str:
    return {"turf": "芝", "dirt": "ダ", "jump": "障"}.get(surface, "")


if __name__ == "__main__":
    main()
