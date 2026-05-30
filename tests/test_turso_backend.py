"""turso_backend.py のテスト。実 Turso には接続せず、libsql クライアントをモック。"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.storage import repo  # noqa: E402
from src.storage.turso_backend import (  # noqa: E402
    TursoBackend,
    schema_discrepancies,
)


class FakeRow(tuple):
    """libsql_client.Row 互換のダミー（asdict を持つ tuple）。"""

    def __new__(cls, d: dict):
        obj = super().__new__(cls, tuple(d.values()))
        obj._d = d
        return obj

    def asdict(self):
        return dict(self._d)


def _result(rows=None, rows_affected=0):
    rs = MagicMock()
    rs.rows = rows or []
    rs.rows_affected = rows_affected
    rs.columns = []
    return rs


@pytest.fixture()
def fake_client():
    """execute を記録するダミークライアント。"""
    client = MagicMock()
    client.execute = MagicMock(return_value=_result())
    return client


@pytest.fixture()
def backend(fake_client):
    # client を注入 → 実接続しない。__init__ で _ensure_schema が走る。
    return TursoBackend(url="libsql://x", auth_token="t", client=fake_client)


def test_ensure_schema_runs_create_table(backend, fake_client):
    """初期化時に CREATE TABLE が複数回実行されること。"""
    creates = [c for c in fake_client.execute.call_args_list
               if "CREATE TABLE" in c.args[0]]
    assert len(creates) >= 6  # 6 ドメインテーブル


def test_upsert_race_builds_upsert_sql(backend, fake_client):
    """upsert_race が INSERT ... ON CONFLICT を正しいパラメータで実行すること。"""
    fake_client.execute.reset_mock()
    backend.upsert_race(race_id="R1", date="20260531", venue="東京", race_no=11)
    sql, params = fake_client.execute.call_args.args
    assert sql.startswith("INSERT INTO races")
    assert "ON CONFLICT(race_id) DO UPDATE SET" in sql
    assert "R1" in params and "東京" in params
    # fetched_at が自動付与される
    assert any(isinstance(p, str) and "T" in p for p in params)


def test_get_race_returns_model_with_datetime(backend, fake_client):
    """get_race が Race モデル（fetched_at は datetime）を返すこと。"""
    from datetime import datetime

    row = FakeRow({
        "race_id": "R1", "date": "20260531", "venue": "東京", "race_no": 11,
        "race_name": "日本ダービー", "distance": 2400, "surface": "turf",
        "course_condition": "firm", "fetched_at": "2026-05-31T10:00:00",
    })
    fake_client.execute.return_value = _result(rows=[row])
    race = backend.get_race("R1")
    assert race is not None
    assert race.venue == "東京"
    assert race.distance == 2400
    assert isinstance(race.fetched_at, datetime)  # TEXT → datetime に復元


def test_get_race_none_when_empty(backend, fake_client):
    """該当行が無ければ None。"""
    fake_client.execute.return_value = _result(rows=[])
    assert backend.get_race("missing") is None


def test_has_pedigree_for_sire(backend, fake_client):
    """1 行でもあれば True、無ければ False。"""
    fake_client.execute.return_value = _result(rows=[FakeRow({"1": 1})])
    assert backend.has_pedigree_for_sire("S1") is True
    fake_client.execute.return_value = _result(rows=[])
    assert backend.has_pedigree_for_sire("S1") is False


def test_pedigree_stat_roundtrip_shape(backend, fake_client):
    """get_pedigree_stat が PedigreeStat（win_rate/sample_size）を返すこと。"""
    row = FakeRow({
        "sire_id": "S1", "distance_bucket": "-1800", "surface": "dirt",
        "win_rate": 0.068, "sample_size": 729, "fetched_at": "2026-05-31T10:00:00",
    })
    fake_client.execute.return_value = _result(rows=[row])
    st = backend.get_pedigree_stat("S1", "-1800", "dirt")
    assert st.win_rate == 0.068
    assert st.sample_size == 729


def test_transaction_commits(backend, fake_client):
    """transaction() が BEGIN→COMMIT を発行すること。"""
    fake_client.execute.reset_mock()
    with backend.transaction():
        backend.upsert_horse(horse_id="H1", name="テスト")
    sqls = [c.args[0] for c in fake_client.execute.call_args_list]
    assert "BEGIN" in sqls
    assert "COMMIT" in sqls


def test_transaction_rolls_back_on_error(backend, fake_client):
    """例外時に ROLLBACK が発行されること。"""
    fake_client.execute.reset_mock()
    with pytest.raises(ValueError):
        with backend.transaction():
            raise ValueError("boom")
    sqls = [c.args[0] for c in fake_client.execute.call_args_list]
    assert "ROLLBACK" in sqls


def test_schema_parity_no_discrepancies():
    """models.py と ddl.sql のテーブル/列が一致していること（差異ゼロ）。"""
    assert schema_discrepancies() == []


# --- repo 層が Turso backend に委譲することの確認（モック注入）---
def test_repo_delegates_to_turso_backend(monkeypatch):
    """Turso が有効なとき repo.upsert_race が backend に委譲し session を使わないこと。"""
    fake_backend = MagicMock()
    monkeypatch.setattr(repo, "_active_backend", lambda: fake_backend)

    sentinel_session = MagicMock()  # 使われてはいけない
    repo.upsert_race(sentinel_session, race_id="R1", venue="東京")
    fake_backend.upsert_race.assert_called_once_with(race_id="R1", venue="東京")
    sentinel_session.merge.assert_not_called()  # SQLAlchemy 経路は通らない


def test_repo_fallback_to_sqlalchemy_when_no_backend(monkeypatch, tmp_path):
    """Turso 無効（None）なら従来の SQLAlchemy 経路を通ること。"""
    monkeypatch.setattr(repo, "_active_backend", lambda: None)
    engine = repo.get_engine(f"sqlite:///{tmp_path / 't.db'}")
    repo.init_db(engine)
    with repo.get_session(engine) as s:
        repo.upsert_race(s, race_id="R1", date="20260531", venue="東京", race_no=11)
        got = repo.get_race(s, "R1")
    assert got is not None and got.venue == "東京"
