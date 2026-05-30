"""統合パイプライン: 1 レースを選んで確率分布を返すオーケストレーション層。

UI からも CLI からも同じ ``predict_race`` を呼べる。データ取得（スクレイパ）→
永続化（storage）→ スコア算出（analysis）→ 合成（combiner）を 1 本に繋ぐ。

スクレイピングはマナー厳守（client.py が担保）。各馬の取得に失敗しても、その馬
だけ中立値で続行し全体は落とさない（自律実装モードの保守的フォールバック）。

返り値の RaceProbabilities.meta に、レース情報・トラックバイアスの出所などを
詰めるので、UI や Markdown 出力でそのまま使える。
"""

from __future__ import annotations

from dataclasses import dataclass

import config
from src.analysis.combiner import (
    CombineWeights,
    HorseScores,
    RaceProbabilities,
    combine_race,
)
from src.analysis.pedigree_score import (
    PedigreeScoreInput,
    SireStat,
    score_pedigree,
)
from src.analysis.running_style_score import (
    RunningStyleScoreInput,
    score_running_style,
)
from src.analysis.track_bias_score import TrackBiasScoreInput, score_track_bias
from src.scraper import pedigree, running_style, track_bias
from src.scraper.client import NetkeibaClient
from src.scraper.race_card import RaceCard, fetch_race_card
from src.scraper.race_list import fetch_kaisai_dates, fetch_race_list
from src.storage import repo

# 血統スコアの基準にする全体平均勝率のフォールバック値（データが無いとき）
DEFAULT_GLOBAL_WIN_RATE = 0.075


@dataclass
class HorseData:
    """1 頭分の取得済みデータ（スコア算出の入力）。"""

    horse_number: int
    horse_name: str
    horse_id: str
    post_position: int
    sire_stat: SireStat | None
    dam_sire_stat: SireStat | None
    running_style: str


def predict_race(
    date: str,
    venue: str,
    race_no: int,
    weights: dict,
    temperature: float,
    force_refresh: bool = False,
    progress=None,
) -> RaceProbabilities:
    """指定レースの予想（各馬の勝率分布）を返す。

    Args:
        date: 開催日 YYYYMMDD。
        venue: 会場名（例 "東京"）。
        race_no: レース番号。
        weights: {"bias","pedigree","style"} の重み（合計1でなくてよい）。
        temperature: softmax 温度。
        force_refresh: True ならキャッシュ無視で再取得。
        progress: 任意のコールバック progress(frac: float, msg: str)。UI 用。

    Returns:
        RaceProbabilities（meta にレース情報・バイアス出所を格納）。
    """
    def _tick(frac: float, msg: str) -> None:
        if progress is not None:
            progress(frac, msg)

    config.ensure_dirs()
    client = NetkeibaClient()
    repo.init_db()

    try:
        with repo.get_session() as session:
            # --- レース特定 ---
            _tick(0.05, "開催日・レース一覧を取得中…")
            dates = fetch_kaisai_dates(client)
            races = fetch_race_list(client, date)
            target = _find_race(races, venue, race_no)
            if target is None:
                raise ValueError(f"レースが見つかりません: {date} {venue} {race_no}R")

            surface = target.surface
            distance_m = target.distance_m
            bucket = pedigree.distance_to_bucket(distance_m)

            # --- 出走表 ---
            _tick(0.12, "出走表を取得中…")
            card = fetch_race_card(client, target.race_id)

            # --- トラックバイアス ---
            _tick(0.2, "トラックバイアスを算出中…")
            bias = track_bias.compute_track_bias(
                client, session, date, venue, surface, dates, force_refresh=force_refresh
            )

            # --- 各馬: 血統 + 脚質 ---
            horse_data: list[HorseData] = []
            n = max(1, len(card.entries))
            for i, e in enumerate(card.entries):
                _tick(0.2 + 0.6 * (i / n), f"血統・脚質を取得中… ({i+1}/{n}) {e.name}")
                horse_data.append(
                    _gather_horse(client, session, e, bucket, surface, force_refresh)
                )

            # --- 全体平均勝率（この距離×馬場の基準）---
            global_avg = _global_win_rate(horse_data)

            # --- スコア算出 + 合成 ---
            _tick(0.85, "スコアを合成中…")
            field_styles = [h.running_style for h in horse_data]
            horse_scores = _build_scores(horse_data, bias, field_styles, global_avg, surface, bucket)

            cw = CombineWeights(
                track_bias=float(weights.get("bias", weights.get("track_bias", 1 / 3))),
                pedigree=float(weights.get("pedigree", 1 / 3)),
                running_style=float(weights.get("style", weights.get("running_style", 1 / 3))),
            )
            result = combine_race(target.race_id, horse_scores, cw, temperature)

            result.meta = {
                "date": date,
                "venue": venue,
                "race_no": race_no,
                "race_id": target.race_id,
                "race_name": card.race_name or target.race_name,
                "surface": surface,
                "distance_m": distance_m,
                "distance_bucket": bucket,
                "track_condition": card.track_condition,
                "weather": card.weather,
                "start_time": card.start_time or target.start_time,
                "bias_describe": bias.describe(),
                "bias_inside_outside": bias.inside_outside_bias,
                "bias_pace": bias.pace_bias,
                "bias_source": bias.source,
                "bias_data_date": bias.data_date,
                "global_avg_win_rate": round(global_avg, 4),
                "field_size": len(card.entries),
            }
            _tick(1.0, "完了")
            return result
    finally:
        client.close()


