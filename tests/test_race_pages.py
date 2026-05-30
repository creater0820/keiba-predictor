"""race_list.py / race_card.py のオフライン解析テスト。

保存済み fixture（実際の netkeiba HTML スナップショット）を使うので、
テスト中にネットワークへは一切出ない。

実行::

    pytest -v tests/test_race_pages.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.scraper.race_card import parse_race_card  # noqa: E402
from src.scraper.race_list import parse_race_list  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture()
def race_list_html() -> str:
    return (FIXTURES / "race_list_20260531.html").read_text(encoding="utf-8")


@pytest.fixture()
def race_list_past_html() -> str:
    return (FIXTURES / "race_list_20260530_result.html").read_text(encoding="utf-8")


@pytest.fixture()
def shutuba_html() -> str:
    return (FIXTURES / "shutuba_202605021201.html").read_text(encoding="utf-8")


# --- レース一覧 ---
def test_parse_race_list_counts(race_list_html):
    """東京12R + 京都12R = 24 レースが解析できること。"""
    entries = parse_race_list(race_list_html, "20260531")
    assert len(entries) == 24
    venues = {e.venue for e in entries}
    assert venues == {"東京", "京都"}


def test_parse_past_date_race_list(race_list_past_html):
    """過去日（result リンク形式）の一覧も解析できること（フォールバック用）。"""
    entries = parse_race_list(race_list_past_html, "20260530")
    assert len(entries) == 24  # shutuba ではなく result リンクでも取得できる
    first = entries[0]
    assert first.race_id == "202605021101"
    assert first.venue == "東京"
    assert first.surface == "dirt"
    assert first.distance_m == 1400


def test_parse_race_list_first_entry(race_list_html):
    """先頭レースの各項目が正しく解析されること。"""
    entries = parse_race_list(race_list_html, "20260531")
    first = entries[0]
    assert first.race_id == "202605021201"
    assert first.kaisai_date == "20260531"
    assert first.race_no == 1
    assert first.race_name == "3歳未勝利"
    assert first.start_time == "09:40"
    assert first.surface == "dirt"
    assert first.distance_m == 1600
    assert first.head_count == 16


# --- 出走表 ---
def test_parse_race_card_meta(shutuba_html):
    """レース見出し（馬場・距離・状態など）が解析できること。"""
    card = parse_race_card(shutuba_html, "202605021201")
    assert card.race_name == "3歳未勝利"
    assert card.surface == "dirt"
    assert card.distance_m == 1600
    assert card.direction == "left"
    assert card.weather == "晴"
    assert card.track_condition == "firm"
    assert card.start_time == "09:40"


def test_parse_race_card_entries(shutuba_html):
    """出走馬テーブルが正しく解析されること（先頭馬を検証）。"""
    card = parse_race_card(shutuba_html, "202605021201")
    assert len(card.entries) == 16  # この fixture は 16 頭立て（一覧の「16頭」と一致）

    head = card.entries[0]
    assert head.post_position == 1
    assert head.horse_number == 1
    assert head.horse_id == "2023103052"
    assert head.name == "ベアエクスプレス"
    assert head.sex == "牝"
    assert head.age == 3
    assert head.weight_to_carry == 55.0
    assert head.jockey == "松岡"
    assert "柄崎" in head.trainer


def test_all_entries_have_horse_id(shutuba_html):
    """全出走馬に horse_id と馬番が付与されていること。"""
    card = parse_race_card(shutuba_html, "202605021201")
    assert all(e.horse_id for e in card.entries)
    assert all(e.horse_number > 0 for e in card.entries)
