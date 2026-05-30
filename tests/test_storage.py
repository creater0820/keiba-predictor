"""storage 層（models.py / repo.py）の単体テスト。

一時ファイル DB を使うので本番 data/cache.db には触れない。

実行::

    pytest -v tests/test_storage.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.storage import repo  # noqa: E402


@pytest.fixture()
def session(tmp_path):
    """一時 DB を作り、テーブル作成済みのセッションを渡す。"""
    engine = repo.get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    repo.init_db(engine)
    with repo.get_session(engine) as s:
        yield s


def test_race_roundtrip(session):
    """races の insert→read が一致すること。"""
    repo.upsert_race(
        session, race_id="R1", date="20260531", venue="東京", race_no=1,
        race_name="3歳未勝利", distance=1600, surface="dirt", course_condition="firm",
    )
    got = repo.get_race(session, "R1")
    assert got is not None
    assert got.venue == "東京"
    assert got.distance == 1600
    assert got.fetched_at is not None  # 自動付与される


def test_horse_and_entry_roundtrip(session):
    """horses と race_entries が保存・取得できること。"""
    repo.upsert_race(session, race_id="R1", date="20260531", venue="東京", race_no=1)
    repo.upsert_horse(session, horse_id="H1", name="テスト馬", sex="牝", age=3)
    repo.upsert_entry(
        session, race_id="R1", horse_id="H1",
        post_position=1, horse_number=5, jockey="松岡", weight=55.0,
    )
    entries = repo.get_entries(session, "R1")
    assert len(entries) == 1
    assert entries[0].horse_number == 5
    assert entries[0].weight == 55.0


def test_upsert_updates_existing(session):
    """同じ主キーで再 upsert すると更新されること（重複行が増えない）。"""
    repo.upsert_horse(session, horse_id="H1", name="旧名", running_style="逃げ")
    repo.upsert_horse(session, horse_id="H1", name="新名", running_style="差し")
    got = repo.get_horse(session, "H1")
    assert got.name == "新名"
    assert got.running_style == "差し"


def test_track_bias_roundtrip(session):
    """track_bias_daily が複合主キーで保存・取得できること。"""
    repo.upsert_track_bias(
        session, date="20260531", venue="東京", surface="dirt",
        inside_outside_bias=-0.3, pace_bias=0.2, raw_json="{}",
    )
    got = repo.get_track_bias(session, "20260531", "東京", "dirt")
    assert got is not None
    assert got.inside_outside_bias == -0.3


def test_pedigree_zero_rows_returns_none(session):
    """初回ゼロ件: 未取得の血統統計は None を返すこと（フォールバック前提）。"""
    assert repo.get_pedigree_stat(session, "no_sire", "1400-1600", "dirt") is None


def test_pedigree_roundtrip(session):
    """血統統計の insert→read。"""
    repo.upsert_pedigree_stat(
        session, sire_id="S1", distance_bucket="1400-1600", surface="dirt",
        win_rate=0.12, sample_size=80,
    )
    got = repo.get_pedigree_stat(session, "S1", "1400-1600", "dirt")
    assert got.win_rate == 0.12
    assert got.sample_size == 80


def test_scrape_log_is_append_only(session):
    """scrape_log は同じ URL でも追記され、行が増えること（監査ログ）。"""
    repo.add_scrape_log(session, url="http://x", status_code=200)
    repo.add_scrape_log(session, url="http://x", status_code=200)
    from src.storage.models import ScrapeLog
    from sqlalchemy import select

    rows = list(session.scalars(select(ScrapeLog)))
    assert len(rows) == 2  # 上書きされず 2 件残る


def test_is_fresh(session):
    """is_fresh: None は False、取得直後は True。"""
    from datetime import datetime, timedelta

    assert repo.is_fresh(None) is False
    assert repo.is_fresh(datetime.now()) is True
    assert repo.is_fresh(datetime.now() - timedelta(hours=48)) is False
