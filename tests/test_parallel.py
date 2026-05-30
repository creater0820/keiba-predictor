"""client.fetch_many（並列取得）のテスト。実ネットワークには出ない。"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from src.scraper.client import NetkeibaClient, RateLimitError  # noqa: E402


def _resp(text="<html>ok</html>", status=200):
    r = MagicMock()
    r.text = text
    r.status_code = status
    r.encoding = "utf-8"
    r.apparent_encoding = "utf-8"
    r.headers = {}
    return r


@pytest.fixture()
def client(tmp_path):
    return NetkeibaClient(db_path=str(tmp_path / "c.db"), respect_robots=False)


def test_fetch_many_gets_all_and_is_faster_than_sequential(client, monkeypatch):
    """3並列でN件取得 → 全部取れ、所要時間が逐次(N×interval)より短いこと。"""
    # テストを速くするため間隔を小さく
    monkeypatch.setattr(config, "MIN_INTERVAL_PER_WORKER", 0.1)
    monkeypatch.setattr(client._session, "get", MagicMock(return_value=_resp()))

    urls = [f"https://race.netkeiba.com/p{i}" for i in range(9)]
    start = time.monotonic()
    results = client.fetch_many(urls, max_concurrent=3)
    elapsed = time.monotonic() - start

    assert len(results) == 9
    assert all(v is not None for v in results.values())
    # 逐次なら 9×0.1=0.9s。3並列(間隔0.0333s)なら ~0.27s。明確に短いこと。
    assert elapsed < 0.9 * 0.8


def test_fetch_many_degrades_and_aborts_on_repeated_429(client, monkeypatch):
    """429 連発 → 並列度を1に落とし(degraded)、連続上限で中断(RateLimitError)。"""
    monkeypatch.setattr(config, "MAX_RETRIES", 0)          # リトライ無しで速く
    monkeypatch.setattr(config, "RATELIMIT_COOLDOWN_SEC", 0)
    monkeypatch.setattr(config, "MIN_INTERVAL_PER_WORKER", 0.01)
    monkeypatch.setattr("src.scraper.client.time.sleep", lambda *a: None)
    monkeypatch.setattr(client._session, "get", MagicMock(return_value=_resp(status=429)))

    urls = [f"https://race.netkeiba.com/x{i}" for i in range(5)]
    with pytest.raises(RateLimitError):
        client.fetch_many(urls, max_concurrent=3)

    # 429 を受けて並列度を落とした（degrade）状態になっていること
    assert client.degraded is True


def test_fetch_many_one_429_among_ok_returns_none_for_that_url(client, monkeypatch):
    """一部だけ 429 → その URL は None、他は取得成功。全体は止まらない。"""
    monkeypatch.setattr(config, "MAX_RETRIES", 0)
    monkeypatch.setattr(config, "RATELIMIT_COOLDOWN_SEC", 0)
    monkeypatch.setattr(config, "MIN_INTERVAL_PER_WORKER", 0.01)
    monkeypatch.setattr("src.scraper.client.time.sleep", lambda *a: None)

    bad = "https://race.netkeiba.com/bad"

    def fake_get(url, **kw):
        return _resp(status=429) if url == bad else _resp("<html>good</html>")

    monkeypatch.setattr(client._session, "get", MagicMock(side_effect=fake_get))

    urls = [f"https://race.netkeiba.com/g{i}" for i in range(3)] + [bad]
    results = client.fetch_many(urls, max_concurrent=3)
    assert results[bad] is None
    assert all(results[u] is not None for u in urls if u != bad)


def test_fetch_many_cache_hit_zero_network(client, monkeypatch):
    """キャッシュ済み URL の再取得は実通信ゼロであること。"""
    monkeypatch.setattr(config, "MIN_INTERVAL_PER_WORKER", 0.01)
    mock_get = MagicMock(return_value=_resp())
    monkeypatch.setattr(client._session, "get", mock_get)

    urls = [f"https://race.netkeiba.com/c{i}" for i in range(4)]
    client.set_request_budget(None)
    client.fetch_many(urls, max_concurrent=3)      # 1回目: 4件ネットワーク
    first = client.network_count
    assert first == 4

    client.fetch_many(urls, max_concurrent=3)      # 2回目: 全部キャッシュ
    assert client.network_count == first           # 増えない（実通信ゼロ）


def test_request_budget_hard_cap(client, monkeypatch):
    """リクエスト上限を超えると個別取得が失敗し None になること（全体は止めない）。"""
    monkeypatch.setattr(config, "MIN_INTERVAL_PER_WORKER", 0.01)
    monkeypatch.setattr(client._session, "get", MagicMock(return_value=_resp()))

    client.set_request_budget(2)  # 2 件まで
    urls = [f"https://race.netkeiba.com/b{i}" for i in range(5)]
    results = client.fetch_many(urls, max_concurrent=1)  # 逐次で上限判定を安定させる
    ok = [v for v in results.values() if v is not None]
    assert len(ok) == 2  # 2 件だけ成功、残りは上限超過で None
