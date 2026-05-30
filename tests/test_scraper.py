"""scraper/client.py のオフライン単体テスト。

実際の netkeiba にはアクセスせず、requests のセッションを差し替えて
（モックして）クライアントの「マナー」ロジックだけを検証する。

実行方法（keiba_predictor ディレクトリで）::

    pytest -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# プロジェクトルートを import パスに追加（config / src を読めるようにする）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.scraper.client import NetkeibaClient  # noqa: E402


def _make_response(text: str = "<html>ok</html>", status: int = 200) -> MagicMock:
    """requests.Response の代わりに使う偽オブジェクトを作る。"""
    resp = MagicMock()
    resp.text = text
    resp.status_code = status
    resp.encoding = "utf-8"
    resp.apparent_encoding = "utf-8"
    resp.headers = {}
    return resp


@pytest.fixture()
def client(tmp_path) -> NetkeibaClient:
    """テスト用クライアント。

    - キャッシュ DB は pytest の一時ディレクトリに作る（後始末不要）
    - robots.txt 確認は無効化（ネットワークに出ないため）
    """
    return NetkeibaClient(
        db_path=str(tmp_path / "test_cache.db"),
        respect_robots=False,
    )


def test_fetch_returns_html(client, monkeypatch):
    """1 回目の fetch でネットワーク取得され、HTML が返ること。"""
    mock_get = MagicMock(return_value=_make_response("<html>race</html>"))
    monkeypatch.setattr(client._session, "get", mock_get)
    # レート制限の待機は実時間を消費するので無効化（ロジックは別テストで確認）
    monkeypatch.setattr(client, "_respect_rate_limit", lambda: None)

    html = client.fetch("https://yoso.netkeiba.com/?pid=race_list")

    assert "race" in html
    assert mock_get.call_count == 1  # ネットワークに 1 回だけ出た


def test_second_fetch_uses_cache(client, monkeypatch):
    """同じ URL を 2 回 fetch しても、ネットワークアクセスは 1 回だけのこと。"""
    mock_get = MagicMock(return_value=_make_response("<html>cached</html>"))
    monkeypatch.setattr(client._session, "get", mock_get)
    monkeypatch.setattr(client, "_respect_rate_limit", lambda: None)

    url = "https://yoso.netkeiba.com/?pid=race_card"
    first = client.fetch_detailed(url)
    second = client.fetch_detailed(url)

    assert first.from_cache is False  # 1 回目はネットワーク
    assert second.from_cache is True  # 2 回目はキャッシュ
    assert mock_get.call_count == 1   # ネットワークアクセスは 1 回のみ


def test_force_refresh_bypasses_cache(client, monkeypatch):
    """force_refresh=True ならキャッシュを無視して再取得すること。"""
    mock_get = MagicMock(return_value=_make_response())
    monkeypatch.setattr(client._session, "get", mock_get)
    monkeypatch.setattr(client, "_respect_rate_limit", lambda: None)

    url = "https://yoso.netkeiba.com/?pid=force"
    client.fetch(url)                       # 1 回目
    client.fetch(url, force_refresh=True)   # 強制再取得

    assert mock_get.call_count == 2


def test_retry_on_temporary_error(client, monkeypatch):
    """503 など一時エラーはリトライし、最終的に成功を返すこと。"""
    responses = [
        _make_response(status=503),         # 1 回目: 一時エラー
        _make_response("<html>ok</html>"),  # 2 回目: 成功
    ]
    mock_get = MagicMock(side_effect=responses)
    monkeypatch.setattr(client._session, "get", mock_get)
    monkeypatch.setattr(client, "_respect_rate_limit", lambda: None)
    # バックオフの待機も無効化（テストを速く保つ）
    monkeypatch.setattr("src.scraper.client.time.sleep", lambda _s: None)

    html = client.fetch("https://yoso.netkeiba.com/?pid=retry")

    assert "ok" in html
    assert mock_get.call_count == 2


def test_empty_charset_falls_back_to_apparent_encoding(client, monkeypatch):
    """Content-Type の charset が空（encoding='')でも apparent_encoding で復号すること。

    netkeiba の一部ページ（shutuba 等）は charset= を空で返すため、
    そのままだと文字化けする。client がこれを検出して直すかを検証する。
    """
    resp = _make_response("<html>出走表</html>")
    resp.encoding = ""              # 空 charset を再現
    resp.apparent_encoding = "EUC-JP"
    mock_get = MagicMock(return_value=resp)
    monkeypatch.setattr(client._session, "get", mock_get)
    monkeypatch.setattr(client, "_respect_rate_limit", lambda: None)

    client.fetch("https://race.netkeiba.com/race/shutuba.html?race_id=1")

    # 空エンコーディングを apparent_encoding に切り替えたこと
    assert resp.encoding == "EUC-JP"


def test_client_factory_creates_db(client):
    """初期化時にキャッシュ DB（http_cache テーブル）が作られること。"""
    import sqlite3

    with sqlite3.connect(client._db_path) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='http_cache'"
        ).fetchone()
    assert row is not None
