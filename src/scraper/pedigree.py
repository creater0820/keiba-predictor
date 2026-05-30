"""血統情報（父・母父）と、種牡馬の距離別成績を取得するモジュール。

データ源（すべて db.netkeiba.com / 公開ページ）:
    - 血統ページ      : /horse/ped/{horse_id}/         父・母父の ID を取得
    - 種牡馬距離別成績 : /horse/sire.html?id={sire_id}&course=1&mode=1&type=2
                        芝/ダート × 距離バケットの着別度数（累計）

キャッシュ方針:
    種牡馬ページは sire_id 単位でキャッシュする。一度 pedigree_stats に保存
    したら再取得しない（has_pedigree_for_sire で判定）。馬→父/母父の対応も
    HTTP キャッシュ（client.http_cache）に残るため二重取得しない。

距離バケットは netkeiba の表記に合わせる:
    "-1400" : 〜1400m  / "-1800" : 1401〜1800m / "-2200" : 1801〜2200m
    "-2600" : 2201〜2600m / "2600-" : 2601m〜
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from src.scraper.client import NetkeibaClient
from src.storage import repo
from src.storage.models import PedigreeStat

PED_URL = "https://db.netkeiba.com/horse/ped/{horse_id}/"
SIRE_DIST_URL = (
    "https://db.netkeiba.com/horse/sire.html"
    "?id={sire_id}&course=1&mode=1&type=2"
)

# 距離バケットの並び（netkeiba の距離別テーブルの列順と一致）
DISTANCE_BUCKETS = ["-1400", "-1800", "-2200", "-2600", "2600-"]


@dataclass
class PedigreeIds:
    """1 頭分の血統 ID（父・母父）。"""

    horse_id: str
    sire_id: str
    sire_name: str
    dam_sire_id: str
    dam_sire_name: str


def distance_to_bucket(distance_m: int) -> str:
    """距離(m) を netkeiba の距離バケット文字列に変換する。"""
    if distance_m <= 1400:
        return "-1400"
    if distance_m <= 1800:
        return "-1800"
    if distance_m <= 2200:
        return "-2200"
    if distance_m <= 2600:
        return "-2600"
    return "2600-"


# ---------------------------------------------------------------------------
# 血統 ID（父・母父）
# ---------------------------------------------------------------------------
def fetch_pedigree_ids(client: NetkeibaClient, horse_id: str) -> PedigreeIds:
    """馬の血統ページを取得し、父・母父の ID を返す。"""
    html = client.fetch(PED_URL.format(horse_id=horse_id))
    return parse_pedigree_ids(html, horse_id)


def parse_pedigree_ids(html: str, horse_id: str) -> PedigreeIds:
    """血統ページ HTML から父・母父の ID を抽出する（ネットワーク不要）。

    blood_table の第 1 世代（rowspan=16 のセルが 2 つ＝父・母）を使う。
    母父は「母」セルと同じ行の次のセル（母の父）。
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one("table.blood_table")

    sire_id = sire_name = dam_sire_id = dam_sire_name = ""

    if table is not None:
        big_cells = [td for td in table.select("td") if td.get("rowspan") == "16"]
        if len(big_cells) >= 2:
            # 父 = 1 つ目の rowspan=16 セル
            sire_id, sire_name = _cell_horse(big_cells[0])
            # 母父 = 「母」セル(2 つ目)の行の、母の次のセル（母の父）
            dam_cell = big_cells[1]
            dam_row = dam_cell.find_parent("tr")
            tds = dam_row.find_all("td", recursive=False)
            if len(tds) >= 2:
                dam_sire_id, dam_sire_name = _cell_horse(tds[1])

    return PedigreeIds(
        horse_id=horse_id,
        sire_id=sire_id,
        sire_name=sire_name,
        dam_sire_id=dam_sire_id,
        dam_sire_name=dam_sire_name,
    )


def _cell_horse(td) -> tuple[str, str]:
    """血統表のセルから (horse_id, 名前) を取り出す。"""
    a = td.select_one('a[href*="/horse/"]')
    if a is None:
        return ("", "")
    m = re.search(r"/horse/(\w+)", a.get("href", ""))
    horse_id = m.group(1) if m else ""
    return (horse_id, a.get_text(strip=True))


# ---------------------------------------------------------------------------
# 種牡馬の距離別成績
# ---------------------------------------------------------------------------
@dataclass
class SireDistanceStat:
    """種牡馬の、ある馬場×距離バケットの成績（累計）。"""

    surface: str          # turf / dirt
    distance_bucket: str  # "-1400" など
    wins: int
    sample_size: int
    win_rate: float


def parse_sire_distance_stats(html: str) -> list[SireDistanceStat]:
    """種牡馬距離別ページから、平地の芝・ダート成績（累計）を抽出する。

    ページには 平地芝/平地ダート/障害芝/障害ダート の順でテーブルが並ぶ。
    本アプリは平地のみ扱うため、芝・ダートそれぞれ最初のテーブルを使う。
    """
    soup = BeautifulSoup(html, "lxml")
    stats: list[SireDistanceStat] = []

    turf_done = dirt_done = False
    for tb in soup.find_all("table"):
        head = tb.get_text(" ", strip=True)[:40]
        if "-1400" not in head:
            continue  # 距離別テーブル以外はスキップ
        if "(芝)" in head and not turf_done:
            stats.extend(_parse_distance_table(tb, "turf"))
            turf_done = True
        elif "(ダート)" in head and not dirt_done:
            stats.extend(_parse_distance_table(tb, "dirt"))
            dirt_done = True
        if turf_done and dirt_done:
            break

    return stats


def _parse_distance_table(tb, surface: str) -> list[SireDistanceStat]:
    """距離別テーブルの「累計」行から各バケットの成績を作る。"""
    out: list[SireDistanceStat] = []
    for tr in tb.select("tr"):
        cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
        if not cells or cells[0] != "累計":
            continue
        # 累計行: [ラベル, 1着,2着,3着,着外] × 5 バケット = 20 個の数値
        nums = [_safe_int(x) for x in cells[1:21]]
        for i, bucket in enumerate(DISTANCE_BUCKETS):
            win, place, show, out_of = nums[i * 4 : i * 4 + 4]
            total = win + place + show + out_of
            out.append(
                SireDistanceStat(
                    surface=surface,
                    distance_bucket=bucket,
                    wins=win,
                    sample_size=total,
                    win_rate=round(win / total, 4) if total else 0.0,
                )
            )
        break
    return out


def _safe_int(text: str) -> int:
    """数字文字列を int に。空や非数字は 0。"""
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else 0


def ensure_sire_stats(client: NetkeibaClient, session, sire_id: str) -> None:
    """種牡馬の距離別成績を（未取得なら）取得して pedigree_stats に保存する。

    すでに pedigree_stats に当該 sire_id の行があれば何もしない（再取得しない）。

    Args:
        client: HTTP クライアント。
        session: DB セッション。
        sire_id: 種牡馬（=父 or 母父）の ID。
    """
    if not sire_id:
        return
    if repo.has_pedigree_for_sire(session, sire_id):
        return  # 取得済み。種牡馬ページは sire_id 単位でキャッシュ済み。

    html = client.fetch(SIRE_DIST_URL.format(sire_id=sire_id))
    for stat in parse_sire_distance_stats(html):
        repo.upsert_pedigree_stat(
            session,
            sire_id=sire_id,
            distance_bucket=stat.distance_bucket,
            surface=stat.surface,
            win_rate=stat.win_rate,
            sample_size=stat.sample_size,
        )
