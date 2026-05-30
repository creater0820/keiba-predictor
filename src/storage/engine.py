"""DB エンジンの生成を一手に担う。Turso（libSQL）と ローカル SQLite を切替。

優先順位:
    1. TURSO_DATABASE_URL と TURSO_AUTH_TOKEN が両方そろっていれば Turso（永続）
       - Streamlit Cloud では st.secrets、ローカルでは環境変数（.env.local）から読む
    2. それ以外、または libSQL ドライバが無い場合は ローカル SQLite にフォールバック

これにより「env var 未設定なら従来どおりローカルで動く」を保証する。
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

import config


def _read_secret(key: str) -> str | None:
    """設定値を Streamlit secrets → 環境変数 の順で読む。

    Streamlit 環境でなくても、secrets ファイルが無くても落ちないよう全体を保護。
    """
    # 1) Streamlit secrets（クラウド）
    try:
        import streamlit as st  # 遅延 import（非 Streamlit 環境でも動くように）

        if hasattr(st, "secrets"):
            try:
                if key in st.secrets:
                    return str(st.secrets[key])
            except Exception:
                # secrets.toml が無い場合 `in` で例外が出ることがある → 無視
                pass
    except Exception:
        pass
    # 2) 環境変数（ローカル .env.local 等）
    return os.getenv(key)


def build_turso_url() -> str | None:
    """Turso の接続情報がそろっていれば SQLAlchemy 用 URL を返す。無ければ None。

    libSQL の SQLAlchemy 方言 `sqlite+libsql://` を使う。
    """
    url = _read_secret("TURSO_DATABASE_URL")
    token = _read_secret("TURSO_AUTH_TOKEN")
    if not url or not token:
        return None

    # libsql://host や https://host → host 部分を取り出す
    host = url.replace("libsql://", "").replace("https://", "").replace("http://", "").strip("/")
    return f"sqlite+libsql://{host}?authToken={token}&secure=true"


def _libsql_dialect_available() -> bool:
    """libSQL の SQLAlchemy 方言が import 可能か。"""
    try:
        import sqlalchemy_libsql  # noqa: F401
        return True
    except Exception:
        return False


def describe_backend() -> str:
    """現在使われるバックエンドの説明（UI/ログ表示用）。"""
    if build_turso_url() is not None and _libsql_dialect_available():
        return "Turso（libSQL・永続キャッシュ）"
    if build_turso_url() is not None:
        return "ローカルSQLite（Turso 設定済みだが libSQL ドライバ未導入のためフォールバック）"
    return "ローカルSQLite"


def create_app_engine() -> Engine:
    """アプリ用エンジンを生成する（Turso 優先・無ければローカル SQLite）。"""
    turso = build_turso_url()
    if turso is not None and _libsql_dialect_available():
        try:
            return create_engine(turso, future=True)
        except Exception:
            # 接続文字列生成・方言ロードで失敗してもローカルにフォールバック
            pass

    config.ensure_dirs()
    return create_engine(config.DB_URL, future=True)
