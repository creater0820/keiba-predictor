"""1 レースのフル予想（並列取得込み）を実走し、所要時間とリクエスト数を計測する。

並列プリフェッチを含む統合パイプライン（pipeline.predict_race）をそのまま使う。
コールド（キャッシュクリア）→ ウォーム（キャッシュ）の2回を計測する。

実行::

    python scripts/run_one_race.py
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from src import pipeline  # noqa: E402

DATE = "20260531"
VENUE = "東京"
RACE_NO = 1  # 東京1R ダ1600m 16頭
WEIGHTS = {"bias": 0.34, "pedigree": 0.33, "style": 0.33}


def _clear_http_cache() -> None:
    """http_cache を空にしてコールド状態を作る（domainテーブルは残す）。"""
    try:
        with sqlite3.connect(str(config.DB_PATH)) as conn:
            conn.execute("DELETE FROM http_cache")
    except sqlite3.OperationalError:
        pass


def _run(label: str) -> None:
    t0 = time.monotonic()
    result = pipeline.predict_race(DATE, VENUE, RACE_NO, WEIGHTS, 1.0)
    elapsed = time.monotonic() - t0
    reqs = result.meta.get("network_requests", "?")
    print(f"  [{label}] {elapsed:5.1f}秒 / 実通信 {reqs} 回 / {len(result.horses)}頭")
    return elapsed


def main() -> None:
    print(f"対象: {DATE} {VENUE} {RACE_NO}R（並列プリフェッチ={config.PARALLEL_MAX_CONCURRENT}本）")
    print("=== コールド（http_cache クリア後・並列取得）===")
    _clear_http_cache()
    cold = _run("cold")
    print("=== ウォーム（キャッシュ参照）===")
    warm = _run("warm")
    print(f"\n✅ 完了: コールド {cold:.0f}秒 → ウォーム {warm:.1f}秒")


if __name__ == "__main__":
    main()