def _find_race(races, venue: str, race_no: int):
    """会場×レース番号でレースを探す。"""
    for r in races:
        if r.venue == venue and r.race_no == race_no:
            return r
    return None


def _gather_horse(client, session, entry, bucket, surface, force_refresh) -> HorseData:
    """1 頭分の血統統計と脚質を取得する（失敗時は中立フォールバック）。"""
    sire_stat = dam_sire_stat = None
    style = "不明"

    # 血統
    try:
        ids = pedigree.fetch_pedigree_ids(client, entry.horse_id)
        for sid in (ids.sire_id, ids.dam_sire_id):
            pedigree.ensure_sire_stats(client, session, sid)
        sire_stat = _stat_or_none(session, ids.sire_id, bucket, surface)
        dam_sire_stat = _stat_or_none(session, ids.dam_sire_id, bucket, surface)
    except Exception:
        pass  # 血統取れず → None のまま（pedigree_score が中立に倒す）

    # 脚質
    try:
        style, _ = running_style.fetch_and_store_running_style(
            client, session, entry.horse_id, force_refresh=force_refresh
        )
    except Exception:
        style = "不明"

    return HorseData(
        horse_number=entry.horse_number,
        horse_name=entry.name,
        horse_id=entry.horse_id,
        post_position=entry.post_position,
        sire_stat=sire_stat,
        dam_sire_stat=dam_sire_stat,
        running_style=style or "不明",
    )


def _stat_or_none(session, sire_id, bucket, surface) -> SireStat | None:
    """pedigree_stats から SireStat を作る。無ければ None。"""
    row = repo.get_pedigree_stat(session, sire_id, bucket, surface)
    if row is None or row.sample_size <= 0:
        return None
    return SireStat(win_rate=row.win_rate, sample_size=row.sample_size)


def _global_win_rate(horse_data: list[HorseData]) -> float:
    """この距離×馬場の全体平均勝率を、取得済み父・母父の勝率から推定する。"""
    rates: list[float] = []
    for h in horse_data:
        for st in (h.sire_stat, h.dam_sire_stat):
            if st is not None and st.sample_size > 0:
                rates.append(st.win_rate)
    if not rates:
        return DEFAULT_GLOBAL_WIN_RATE
    return sum(rates) / len(rates)


def _build_scores(horse_data, bias, field_styles, global_avg, surface, bucket) -> list[HorseScores]:
    """各馬の 3 スコアを算出して HorseScores のリストにする。"""
    out: list[HorseScores] = []
    for h in horse_data:
        tb = score_track_bias(TrackBiasScoreInput(
            post_position=h.post_position,
            running_style=h.running_style,
            inside_outside_bias=bias.inside_outside_bias,
            pace_bias=bias.pace_bias,
            bias_n_races=bias.n_races,
        ))
        ped = score_pedigree(PedigreeScoreInput(
            sire=h.sire_stat,
            dam_sire=h.dam_sire_stat,
            global_avg_win_rate=global_avg,
            context={"surface": surface, "distance_bucket": bucket},
        ))
        rs = score_running_style(RunningStyleScoreInput(
            running_style=h.running_style,
            post_position=h.post_position,
            field_styles=field_styles,
        ))
        out.append(HorseScores(
            horse_number=h.horse_number,
            horse_name=h.horse_name,
            track_bias=(tb[0], tb[1]),
            pedigree=(ped[0], ped[1]),
            running_style=(rs[0], rs[1]),
        ))
    return out
