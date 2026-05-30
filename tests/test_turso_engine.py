"""storage/engine.py の Turso 切替テスト（実接続せずモックで検証）。"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.storage import engine as engine_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_turso_env(monkeypatch):
    """各テスト開始時に Turso 環境変数を消す（他テストの影響を排除）。"""
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)


def test_no_env_falls_back_to_local_sqlite(monkeypatch):
    """環境変数が無ければ Turso URL は None（＝ローカルSQLite）。"""
    assert engine_mod.build_turso_url() is None
    assert "SQLite" in engine_mod.describe_backend()


def test_build_turso_url_from_env(monkeypatch):
    """環境変数がそろえば sqlite+libsql:// の接続URLが組まれること。"""
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://keiba-predictor-yasu.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "ey.TESTTOKEN.zz")
    url = engine_mod.build_turso_url()
    assert url is not None
    assert url.startswith("sqlite+libsql://keiba-predictor-yasu.turso.io")
    assert "authToken=ey.TESTTOKEN.zz" in url
    assert "secure=true" in url
    # libsql:// プレフィックスは除去されている
    assert "libsql://keiba" not in url.replace("sqlite+libsql://", "")


def test_partial_env_is_ignored(monkeypatch):
    """URL だけ / TOKEN だけ では Turso を使わない（両方必須）。"""
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://x.turso.io")
    assert engine_mod.build_turso_url() is None
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "tok")
    assert engine_mod.build_turso_url() is None


def test_create_app_engine_uses_turso_when_available(monkeypatch):
    """Turso 設定＆方言ありのとき、Turso URL で create_engine が呼ばれること。"""
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://db.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "tok123")
    # 方言は「ある」とみなし、create_engine をモックして接続はしない
    monkeypatch.setattr(engine_mod, "_libsql_dialect_available", lambda: True)
    fake_engine = MagicMock(name="engine")
    captured = {}

    def fake_create_engine(url, **kw):
        captured["url"] = url
        return fake_engine

    monkeypatch.setattr(engine_mod, "create_engine", fake_create_engine)

    eng = engine_mod.create_app_engine()
    assert eng is fake_engine
    assert captured["url"].startswith("sqlite+libsql://db.turso.io")
    assert "authToken=tok123" in captured["url"]


def test_create_app_engine_falls_back_without_dialect(monkeypatch):
    """Turso 設定済みでも libSQL 方言が無ければローカルSQLiteにフォールバック。"""
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://db.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "tok123")
    monkeypatch.setattr(engine_mod, "_libsql_dialect_available", lambda: False)
    captured = {}

    def fake_create_engine(url, **kw):
        captured["url"] = url
        return MagicMock()

    monkeypatch.setattr(engine_mod, "create_engine", fake_create_engine)
    engine_mod.create_app_engine()
    # ローカル SQLite の URL（sqlite:///...）が使われること
    assert captured["url"].startswith("sqlite:///")
    assert "libsql" not in captured["url"]
