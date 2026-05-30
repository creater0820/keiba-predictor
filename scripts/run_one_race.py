"""1 レース分のフル取得（トラックバイアス + 全頭の血統）を実走し、
所要時間とネットワークリクエスト数を計測するスクリプト。

本番 DB を汚さないよう一時 DB を使う（コールド状態から計測）。
2 回目はキャッシュが効き追加リクエスト 0 になることも確認する。

実行::

    python scripts/run_one_race.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.scraper import pedigree, track_bias  # noqa: E402
from src.scraper.client import NetkeibaClient  # noqa: E402
from src.scraper.race_card import fetch_race_card  # noqa: E402
from src.scraper.race_list import fetch_kaisai_dates  # noqa: E402
from src.storage import repo  # noqa: E402

TARGET_RACE_ID = "202605021201"  # 2026/5/31 東京1R ダ1600m
TARGET_DATE = "20260531"
VENUE = "東京"
SURFACE = "dirt"


class CountingClient(NetkeibaClient):
    """実ネットワーク取得の回数を数えるためのサブクラス。"""

    network_calls = 0

    def _fetch_with_retry(self, url: str):  # type: ignore[override]
        CountingClient.network_calls += 1
        return super()._fetch_with_retry(url)


def run_full_fetch(client, session) -> dict:
    """1 レースのフル取得を実行し、結果サマリを返す。"""
    dates = fetch_kaisai_dates(client)

    # 1) 出走表（16 頭）
    card = fetch_race_card(client, TARGET_RACE_ID)

    # 2) トラックバイアス（当日→前日→中立のフォールバック）
    bias = track_bias.compute_track_bias(
        client, session, TARGET_DATE, VENUE, SURFACE, dates
    )

    # 3) 全頭の血統（父・母父）＋ 種牡馬距離別成績（sire 単位キャッシュ）
    sires_fetched = set()
    for e in card.entries:
        ids = pedigree.fetch_pedigree_ids(client, e.horse_id)
        for sid in (ids.sire_id, ids.dam_sire_id):
            if sid and sid not in sires_fetched:
                pedigree.ensure_sire_stats(client, session, sid)
                sires_fetched.add(sid)

    return {
        "horses": len(card.entries),
        "bias": bias,
        "unique_sires": len(sires_fetched),
    }


def main() -> None:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    engine = repo.get_engine(f"sqlite:///{tmp.name}")
    repo.init_db(engine)

    client = CountingClient(db_path=tmp.name)

    # ===== 1 回目: コールド（実取得） =====
    print(f"対象: {TARGET_DATE} {VENUE} {TARGET_RACE_ID}（ダ1600m）")
    print("=== 1回目（コールド: 実ネットワーク取得）===")
    CountingClient.network_calls = 0
    t0 = time.monotonic()
    with repo.get_session(engine) as s:
        summary = run_full_fetch(client, s)
    elapsed = time.monotonic() - t0
    cold_calls = CountingClient.network_calls

    print(f"  出走頭数        : {summary['horses']} 頭")
    print(f"  ユニーク種牡馬数 : {summary['unique_sires']} 頭（父＋母父の実数）")
    b = summary["bias"]
    print(f"  トラックバイアス : 内外={b.inside_outside_bias} ペース={b.pace_bias}")
    print(f"                    {b.describe()}")
    print(f"  ネット取得回数  : {cold_calls} 回")
    print(f"  所要時間        : {elapsed:.1f} 秒")
    if cold_calls:
        print(f"  1回あたり平均   : {elapsed / cold_calls:.2f} 秒/req")

    # ===== 2 回目: ウォーム（キャッシュ） =====
    print("\n=== 2回目（ウォーム: キャッシュ参照）===")
    CountingClient.network_calls = 0
    t0 = time.monotonic()
    with repo.get_session(engine) as s:
        run_full_fetch(client, s)
    elapsed2 = time.monotonic() - t0
    warm_calls = CountingClient.network_calls
    print(f"  ネット取得回数  : {warm_calls} 回（0 なら完全キャッシュ）")
    print(f"  所要時間        : {elapsed2:.2f} 秒")

    client.close()
    Path(tmp.name).unlink(missing_ok=True)

    print("\n✅ 1レース・フル取得の計測完了")
    print(f"   → コールド {cold_calls}req/{elapsed:.0f}s, ウォーム {warm_calls}req/{elapsed2:.2f}s")


if __name__ == "__main__":
    main()
