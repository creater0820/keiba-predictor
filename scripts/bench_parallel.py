"""並列化の Before/After ベンチマーク。

ダービー18頭の「血統ページ + 過去走ページ」(計36URL)という支配的なワークロードで、
逐次取得 vs 3並列取得 の所要時間・リクエスト数を実測して比較する。

公平を期すため各計測の直前に対象URLのキャッシュをクリアし、同じ条件で測る。
実通信は 36×2 = 約72リクエスト（レート制限厳守）。

実行::

    python scripts/bench_parallel.py
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from src.scraper import pedigree, running_style  # noqa: E402
from src.scraper.client import NetkeibaClient  # noqa: E402
from src.scraper.race_card import fetch_race_card  # noqa: E402

DERBY_RACE_ID = "202605021211"


def _clear_cache(urls: list[str]) -> None:
    """対象 URL をキャッシュから削除（コールド状態を作る）。"""
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        conn.executemany("DELETE FROM http_cache WHERE url = ?", [(u,) for u in urls])


def main() -> None:
    client = NetkeibaClient()

    # 出走表（キャッシュ利用）から18頭のhorse_idを得る
    card = fetch_race_card(client, DERBY_RACE_ID)
    urls: list[str] = []
    for e in card.entries:
        urls.append(pedigree.PED_URL.format(horse_id=e.horse_id))
        urls.append(running_style.RESULT_PAGE_URL.format(horse_id=e.horse_id))
    print(f"対象: ダービー{len(card.entries)}頭 / {len(urls)} URL（血統+過去走）")

    # --- 逐次（Before）---
    _clear_cache(urls)
    client.set_request_budget(None)
    n0 = client.network_count
    t0 = time.monotonic()
    for u in urls:
        try:
            client.fetch(u)
        except Exception:
            pass
    seq_time = time.monotonic() - t0
    seq_reqs = client.network_count - n0

    # --- 3並列（After）---
    _clear_cache(urls)
    n1 = client.network_count
    t1 = time.monotonic()
    client.fetch_many(urls, max_concurrent=3)
    par_time = time.monotonic() - t1
    par_reqs = client.network_count - n1

    client.close()

    speedup = seq_time / par_time if par_time > 0 else 0.0
    reduction = (1 - par_time / seq_time) * 100 if seq_time > 0 else 0.0

    print("\n=== ベンチ結果（同一36URLワークロード）===")
    print(f"{'方式':<12}{'所要(秒)':>10}{'リクエスト':>10}{'並列度':>8}")
    print(f"{'逐次(Before)':<12}{seq_time:>10.1f}{seq_reqs:>10}{1:>8}")
    print(f"{'並列(After)':<12}{par_time:>10.1f}{par_reqs:>10}{3:>8}")
    print(f"\n→ {reduction:.0f}% 短縮（{speedup:.1f}倍速）")
    print(f"  逐次 {seq_time/len(urls):.2f}s/req → 並列 {par_time/len(urls):.2f}s/req")


if __name__ == "__main__":
    main()
