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
    """3並列が 1並列より明確に速いこと（マシン速度に依存しない相対比較）。"""
    monkeypatch.setattr(config, "MIN_INTERVAL_PER_WORKER", 0.1)
    monkeypatch.setattr(client._session, "get", MagicMock(return_value=_resp()))

    # 1並列（実効 0.1s/req）の所要時間
    urls_seq = [f"https://race.netkeiba.com/s{i}" for i in range(9)]
    t = time.monotonic()
    r_seq = client.fetch_many(urls_seq, max_concurrent=1)
    seq_time = time.monotonic() - t

    # 3並列（実効 0.033s/req）の所要時間（別URLでキャッシュ干渉なし）
    urls_par = [f"https://race.netkeiba.com/p{i}" for i in range(9)]
    t = time.monotonic()
    r_par = client.fetch_many(urls_par, max_concurrent=3)
    par_time = time.monotonic() - t

    assert len(r_seq) == 9 and all(v is not None for v in r_seq.values())
    assert len(r_par) == 9 and all(v is not None for v in r_par.values())
    # 3並列は1並列より明確に速い（理論 1/3、余裕を見て 0.6 倍未満）
    assert par_time < seq_time * 0.6


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
