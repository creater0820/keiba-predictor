"""storage/engine.py（Turso 認証情報の判定・ローカルエンジン生成）のテスト。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.storage import engine as engine_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_turso_env(monkeypatch):
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)


def test_no_env_means_no_turso():
    """環境変数が無ければ Turso 認証情報は None（＝ローカルSQLite）。"""
    assert engine_mod.turso_credentials() is None


def test_both_env_returns_credentials(monkeypatch):
    """URL と TOKEN がそろえば (url, token) を返すこと。"""
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://keiba-yasu.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "ey.TESTTOKEN.zz")
    creds = engine_mod.turso_credentials()
    assert creds == ("libsql://keiba-yasu.turso.io", "ey.TESTTOKEN.zz")


def test_partial_env_is_ignored(monkeypatch):
    """URL だけ / TOKEN だけ では None（両方必須）。"""
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://x.turso.io")
    assert engine_mod.turso_credentials() is None
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "tok")
    assert engine_mod.turso_credentials() is None


def test_create_local_engine_is_sqlite():
    """create_local_engine がローカル SQLite エンジンを返すこと。"""
    eng = engine_mod.create_local_engine()
    assert eng.url.get_backend_name() == "sqlite"


def test_describe_backend_local_when_no_env():
    """env 未設定なら repo.describe_backend はローカルSQLite。"""
    from src.storage import repo
    assert repo.describe_backend() == "ローカルSQLite"
