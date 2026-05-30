"""pedigree.py / track_bias.py のオフライン解析テスト。

保存済み fixture（実 netkeiba HTML）を使い、ネットワークには出ない。

実行::

    pytest -v tests/test_pedigree_bias.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.scraper.pedigree import (  # noqa: E402
    distance_to_bucket,
    parse_pedigree_ids,
    parse_sire_distance_stats,
)
from src.scraper.track_bias import (  # noqa: E402
    RaceResultSummary,
    _aggregate,
    parse_result_summary,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


# --- 血統 ID ---
def test_parse_pedigree_ids():
    """血統ページから父・母父の ID が取れること。"""
    html = (FIXTURES / "ped_2023103052.html").read_text(encoding="utf-8")
    ids = parse_pedigree_ids(html, "2023103052")
    assert ids.sire_id == "2011110091"      # 父 アジアエクスプレス
    assert ids.sire_name == "アジアエクスプレス"
    assert ids.dam_sire_id == "2000101561"  # 母父 リンカーン
    assert ids.dam_sire_name == "リンカーン"


# --- 距離バケット ---
@pytest.mark.parametrize(
    "dist,bucket",
    [(1200, "-1400"), (1400, "-1400"), (1600, "-1800"),
     (1800, "-1800"), (2000, "-2200"), (2400, "-2600"), (3000, "2600-")],
)
def test_distance_to_bucket(dist, bucket):
    assert distance_to_bucket(dist) == bucket


# --- 種牡馬距離別成績 ---
def test_parse_sire_distance_stats():
    """種牡馬距離別ページから芝・ダート×距離の成績が取れること。"""
    html = (FIXTURES / "sire_dist_2011110091.html").read_text(encoding="utf-8")
    stats = parse_sire_distance_stats(html)
    # 芝 5 バケット + ダート 5 バケット = 10 行
    assert len(stats) == 10
    surfaces = {s.surface for s in stats}
    assert surfaces == {"turf", "dirt"}

    # ダート -1800 の累計（既知の値）を検証
    dirt_1800 = next(
        s for s in stats if s.surface == "dirt" and s.distance_bucket == "-1800"
    )
    assert dirt_1800.wins == 50
    assert dirt_1800.sample_size == 729
    assert abs(dirt_1800.win_rate - 0.0686) < 0.001


# --- トラックバイアス（結果解析）---
def test_parse_result_summary():
    """結果ページから着順確定・枠・上がり最速が取れること。"""
    html = (FIXTURES / "result_202605021101.html").read_text(encoding="utf-8")
    summ = parse_result_summary(html)
    assert summ.has_result is True
    assert summ.surface == "dirt"
    assert summ.distance_m == 1400
    assert summ.field_size == 16
    assert summ.top_frames[:3] == [5, 7, 8]  # 1〜3 着の枠
    assert summ.winner_fastest_agari is True


def test_aggregate_bias_ranges():
    """_aggregate がバイアスを -1〜+1 の範囲で返すこと。"""
    # 内枠が上位を独占＋前残り（勝ち馬が上がり最速でない）ケース
    inside = [
        RaceResultSummary(True, "dirt", 1400, 16, [1, 2, 1], False),
        RaceResultSummary(True, "dirt", 1600, 16, [2, 1, 3], False),
    ]
    io, pace = _aggregate(inside)
    assert -1.0 <= io <= 1.0
    assert io < 0          # 内枠中心 → 内有利（負）
    assert pace == -1.0    # 全レース前残り → 強い前残り

    # 外枠＋差し決着
    outside = [
        RaceResultSummary(True, "turf", 2000, 18, [7, 8, 8], True),
        RaceResultSummary(True, "turf", 1800, 16, [8, 7, 6], True),
    ]
    io2, pace2 = _aggregate(outside)
    assert io2 > 0         # 外有利（正）
    assert pace2 == 1.0    # 全レース差し決着


def test_neutral_when_empty():
    """結果ゼロ件なら中立（0,0）を返すこと。"""
    assert _aggregate([]) == (0.0, 0.0)
