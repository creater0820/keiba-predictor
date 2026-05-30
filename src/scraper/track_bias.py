"""トラックバイアス（馬場傾向）を当日/前日のレース結果から算出するモジュール。

馬場傾向は「実際のレース結果」から逆算する。当日まだ結果が無い（＝明日の予想）
場合は前日の同会場結果を使い、それも無ければ中立（50 相当 = バイアス 0）に倒す。

フォールバック順（重要）:
    1. 当日先行レース … 予想対象日の同会場・同馬場で既に終わったレース結果
    2. 前日同会場     … 直近の過去開催日（同会場）の同馬場結果
    3. 中立           … データ不足。inside_outside_bias=0, pace_bias=0

返り値にはデータの出所（source）と算出に使った日付（data_date）を含めるので、
UI で「○月○日（前日）の馬場傾向に基づく」と表示できる。

算出するバイアス（いずれも -1.0 〜 +1.0）:
    inside_outside_bias : 内有利(-) 〜 外有利(+)。上位入線馬の枠の平均で判定。
    pace_bias           : 前残り有利(-) 〜 差し有利(+)。勝ち馬が上がり最速
                          （末脚で差した）割合で判定。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from src.scraper.client import NetkeibaClient
from src.scraper.race_list import fetch_race_list
from src.storage import repo

RESULT_URL = "https://race.netkeiba.com/race/result.html?race_id={race_id}"

# JRA の枠は最大 8。枠比率の正規化に使う。
MAX_FRAME = 8
# 上位何頭を「内外バイアス」の判定に使うか
TOP_N_FOR_FRAME = 3


@dataclass
class RaceResultSummary:
    """1 レースの結果から取り出したバイアス判定用の要約。"""

    has_result: bool          # 着順が確定しているか（未確定＝未来のレース）
    surface: str              # turf / dirt / jump
    distance_m: int
    field_size: int           # 出走頭数
    top_frames: list[int]     # 上位入線馬の枠番（最大 TOP_N_FOR_FRAME 頭）
    winner_fastest_agari: bool  # 勝ち馬が上がり 3F 最速だったか


@dataclass
class TrackBias:
    """ある予想日・会場・馬場のトラックバイアス（メタ情報込み）。"""

    date: str                 # 予想対象日（YYYYMMDD）
    venue: str
    surface: str
    inside_outside_bias: float  # -1(内) 〜 +1(外)
    pace_bias: float            # -1(前残り) 〜 +1(差し)
    data_date: str            # 実際に算出に使った日（YYYYMMDD）。中立時は ""
    source: str               # "同日(先行レース)" / "前日同会場" / "中立(データ不足)"
    n_races: int              # 算出に使ったレース数

    def describe(self) -> str:
        """UI 表示用の一言説明を返す。"""
        if self.source.startswith("中立"):
            return "馬場傾向データ不足のため中立（補正なし）"
        d = self.data_date
        nice = f"{int(d[4:6])}月{int(d[6:8])}日" if len(d) == 8 else d
        return f"{nice}（{self.source}）の馬場傾向に基づく / {self.n_races}レース"


# ---------------------------------------------------------------------------
# 結果ページの解析
# ---------------------------------------------------------------------------
def parse_result_summary(html: str) -> RaceResultSummary:
    """結果ページ HTML をバイアス判定用に要約する（ネットワーク不要）。"""
    soup = BeautifulSoup(html, "lxml")

    # 馬場・距離
    surface, distance_m = "unknown", 0
    data01 = soup.select_one(".RaceData01")
    if data01:
        m = re.search(r"([芝ダ障])(\d+)m", data01.get_text(" ", strip=True))
        if m:
            surface = {"芝": "turf", "ダ": "dirt", "障": "jump"}.get(m.group(1), "unknown")
            distance_m = int(m.group(2))

    rows = soup.select("tr.HorseList")
    top_frames: list[int] = []
    has_result = False
    winner_fastest_agari = False

    for idx, tr in enumerate(rows):
        rank_td = tr.select_one("td.Result_Num")
        time_td = tr.select_one("td.Time")
        rank = _safe_int(rank_td.get_text(strip=True)) if rank_td else 0
        time_text = time_td.get_text(strip=True) if time_td else ""

        # 着順が数字＆タイムがあれば「結果確定」とみなす
        if rank >= 1 and re.search(r"\d:\d", time_text):
            has_result = True

        # 枠番（td.Num のクラス Waku1〜8 から取得）
        if idx < TOP_N_FOR_FRAME:
            waku_td = tr.select_one('td.Num[class*="Waku"]')
            if waku_td:
                frame = _frame_from_class(waku_td.get("class"))
                if frame:
                    top_frames.append(frame)

        # 1 着馬が上がり最速（td に BgOrange マーカー）か
        if idx == 0:
            winner_fastest_agari = tr.select_one("td.BgOrange") is not None

    return RaceResultSummary(
        has_result=has_result,
        surface=surface,
        distance_m=distance_m,
        field_size=len(rows),
        top_frames=top_frames,
        winner_fastest_agari=winner_fastest_agari,
    )


def _frame_from_class(classes: list[str] | None) -> int:
    """["Num","Waku5"] のようなクラスから枠番 5 を取り出す。"""
    if not classes:
        return 0
    for c in classes:
        m = re.search(r"Waku(\d)", c)
        if m:
            return int(m.group(1))
    return 0


def _safe_int(text: str) -> int:
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else 0


# ---------------------------------------------------------------------------
# バイアス算出（フォールバック付き）
# ---------------------------------------------------------------------------
def compute_track_bias(
    client: NetkeibaClient,
    session,
    target_date: str,
    venue: str,
    surface: str,
    available_dates: list[str],
    force_refresh: bool = False,
) -> TrackBias:
    """予想日・会場・馬場のトラックバイアスを算出する（キャッシュ付き）。

    Args:
        client: HTTP クライアント。
        session: DB セッション。
        target_date: 予想対象日（YYYYMMDD）。
        venue: 会場名（例 "東京"）。
        surface: "turf" / "dirt"。
        available_dates: 取得可能な開催日一覧（race_list.fetch_kaisai_dates の結果）。
        force_refresh: True ならキャッシュ無視。

    Returns:
        TrackBias（バイアス値＋出所メタ情報）。
    """
    # --- キャッシュ確認 ---
    if not force_refresh:
        cached = repo.get_track_bias(session, target_date, venue, surface)
        if cached is not None and repo.is_fresh(cached.fetched_at):
            return _from_model(cached)

    # --- 1. 当日先行レース ---
    summ = _gather_results(client, target_date, venue, surface, force_refresh)
    if summ:
        bias = _aggregate(summ)
        result = TrackBias(
            date=target_date, venue=venue, surface=surface,
            inside_outside_bias=bias[0], pace_bias=bias[1],
            data_date=target_date, source="同日(先行レース)", n_races=len(summ),
        )
    else:
        # --- 2. 前日同会場 ---
        prev_date = _prev_same_venue_date(
            client, target_date, venue, available_dates, force_refresh
        )
        summ2 = (
            _gather_results(client, prev_date, venue, surface, force_refresh)
            if prev_date else []
        )
        if summ2:
            bias = _aggregate(summ2)
            result = TrackBias(
                date=target_date, venue=venue, surface=surface,
                inside_outside_bias=bias[0], pace_bias=bias[1],
                data_date=prev_date, source="前日同会場", n_races=len(summ2),
            )
        else:
            # --- 3. 中立 ---
            result = TrackBias(
                date=target_date, venue=venue, surface=surface,
                inside_outside_bias=0.0, pace_bias=0.0,
                data_date="", source="中立(データ不足)", n_races=0,
            )

    # --- 保存（track_bias_daily, 予想日キーで上書き）---
    repo.upsert_track_bias(
        session,
        date=target_date, venue=venue, surface=surface,
        inside_outside_bias=result.inside_outside_bias,
        pace_bias=result.pace_bias,
        raw_json=json.dumps(
            {"data_date": result.data_date, "source": result.source,
             "n_races": result.n_races},
            ensure_ascii=False,
        ),
    )
    return result


def _gather_results(
    client: NetkeibaClient, date: str, venue: str, surface: str, force_refresh: bool
) -> list[RaceResultSummary]:
    """指定日・会場・馬場の「結果が確定した」レース要約を集める。"""
    if not date:
        return []
    races = fetch_race_list(client, date)
    targets = [r for r in races if r.venue == venue and r.surface == surface]

    summaries: list[RaceResultSummary] = []
    for r in targets:
        html = client.fetch(
            RESULT_URL.format(race_id=r.race_id), force_refresh=force_refresh
        )
        summ = parse_result_summary(html)
        if summ.has_result:
            summaries.append(summ)
    return summaries


def _aggregate(summaries: list[RaceResultSummary]) -> tuple[float, float]:
    """複数レースの要約から (inside_outside_bias, pace_bias) を算出する。"""
    # 内外バイアス: 上位入線馬の枠比率（枠/8）の平均 → 0.5 中心を ±1 に展開
    frame_ratios: list[float] = []
    for s in summaries:
        for f in s.top_frames:
            frame_ratios.append(f / MAX_FRAME)
    inside_outside = (
        (sum(frame_ratios) / len(frame_ratios) - 0.5) * 2 if frame_ratios else 0.0
    )

    # ペースバイアス: 勝ち馬が上がり最速だった割合（差し決着の頻度）
    closers = sum(1 for s in summaries if s.winner_fastest_agari)
    pace = (closers / len(summaries) - 0.5) * 2 if summaries else 0.0

    return (round(inside_outside, 4), round(pace, 4))


def _prev_same_venue_date(
    client: NetkeibaClient,
    target_date: str,
    venue: str,
    available_dates: list[str],
    force_refresh: bool,
) -> str | None:
    """target_date より前で、同じ会場が開催された直近の開催日を返す。"""
    earlier = sorted([d for d in available_dates if d < target_date], reverse=True)
    for d in earlier:
        races = fetch_race_list(client, d)
        if any(r.venue == venue for r in races):
            return d
    return None


def _from_model(model) -> TrackBias:
    """track_bias_daily のレコードから TrackBias を復元する（キャッシュ用）。"""
    meta = {}
    try:
        meta = json.loads(model.raw_json) if model.raw_json else {}
    except (ValueError, TypeError):
        meta = {}
    return TrackBias(
        date=model.date, venue=model.venue, surface=model.surface,
        inside_outside_bias=model.inside_outside_bias,
        pace_bias=model.pace_bias,
        data_date=meta.get("data_date", ""),
        source=meta.get("source", ""),
        n_races=meta.get("n_races", 0),
    )
