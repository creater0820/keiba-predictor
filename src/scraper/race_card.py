"""出走表（馬柱）を netkeiba から取得するモジュール。

出走表ページ::

    https://race.netkeiba.com/race/shutuba.html?race_id=YYYYMMDD....

このページは静的 HTML に出走馬テーブルが含まれるため、そのまま解析できる
（一覧ページと違って Ajax ではない）。脚質（逃げ/先行/差し/追込）は出走表
そのものには載らないため、ここでは取得できる範囲（枠・馬番・馬名・horse_id・
性齢・斤量・騎手・調教師）を抽出する。脚質は後段の分析モジュールが馬の過去
成績から推定する。

公開関数:
    fetch_race_card(client, race_id) -> RaceCard
    parse_race_card(html, race_id)   -> RaceCard（オフラインテスト用）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from src.scraper.client import NetkeibaClient

SHUTUBA_URL = "https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"

# 馬場種別の表記 → 内部値
_SURFACE_MAP = {"芝": "turf", "ダ": "dirt", "障": "jump"}
# 馬場状態の表記 → 内部値
_CONDITION_MAP = {"良": "firm", "稍": "good", "稍重": "good", "重": "yielding", "不": "soft", "不良": "soft"}


@dataclass
class HorseEntry:
    """出走馬 1 頭分の情報。

    Attributes:
        post_position: 枠番（1〜8）。
        horse_number: 馬番。
        horse_id: netkeiba の馬 ID（血統取得などに使う）。
        name: 馬名。
        sex: 性別（"牡" / "牝" / "セ"）。
        age: 年齢。
        weight_to_carry: 斤量（背負う重量, kg）。
        jockey: 騎手名。
        trainer: 調教師名（所属込み, 例 "美浦 柄崎"）。
    """

    post_position: int
    horse_number: int
    horse_id: str
    name: str
    sex: str
    age: int
    weight_to_carry: float
    jockey: str
    trainer: str


@dataclass
class RaceCard:
    """1 レース分の出走表。

    Attributes:
        race_id: レース ID。
        race_name: レース名。
        surface: 馬場種別（"turf" / "dirt" / "jump"）。
        distance_m: 距離（メートル）。
        direction: 回り（"left" / "right" / "straight" / ""）。
        weather: 天候（例: "晴"）。
        track_condition: 馬場状態（"firm" / "good" / "yielding" / "soft" / ""）。
        start_time: 発走時刻（"HH:MM"）。
        entries: 出走馬のリスト。
    """

    race_id: str
    race_name: str
    surface: str
    distance_m: int
    direction: str
    weather: str
    track_condition: str
    start_time: str
    entries: list[HorseEntry] = field(default_factory=list)


def fetch_race_card(client: NetkeibaClient, race_id: str) -> RaceCard:
    """指定レースの出走表を取得して解析する。

    Args:
        client: HTTP クライアント。
        race_id: レース ID。

    Returns:
        解析済みの RaceCard。
    """
    html = client.fetch(SHUTUBA_URL.format(race_id=race_id))
    return parse_race_card(html, race_id)


def parse_race_card(html: str, race_id: str) -> RaceCard:
    """出走表 HTML を解析して RaceCard を返す（ネットワーク不要）。

    Args:
        html: shutuba ページの HTML。
        race_id: レース ID。

    Returns:
        RaceCard。
    """
    soup = BeautifulSoup(html, "lxml")

    # --- レース見出し ---
    name_el = soup.select_one(".RaceName")
    race_name = name_el.get_text(strip=True) if name_el else ""

    data01 = soup.select_one(".RaceData01")
    meta = _parse_race_data01(data01.get_text(" ", strip=True) if data01 else "")

    # --- 出走馬テーブル ---
    entries: list[HorseEntry] = []
    for row in soup.select("table.Shutuba_Table tr.HorseList"):
        entry = _parse_horse_row(row)
        if entry is not None:
            entries.append(entry)

    return RaceCard(
        race_id=race_id,
        race_name=race_name,
        surface=meta["surface"],
        distance_m=meta["distance"],
        direction=meta["direction"],
        weather=meta["weather"],
        track_condition=meta["condition"],
        start_time=meta["time"],
        entries=entries,
    )


def _parse_race_data01(text: str) -> dict:
    """RaceData01 テキストを各項目に分解する。

    例: "09:40発走 / ダ1600m (左) / 天候:晴 / 馬場:良"
    """
    result = {
        "time": "",
        "surface": "unknown",
        "distance": 0,
        "direction": "",
        "weather": "",
        "condition": "",
    }

    m_time = re.search(r"(\d{1,2}:\d{2})発走", text)
    if m_time:
        result["time"] = m_time.group(1)

    m_dist = re.search(r"([芝ダ障])(\d+)m", text)
    if m_dist:
        result["surface"] = _SURFACE_MAP.get(m_dist.group(1), "unknown")
        result["distance"] = int(m_dist.group(2))

    m_dir = re.search(r"\((左|右|直)\)", text)
    if m_dir:
        result["direction"] = {"左": "left", "右": "right", "直": "straight"}[m_dir.group(1)]

    m_weather = re.search(r"天候\s*[:：]\s*(\S+)", text)
    if m_weather:
        result["weather"] = m_weather.group(1)

    m_cond = re.search(r"馬場\s*[:：]\s*(\S+)", text)
    if m_cond:
        raw = m_cond.group(1)
        result["condition"] = _CONDITION_MAP.get(raw, raw)

    return result


def _parse_horse_row(row) -> HorseEntry | None:
    """出走表の 1 行（tr.HorseList）を HorseEntry に変換する。

    解析に必要な要素が欠ける行（見出し等）は None を返す。
    """
    # 馬名 + horse_id（HorseInfo セル内のリンク）
    info = row.select_one("td.HorseInfo a")
    if info is None:
        return None
    name = info.get_text(strip=True)
    m_hid = re.search(r"/horse/(\d+)", info.get("href", ""))
    horse_id = m_hid.group(1) if m_hid else ""

    # 枠番（td.Waku1 〜 Waku8 のように数字付きクラス、無ければテキストから）
    waku_td = row.select_one('td[class*="Waku"]')
    post_position = _to_int(waku_td.get_text(strip=True)) if waku_td else 0

    # 馬番（td.Umaban1 〜）
    umaban_td = row.select_one('td[class*="Umaban"]')
    horse_number = _to_int(umaban_td.get_text(strip=True)) if umaban_td else 0

    # 性齢（td.Barei, 例 "牝3"）
    barei_td = row.select_one("td.Barei")
    sex, age = _parse_sex_age(barei_td.get_text(strip=True) if barei_td else "")

    # 斤量: 性齢セルの次の td（位置が安定しているため相対指定）
    weight_to_carry = 0.0
    if barei_td is not None:
        kin_td = barei_td.find_next_sibling("td")
        if kin_td is not None:
            weight_to_carry = _to_float(kin_td.get_text(strip=True))

    # 騎手・調教師
    jockey_td = row.select_one("td.Jockey")
    jockey = jockey_td.get_text(strip=True) if jockey_td else ""
    trainer_td = row.select_one("td.Trainer")
    trainer = trainer_td.get_text(" ", strip=True) if trainer_td else ""

    return HorseEntry(
        post_position=post_position,
        horse_number=horse_number,
        horse_id=horse_id,
        name=name,
        sex=sex,
        age=age,
        weight_to_carry=weight_to_carry,
        jockey=jockey,
        trainer=trainer,
    )


def _parse_sex_age(text: str) -> tuple[str, int]:
    """性齢 "牝3" を ("牝", 3) に分解する。"""
    m = re.match(r"([牡牝セ騙])(\d+)", text)
    if not m:
        return ("", 0)
    return (m.group(1), int(m.group(2)))


def _to_int(text: str) -> int:
    """数字以外を除いて int 化。失敗時は 0。"""
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else 0


def _to_float(text: str) -> float:
    """先頭の数値（小数可）を float 化。失敗時は 0.0。"""
    m = re.search(r"\d+(?:\.\d+)?", text)
    return float(m.group()) if m else 0.0
