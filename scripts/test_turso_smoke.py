"""Turso 実接続スモークテスト（手動・任意）。

env var（TURSO_DATABASE_URL / TURSO_AUTH_TOKEN）がセットされた状態で、実際に
Turso に 1 行 insert → select → delete して往復を確認する。CI では動かさない。

実行（setup_turso.sh 実行後）::

    source .env.local 2>/dev/null; export $(grep -v '^#' .env.local | xargs)  # or 手動 export
    python scripts/test_turso_smoke.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# .env.local を読む（ローカル実行用）
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env.local")
except Exception:
    pass

from src.storage import engine as engine_mod  # noqa: E402


def main() -> None:
    creds = engine_mod.turso_credentials()
    if creds is None:
        print("❌ TURSO_DATABASE_URL / TURSO_AUTH_TOKEN が未設定です。")
        print("   先に ./scripts/setup_turso.sh を実行し、.env.local を用意してください。")
        sys.exit(1)

    from src.storage.turso_backend import TursoBackend

    print(f"接続先: {creds[0]}")
    backend = TursoBackend(creds[0], creds[1])
    print("✅ 接続成功・スキーマ確認 OK")

    # insert → select → delete
    backend.upsert_pedigree_stat(
        sire_id="SMOKE_TEST", distance_bucket="-1800", surface="dirt",
        win_rate=0.123, sample_size=99,
    )
    got = backend.get_pedigree_stat("SMOKE_TEST", "-1800", "dirt")
    assert got is not None and got.sample_size == 99, "select 失敗"
    print(f"✅ insert→select OK: win_rate={got.win_rate} sample={got.sample_size}")

    backend._execute("DELETE FROM pedigree_stats WHERE sire_id = ?", ["SMOKE_TEST"])
    gone = backend.get_pedigree_stat("SMOKE_TEST", "-1800", "dirt")
    assert gone is None, "delete 失敗"
    print("✅ delete OK")
    print("\n🎉 Turso 往復スモークテスト成功。永続キャッシュは有効です。")


if __name__ == "__main__":
    main()
