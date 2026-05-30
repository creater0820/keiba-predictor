"""明日（指定日）のレース一覧を netkeiba から取得するモジュール。

netkeiba のレース一覧は JavaScript で後から読み込まれるため、ブラウザ表示の
HTML をそのまま取っても中身が空になる。代わりに、ページが内部で叩いている
Ajax エンドポイントを直接取得する：

- 開催日リスト : ``race_list_get_date_list.html``
- 各日の一覧   : ``race_list_sub.html?kaisai_date=YYYYMMDD``

これらは HTML 断片を返すので BeautifulSoup で解析できる。

公開関数:
    fetch_kaisai_dates(client)        -> 取得可能な開催日（YYYYMMDD）の一覧
    fetch_race_list(client, date)     -> その日の RaceListEntry 一覧
    parse_race_list(html, date)       -> HTML 断片を解析（オフラインテスト用）
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from src.scraper.client import NetkeibaClient

# Ajax エンドポイント（race.netkeiba.com のレース情報側）
RACE_TOP_BASE = "https://race.netkeiba.com/top/"
DATE_LIST_URL = RACE_TOP_BASE + "race_list_get_date_list.html"
RACE_LIST_SUB_URL = RACE_TOP_BASE + "race_list_sub.html?kaisai_date={date}"

# レース見出しテキスト（例: "1R 3歳未勝利 09:40 ダ1600m 16頭"）を分解する正規表現
_RACE_TEXT_RE = re.compile(
    r"(?P<no>\d+)R\s+"            # レース番号
    r"(?P<name>.+?)\s+"           # レース名（最短一致）
    r"(?P<time>\d{1,2}:\d{2})\s+"  # 発走時刻
    r"(?P<surface>[芝ダ障])"       # 馬場種別（芝/ダート/障害）
    r"(?P<distance>\d+)m\s+"      # 距離
    r"(?P<heads>\d+)頭"           # 出走頭数
)

# 馬場種別の表記をアプリ内部の値に正規化する対応表
_SURFACE_MAP = {"芝": "turf", "ダ": "dirt", "障": "jump"}


@dataclass
class RaceListEntry:
    """レース一覧の 1 レース分の情報。

    Attributes:
        race_id: netkeiba のレース ID（例: "202605021201"）。
        kaisai_date: 開催日（YYYYMMDD）。
        venue: 会場名（例: "東京"）。
        race_no: レース番号（1〜12）。
        race_name: レース名（例: "3歳未勝利"）。
        start_time: 発走時刻（"HH:MM"）。
        surface: 馬場種別（"turf" / "dirt" / "jump"）。
        distance_m: 距離（メートル）。
        head_count: 出走頭数。
    """

    race_id: str
    kaisai_date: str
    venue: str
    race_no: int
    race_name: str
    start_time: str
    surface: str
    distance_m: int
    head_count: int


def fetch_kaisai_dates(client: NetkeibaClient) -> list[str]:
    """取得可能な開催日（YYYYMMDD 文字列）の一覧を返す。

    Args:
        client: マナーを守る HTTP クライアント。

    Returns:
        開催日の昇順リスト（例: ["20260530", "20260531", "20260607"]）。
    """
    html = client.fetch(DATE_LIST_URL)
    # href の中の kaisai_date=YYYYMMDD をすべて拾って重複排除
    dates = sorted(set(re.findall(r"kaisai_date=(\d{8})", html)))
    return dates


def fetch_race_list(client: NetkeibaClient, kaisai_date: str) -> list[RaceListEntry]:
    """指定日のレース一覧を取得して解析する。

    Args:
        client: HTTP クライアント。
        kaisai_date: 開催日（YYYYMMDD）。

    Returns:
        その日の全レース（会場×レース番号）の RaceListEntry 一覧。
    """
    url = RACE_LIST_SUB_URL.format(date=kaisai_date)
    html = client.fetch(url)
    return parse_race_list(html, kaisai_date)


def parse_race_list(html: str, kaisai_date: str) -> list[RaceListEntry]:
    """レース一覧 HTML 断片を解析して RaceListEntry の一覧を返す。

    ネットワークに触れないので、保存済み fixture を使った単体テストに使える。

    Args:
        html: ``race_list_sub.html`` が返す HTML 断片。
        kaisai_date: 開催日（YYYYMMDD）。各エントリに付与する。

    Returns:
        RaceListEntry のリスト（会場ごとの並び順を保持）。
    """
    soup = BeautifulSoup(html, "lxml")
    entries: list[RaceListEntry] = []

    # 会場ごとのブロック（dl.RaceList_DataList）を順に処理する
    for block in soup.select("dl.RaceList_DataList"):
        title_el = block.select_one(".RaceList_DataTitle")
        # 会場名は "2回 東京 12日目" のような表記 → 真ん中の会場名だけ取り出す
        venue = _extract_venue(title_el.get_text(" ", strip=True)) if title_el else ""

        # 各レース（li.RaceList_DataItem）の本体リンク。
        # 未来日は shutuba（出馬表）、過去日は result（結果）にリンクするため両対応。
        # movie 等の補助リンクは拾わない。
        for item in block.select("li.RaceList_DataItem"):
            link = item.select_one('a[href*="shutuba"], a[href*="result"]')
            if link is None:
                continue

            href = link.get("href", "")
            m_id = re.search(r"race_id=(\d+)", href)
            if not m_id:
                continue
            race_id = m_id.group(1)

            text = link.get_text(" ", strip=True)
            parsed = _parse_race_text(text)
            if parsed is None:
                # 想定外の表記はスキップ（落ちないように）
                continue

            entries.append(
                RaceListEntry(
                    race_id=race_id,
                    kaisai_date=kaisai_date,
                    venue=venue,
                    race_no=parsed["no"],
                    race_name=parsed["name"],
                    start_time=parsed["time"],
                    surface=parsed["surface"],
                    distance_m=parsed["distance"],
                    head_count=parsed["heads"],
                )
            )

    return entries


def _extract_venue(title_text: str) -> str:
    """会場見出し "2回 東京 12日目" から会場名 "東京" を取り出す。"""
    # 「N回」「N日目」を取り除いた残りを会場名とみなす
    cleaned = re.sub(r"\d+回|\d+日目", "", title_text)
    return cleaned.strip()


def _parse_race_text(text: str) -> dict | None:
    """レース見出しテキストを各項目に分解する。

    例: "1R 3歳未勝利 09:40 ダ1600m 16頭" を
        {"no":1, "name":"3歳未勝利", "time":"09:40",
         "surface":"dirt", "distance":1600, "heads":16}
    に変換する。解析できなければ None。
    """
    m = _RACE_TEXT_RE.search(text)
    if not m:
        return None
    return {
        "no": int(m.group("no")),
        "name": m.group("name").strip(),
        "time": m.group("time"),
        "surface": _SURFACE_MAP.get(m.group("surface"), "unknown"),
        "distance": int(m.group("distance")),
        "heads": int(m.group("heads")),
    }
