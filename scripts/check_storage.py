"""storage 層の最小動作確認スクリプト。

各テーブルへ 1 件ずつ insert し、読み出せることを確認する。
本番の data/cache.db を汚さないよう、一時ファイル DB を使う。

実行（keiba_predictor ディレクトリで）::

    python scripts/check_storage.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# プロジェクトルートを import パスに追加
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.storage import repo  # noqa: E402


def main() -> None:
    # 一時ファイルに SQLite を作る（確認後に消える）
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_url = f"sqlite:///{tmp.name}"
    engine = repo.get_engine(db_url)

    # --- スキーマ作成（create_all のみ。Alembic 不使用）---
    repo.init_db(engine)
    print(f"DB 作成: {tmp.name}")

    with repo.get_session(engine) as s:
        # --- 各テーブルに 1 件ずつ insert ---
        repo.upsert_race(
            s,
            race_id="202605021201",
            date="20260531",
            venue="東京",
            race_no=1,
            race_name="3歳未勝利",
            distance=1600,
            surface="dirt",
            course_condition="firm",
        )
        repo.upsert_horse(
            s,
            horse_id="2023103052",
            name="ベアエクスプレス",
            sex="牝",
            age=3,
            sire_id="sire_0001",
            dam_sire_id="damsire_0001",
            running_style="先行",
        )
        repo.upsert_entry(
            s,
            race_id="202605021201",
            horse_id="2023103052",
            post_position=1,
            horse_number=1,
            jockey="松岡",
            weight=55.0,
        )
        repo.upsert_track_bias(
            s,
            date="20260531",
            venue="東京",
            surface="dirt",
            inside_outside_bias=-0.3,  # やや内有利
            pace_bias=0.2,             # やや差し有利
            raw_json='{"sample_races": 5}',
        )
        repo.upsert_pedigree_stat(
            s,
            sire_id="sire_0001",
            distance_bucket="1400-1600",
            surface="dirt",
            win_rate=0.12,
            sample_size=80,
        )
        repo.add_scrape_log(
            s,
            url="https://race.netkeiba.com/race/shutuba.html?race_id=202605021201",
            status_code=200,
            etag='"abc123"',
        )
        print("各テーブルに 1 件ずつ insert 完了")

        # --- 読み出して確認 ---
        print("\n=== 読み出し結果 ===")
        race = repo.get_race(s, "202605021201")
        print(f"race        : {race.venue} {race.race_no}R {race.race_name} "
              f"{race.surface}{race.distance}m fetched_at={race.fetched_at}")

        horse = repo.get_horse(s, "2023103052")
        print(f"horse       : {horse.name}({horse.sex}{horse.age}) "
              f"脚質={horse.running_style} 父={horse.sire_id}")

        entries = repo.get_entries(s, "202605021201")
        e = entries[0]
        print(f"race_entries: {len(entries)}件 / 馬番{e.horse_number} {e.jockey} 斤量{e.weight}")

        bias = repo.get_track_bias(s, "20260531", "東京", "dirt")
        print(f"track_bias  : 内外={bias.inside_outside_bias} ペース={bias.pace_bias}")

        ped = repo.get_pedigree_stat(s, "sire_0001", "1400-1600", "dirt")
        print(f"pedigree    : 勝率{ped.win_rate} 標本{ped.sample_size}")

        # ゼロ件フォールバックの確認（未取得の血統は None が返る）
        missing = repo.get_pedigree_stat(s, "unknown_sire", "1000-1200", "turf")
        print(f"pedigree(未取得): {missing}  ← None なら初回ゼロ件でも安全")

        # 鮮度判定
        print(f"is_fresh(race): {repo.is_fresh(race.fetched_at)}  ← 取得直後なので True")

    print("\n✅ storage 層: insert→read すべて成功")
    # 後始末
    Path(tmp.name).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
