"""DB バックエンドの判定と、ローカル SQLite エンジンの生成。

Turso（libSQL）は SQLAlchemy 方言（Rust ビルド要）ではなく、純 Python の
`libsql-client` を直接使う（turso_backend.py）。ここでは「Turso を使うべきか」の
判定材料（認証情報の読み取り）と、フォールバック先のローカル SQLite エンジンの
生成だけを担当する。
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

import config


def _read_secret(key: str) -> str | None:
    """設定値を Streamlit secrets → 環境変数 の順で読む（非 Streamlit 環境でも安全）。"""
    # 1) Streamlit secrets（クラウド）
    try:
        import streamlit as st  # 遅延 import

        if hasattr(st, "secrets"):
            try:
                val = st.secrets.get(key)  # type: ignore[attr-defined]
                if val:
                    return str(val)
            except Exception:
                # secrets.toml が無い等で例外になることがある → 無視して環境変数へ
                pass
    except Exception:
        pass
    # 2) 環境変数（ローカル .env.local 等）
    return os.getenv(key)


def turso_credentials() -> tuple[str, str] | None:
    """Turso の (URL, トークン) がそろっていれば返す。片方でも欠ければ None。"""
    url = _read_secret("TURSO_DATABASE_URL")
    token = _read_secret("TURSO_AUTH_TOKEN")
    if url and token:
        return (url, token)
    return None


def create_local_engine(db_url: str | None = None) -> Engine:
    """ローカル SQLite の SQLAlchemy エンジンを返す（フォールバック既定）。"""
    if db_url is None:
        config.ensure_dirs()
        db_url = config.DB_URL
    return create_engine(db_url, future=True)
