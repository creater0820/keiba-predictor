"""脚質（逃げ/先行/差し/追込）を馬の過去走から推定するモジュール。

データ源:
    https://db.netkeiba.com/horse/result/{horse_id}/
    （競走成績テーブル。1 コーナー通過順位 "通過" と "頭数" を使う）

推定ロジック:
    直近最大 RUNNING_STYLE_MAX_RUNS 走の「1 コーナー通過順位 ÷ 頭数」の平均で
    早い位置取りかどうかを測り、閾値で 4 脚質に分類する。
    使える走数が RUNNING_STYLE_MIN_RUNS 未満なら「不明」（confidence=走数）。

推定結果は horses テーブルに running_style と running_style_confidence（=使用走数）
として保存し、RUNNING_STYLE_TTL_DAYS 日キャッシュする。

注意: parse / estimate は I/O を持たない純粋関数なのでダミーデータでテスト可能。
取得＋保存を行う fetch_and_store_running_style だけがネットワーク・DB に触れる。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from bs4 import BeautifulSoup

import config
from src.scraper.client import NetkeibaClient
from src.storage import repo

RESULT_PAGE_URL = "https://db.netkeiba.com/horse/result/{horse_id}/"


@dataclass
class PastRun:
    """過去走 1 走分のうち、脚質推定に必要な部分だけ。"""

    date: str           # 日付（表記そのまま）
    field_size: int     # 頭数
    first_corner: int   # 1 コーナーでの通過順位（先頭=1）
    finish: int         # 着順（参考）


def fetch_and_store_running_style(
    client: NetkeibaClient, session, horse_id: str, force_refresh: bool = False
) -> tuple[str, int, float | None]:
    """馬の脚質を取得・推定して horses に保存し、(脚質, 走数, 平均位置率) を返す。

    horses に 30 日以内の推定が残っていれば再取得しない（キャッシュ）。
    平均位置率は新規推定したときのみ得られる（キャッシュ命中時は None）。

    Returns:
        (running_style, confidence_runs, avg_position_ratio | None) のタプル。
    """
    # --- キャッシュ確認（horses.fetched_at が 30 日以内なら再取得しない）---
    if not force_refresh:
        horse = repo.get_horse(session, horse_id)
        if horse is not None and horse.running_style and _within_ttl(horse.fetched_at):
            return (horse.running_style, horse.running_style_confidence, None)

    # --- 取得・解析・推定 ---
    html = client.fetch(RESULT_PAGE_URL.format(horse_id=horse_id), force_refresh=force_refresh)
    runs = parse_horse_runs(html)
    style, n_runs, breakdown = estimate_running_style(runs)

    # --- 保存（既存の馬情報を壊さないよう merge）---
    repo.upsert_horse(
        session,
        horse_id=horse_id,
        running_style=style,
        running_style_confidence=n_runs,
    )
    return (style, n_runs, breakdown.get("avg_position_ratio"))


def parse_horse_runs(html: str) -> list[PastRun]:
    """競走成績ページから過去走（頭数・1 コーナー通過順位・着順）を抽出する。

    新しい順（ページ表示順）で返す。通過順位が無い走（地方・障害等で欠損）は除く。
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one("table.db_h_race_results")
    if table is None:
        return []

    rows = table.select("tr")
    if not rows:
        return []

    # ヘッダから必要列のインデックスを特定（列順は変わりうるので名前で探す）
    headers = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])]
    idx_date = _find_col(headers, "日付")
    idx_field = _find_col(headers, "頭数")
    idx_finish = _find_col(headers, "着順")
    idx_pass = _find_col(headers, "通過")
    if idx_field is None or idx_pass is None:
        return []

    runs: list[PastRun] = []
    for tr in rows[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all(["th", "td"])]
        if len(cells) <= max(idx_field, idx_pass):
            continue

        field_size = _safe_int(cells[idx_field])
        first_corner = _first_corner(cells[idx_pass])
        if field_size <= 0 or first_corner <= 0:
            continue  # 通過順位・頭数が取れない走はスキップ

        runs.append(
            PastRun(
                date=cells[idx_date] if idx_date is not None else "",
                field_size=field_size,
                first_corner=first_corner,
                finish=_safe_int(cells[idx_finish]) if idx_finish is not None else 0,
            )
        )
    return runs


def estimate_running_style(
    runs: list[PastRun],
    max_runs: int | None = None,
    min_runs: int | None = None,
) -> tuple[str, int, dict]:
    """過去走リストから脚質を推定する（純粋関数）。

    Args:
        runs: 新しい順の過去走リスト。
        max_runs: 使う最大走数（None なら config 値）。
        min_runs: 不明扱いにする下限走数（None なら config 値）。

    Returns:
        (脚質, 使用走数, debug_breakdown) のタプル。
        使用走数が下限未満なら脚質は "不明"。
    """
    max_n = max_runs if max_runs is not None else config.RUNNING_STYLE_MAX_RUNS
    min_n = min_runs if min_runs is not None else config.RUNNING_STYLE_MIN_RUNS

    used = runs[:max_n]
    n = len(used)

    breakdown: dict = {
        "n_runs_used": n,
        "position_ratios": [],
        "avg_position_ratio": None,
        "style": "不明",
    }

    if n < min_n:
        # 走数不足 → 不明（confidence=走数）
        return ("不明", n, breakdown)

    # 1 コーナー通過順位を頭数で正規化（0=先頭〜1=最後方）
    ratios = [r.first_corner / r.field_size for r in used]
    avg = sum(ratios) / len(ratios)

    style = _classify(avg)
    breakdown.update(
        position_ratios=[round(x, 3) for x in ratios],
        avg_position_ratio=round(avg, 3),
        style=style,
    )
    return (style, n, breakdown)


def _classify(avg_ratio: float) -> str:
    """正規化平均位置（0=前〜1=後）を脚質に変換する。"""
    th = config.RUNNING_STYLE_THRESHOLDS
    if avg_ratio <= th["逃げ"]:
        return "逃げ"
    if avg_ratio <= th["先行"]:
        return "先行"
    if avg_ratio <= th["差し"]:
        return "差し"
    return "追込"


def _first_corner(passage: str) -> int:
    """通過順位文字列 "3-3-3-3" から 1 コーナーの順位 3 を取り出す。"""
    m = re.match(r"\s*(\d+)", passage)
    return int(m.group(1)) if m else 0


def _find_col(headers: list[str], name: str) -> int | None:
    """ヘッダ名（部分一致）から列インデックスを返す。無ければ None。"""
    for i, h in enumerate(headers):
        if name in h:
            return i
    return None


def _safe_int(text: str) -> int:
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else 0


def _within_ttl(fetched_at: datetime | None) -> bool:
    """horses.fetched_at が脚質キャッシュの有効期間（30 日）内かどうか。"""
    if fetched_at is None:
        return False
    return datetime.now() - fetched_at <= timedelta(days=config.RUNNING_STYLE_TTL_DAYS)
