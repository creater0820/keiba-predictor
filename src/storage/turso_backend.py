"""Turso（libSQL）バックエンド。純 Python の libsql-client を直接使う。

`sqlalchemy-libsql`（Rust ビルド要）を避け、HTTP/WebSocket ベースの
`libsql-client` で生 SQL を実行する。Streamlit Cloud でも導入できる。

repo.py の各公開関数と同じ意味のメソッドを提供し、戻り値は SQLAlchemy の
モデルインスタンス（transient）にそろえる。これにより呼び出し側
（属性アクセス）を一切変えずに差し替えられる。
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from src.storage.models import (
    Horse,
    PedigreeStat,
    Race,
    RaceEntry,
    ScrapeLog,
    TrackBiasDaily,
)

DDL_PATH = Path(__file__).resolve().parent / "ddl.sql"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_dt(value) -> datetime | None:
    """fetched_at の TEXT を datetime に戻す（None/空は None）。"""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


class TursoBackend:
    """libsql-client で Turso に読み書きする薄いラッパ。

    repo.py から委譲される。スレッド安全のため execute はロックで直列化する
    （ドメイン更新は基本的に逐次だが、念のため）。
    """

    def __init__(self, url: str, auth_token: str, client=None) -> None:
        """接続して（必要なら）スキーマを用意する。

        Args:
            url: Turso の libsql:// URL。
            auth_token: 認証トークン。
            client: テスト用に差し込むクライアント（None なら実接続）。
        """
        self._lock = threading.Lock()
        if client is not None:
            self._client = client
        else:
            from libsql_client import create_client_sync

            self._client = create_client_sync(url=url, auth_token=auth_token)
        self._ensure_schema()

    # ------------------------------------------------------------------
    # 低レベル
    # ------------------------------------------------------------------
    def _execute(self, sql: str, params: list | None = None):
        """1 文を実行して ResultSet を返す（ロックで直列化）。"""
        with self._lock:
            return self._client.execute(sql, params or [])

    def _ensure_schema(self) -> None:
        """ddl.sql の各文を実行してテーブルを用意する。"""
        ddl = DDL_PATH.read_text(encoding="utf-8")
        for stmt in _split_sql(ddl):
            self._execute(stmt)

    @contextmanager
    def transaction(self):
        """BEGIN/COMMIT/ROLLBACK でまとめて更新する（バッチ整合性用）。"""
        self._execute("BEGIN")
        try:
            yield
        except Exception:
            self._execute("ROLLBACK")
            raise
        else:
            self._execute("COMMIT")

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # upsert（書き込み）
    # ------------------------------------------------------------------
    def _upsert(self, table: str, pk_cols: list[str], fields: dict) -> None:
        """INSERT ... ON CONFLICT(pk) DO UPDATE で 1 行 upsert する。"""
        data = dict(fields)
        data.setdefault("fetched_at", _now_iso())
        cols = list(data.keys())
        placeholders = ", ".join("?" for _ in cols)
        col_list = ", ".join(cols)
        # 主キー以外を更新対象にする
        update_cols = [c for c in cols if c not in pk_cols]
        set_clause = ", ".join(f"{c}=excluded.{c}" for c in update_cols) or \
            f"{pk_cols[0]}={pk_cols[0]}"  # 更新列が無い場合のダミー
        sql = (
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT({', '.join(pk_cols)}) DO UPDATE SET {set_clause}"
        )
        self._execute(sql, [data[c] for c in cols])

    def upsert_race(self, **fields) -> None:
        self._upsert("races", ["race_id"], fields)

    def upsert_horse(self, **fields) -> None:
        self._upsert("horses", ["horse_id"], fields)

    def upsert_entry(self, **fields) -> None:
        self._upsert("race_entries", ["race_id", "horse_id"], fields)

    def upsert_track_bias(self, **fields) -> None:
        self._upsert("track_bias_daily", ["date", "venue", "surface"], fields)

    def upsert_pedigree_stat(self, **fields) -> None:
        self._upsert("pedigree_stats", ["sire_id", "distance_bucket", "surface"], fields)

    def add_scrape_log(self, url: str, status_code: int, etag: str | None = None) -> None:
        self._execute(
            "INSERT INTO scrape_log (url, fetched_at, status_code, etag) VALUES (?, ?, ?, ?)",
            [url, _now_iso(), status_code, etag or ""],
        )

    # ------------------------------------------------------------------
    # get（読み出し）
    # ------------------------------------------------------------------
    def _query(self, sql: str, params: list) -> list[dict]:
        rs = self._execute(sql, params)
        return [_row_to_dict(r) for r in rs.rows]

    def _build(self, model_cls, row: dict):
        """行 dict からモデルインスタンス（transient）を作る。fetched_at は datetime に。"""
        data = dict(row)
        if "fetched_at" in data:
            data["fetched_at"] = _parse_dt(data["fetched_at"])
        return model_cls(**data)

    def get_race(self, race_id: str) -> Race | None:
        rows = self._query("SELECT * FROM races WHERE race_id = ?", [race_id])
        return self._build(Race, rows[0]) if rows else None

    def get_horse(self, horse_id: str) -> Horse | None:
        rows = self._query("SELECT * FROM horses WHERE horse_id = ?", [horse_id])
        return self._build(Horse, rows[0]) if rows else None

    def get_entries(self, race_id: str) -> list[RaceEntry]:
        rows = self._query(
            "SELECT * FROM race_entries WHERE race_id = ? ORDER BY horse_number", [race_id]
        )
        return [self._build(RaceEntry, r) for r in rows]

    def get_races_by_date(self, date: str) -> list[Race]:
        rows = self._query(
            "SELECT * FROM races WHERE date = ? ORDER BY venue, race_no", [date]
        )
        return [self._build(Race, r) for r in rows]

    def get_track_bias(self, date: str, venue: str, surface: str) -> TrackBiasDaily | None:
        rows = self._query(
            "SELECT * FROM track_bias_daily WHERE date = ? AND venue = ? AND surface = ?",
            [date, venue, surface],
        )
        return self._build(TrackBiasDaily, rows[0]) if rows else None

    def get_pedigree_stat(
        self, sire_id: str, distance_bucket: str, surface: str
    ) -> PedigreeStat | None:
        rows = self._query(
            "SELECT * FROM pedigree_stats WHERE sire_id = ? AND distance_bucket = ? AND surface = ?",
            [sire_id, distance_bucket, surface],
        )
        return self._build(PedigreeStat, rows[0]) if rows else None

    def has_pedigree_for_sire(self, sire_id: str) -> bool:
        rows = self._query(
            "SELECT 1 FROM pedigree_stats WHERE sire_id = ? LIMIT 1", [sire_id]
        )
        return len(rows) > 0


# ----------------------------------------------------------------------
# 補助
# ----------------------------------------------------------------------
def _row_to_dict(row) -> dict:
    """libsql_client.Row を dict に。asdict() があれば使う。"""
    if hasattr(row, "asdict"):
        return row.asdict()
    return dict(row)


def _split_sql(text: str) -> list[str]:
    """DDL を `;` 区切りで文に分割（コメント行は除去）。"""
    cleaned: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("--"):
            continue
        cleaned.append(line)
    joined = "\n".join(cleaned)
    return [stmt.strip() for stmt in joined.split(";") if stmt.strip()]


def _parse_ddl_columns() -> dict[str, set[str]]:
    """ddl.sql を解析し {テーブル名: 列名集合} を返す（CREATE TABLE のみ）。"""
    import re

    out: dict[str, set[str]] = {}
    for stmt in _split_sql(DDL_PATH.read_text(encoding="utf-8")):
        m = re.match(r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\((.*)\)\s*$", stmt, re.S | re.I)
        if not m:
            continue
        table, body = m.group(1), m.group(2)
        cols: set[str] = set()
        for part in _split_top_level(body):
            first = part.strip().split()[0] if part.strip() else ""
            # 制約行（PRIMARY KEY 等）は列ではない
            if first.upper() in ("PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT"):
                continue
            if first:
                cols.add(first)
        out[table] = cols
    return out


def _split_top_level(body: str) -> list[str]:
    """括弧のネストを考慮してカンマ区切りする（PRIMARY KEY(a, b) を壊さない）。"""
    parts, depth, cur = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur))
    return parts


def schema_discrepancies() -> list[str]:
    """SQLAlchemy モデルと ddl.sql の テーブル/列 の食い違いを列挙する。

    両者が一致していれば空リスト。起動時の整合チェック用。
    """
    from src.storage.models import Base

    model_schema = {
        t.name: set(c.name for c in t.columns) for t in Base.metadata.sorted_tables
    }
    ddl_schema = _parse_ddl_columns()

    diffs: list[str] = []
    for table, cols in model_schema.items():
        if table not in ddl_schema:
            diffs.append(f"ddl.sql にテーブル {table} が無い")
            continue
        missing = cols - ddl_schema[table]
        extra = ddl_schema[table] - cols
        if missing:
            diffs.append(f"{table}: ddl.sql に列が不足 {sorted(missing)}")
        if extra:
            diffs.append(f"{table}: ddl.sql に余分な列 {sorted(extra)}")
    return diffs
