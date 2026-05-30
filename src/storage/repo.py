"""キャッシュ DB の読み書きを担うリポジトリ層。

UI やスクレイパは SQLAlchemy の細かい操作を知らなくてよいよう、ここで
「upsert（あれば更新・無ければ挿入）」と「取得」の関数を提供する。

設計メモ:
    - upsert は session.merge() を使う（主キー一致で UPDATE、無ければ INSERT）。
    - get_* は見つからなければ None / 空リストを返す。pedigree_stats が
      ゼロ件でも呼び出し側が落ちないようにするため。
    - エンジンはモジュール内で 1 つ使い回す（SQLite ファイルは config.DB_URL）。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

import config
from src.storage import engine as engine_mod
from src.storage.models import (
    Base,
    Horse,
    PedigreeStat,
    Race,
    RaceEntry,
    ScrapeLog,
    TrackBiasDaily,
)

# モジュール内で共有するエンジンとセッションファクトリ（遅延初期化）
_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None

# Turso バックエンドのキャッシュ（認証情報ごとに 1 接続）
_backend_cache: dict = {}


# ---------------------------------------------------------------------------
# バックエンド選択（Turso が設定済みなら Turso、無ければローカル SQLite）
# ---------------------------------------------------------------------------
def _active_backend():
    """Turso が使える状態なら TursoBackend を、無ければ None を返す。

    認証情報が無い／接続に失敗した場合は None（＝ SQLAlchemy + ローカル SQLite）。
    接続成功した backend は認証情報ごとにキャッシュする。
    """
    creds = engine_mod.turso_credentials()
    if creds is None:
        return None
    if creds in _backend_cache:
        return _backend_cache[creds]

    backend = _connect_turso_with_timeout(creds)
    _backend_cache[creds] = backend
    return backend


# Turso 接続にかける最大秒数（これを超えたらフォールバック。アプリを固めない）
TURSO_CONNECT_TIMEOUT_SEC = 8.0


def _connect_turso_with_timeout(creds: tuple[str, str]):
    """daemon スレッドで Turso へ接続。タイムアウト/失敗ならローカルにフォールバック(None)。

    不正・到達不能な URL でアプリが起動時に固まらないための安全装置。daemon スレッドに
    するので、接続がぶら下がってもプロセス終了をブロックしない。
    """
    import threading

    box: dict = {}

    def _connect():
        try:
            from src.storage.turso_backend import TursoBackend
            box["backend"] = TursoBackend(creds[0], creds[1])
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=_connect, daemon=True)
    t.start()
    t.join(timeout=TURSO_CONNECT_TIMEOUT_SEC)

    if t.is_alive():
        print(f"[repo] Turso 接続が {TURSO_CONNECT_TIMEOUT_SEC}s 以内に完了せず "
              "→ ローカル SQLite で続行", flush=True)
        return None
    if "error" in box:
        print(f"[repo] Turso 接続に失敗 → ローカル SQLite で続行: {box['error']}", flush=True)
        return None
    return box.get("backend")


def describe_backend() -> str:
    """現在のキャッシュ保存先の説明（UI 表示用）。"""
    creds = engine_mod.turso_credentials()
    if creds is None:
        return "ローカルSQLite"
    return "Turso（永続）" if _active_backend() is not None else \
        "ローカルSQLite（Turso接続失敗のためフォールバック）"


class _NullSession:
    """Turso 利用時のダミーセッション。repo 関数は backend に委譲し session を使わない。"""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def get_engine(db_url: str | None = None) -> Engine:
    """SQLAlchemy エンジンを返す（ローカル SQLite。Turso は別経路）。

    Args:
        db_url: 接続文字列。None なら config.DB_URL。テスト時に別 DB を渡せる。
    """
    global _engine, _SessionFactory
    if db_url is not None:
        # テスト用に明示指定された場合は、その都度新しいエンジンを作る
        return create_engine(db_url, future=True)
    if _engine is None:
        _engine = engine_mod.create_local_engine()
        _SessionFactory = sessionmaker(bind=_engine, future=True)
    return _engine


def init_db(engine: Engine | None = None) -> None:
    """スキーマを用意する。Turso 利用時は Turso 側、無ければローカル SQLite に作る。

    Alembic は使わない。スキーマ変更時は data/cache.db を削除（または Turso を作り直し）。
    """
    if engine is None and _active_backend() is not None:
        # TursoBackend の __init__ が ddl.sql でスキーマを用意済み。
        # 念のためモデルと ddl.sql の整合をチェックして食い違いを警告する。
        _warn_on_schema_mismatch()
        return
    eng = engine or get_engine()
    Base.metadata.create_all(eng)


def get_session(engine: Engine | None = None) -> Session:
    """新しいセッションを返す。呼び出し側で with 構文で閉じること。

    Turso 利用時はダミーセッションを返す（実書き込みは backend が担当）。
    """
    if engine is not None:
        return Session(engine, future=True)
    if _active_backend() is not None:
        return _NullSession()  # type: ignore[return-value]
    get_engine()  # 共有エンジン・ファクトリを初期化
    assert _SessionFactory is not None
    return _SessionFactory()


def _warn_on_schema_mismatch() -> None:
    """SQLAlchemy モデルと ddl.sql のテーブル/列の食い違いを警告する。"""
    try:
        from src.storage.turso_backend import schema_discrepancies
        diffs = schema_discrepancies()
        if diffs:
            print("[repo] 警告: モデルと ddl.sql のスキーマに差異:", diffs, flush=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# upsert（書き込み）
# ---------------------------------------------------------------------------
def upsert_race(session: Session, **fields) -> Race | None:
    """races テーブルへ 1 件 upsert する。fields は Race の属性名で渡す。"""
    be = _active_backend()
    if be is not None:
        be.upsert_race(**fields)
        return None
    merged = session.merge(Race(**fields))  # 主キー(race_id)一致で更新、無ければ挿入
    session.commit()
    return merged


def upsert_horse(session: Session, **fields) -> Horse | None:
    """horses テーブルへ 1 件 upsert する。"""
    be = _active_backend()
    if be is not None:
        be.upsert_horse(**fields)
        return None
    merged = session.merge(Horse(**fields))
    session.commit()
    return merged


def upsert_entry(session: Session, **fields) -> RaceEntry | None:
    """race_entries テーブルへ 1 件 upsert する。"""
    be = _active_backend()
    if be is not None:
        be.upsert_entry(**fields)
        return None
    merged = session.merge(RaceEntry(**fields))
    session.commit()
    return merged


def upsert_track_bias(session: Session, **fields) -> TrackBiasDaily | None:
    """track_bias_daily テーブルへ 1 件 upsert する。"""
    be = _active_backend()
    if be is not None:
        be.upsert_track_bias(**fields)
        return None
    merged = session.merge(TrackBiasDaily(**fields))
    session.commit()
    return merged


def upsert_pedigree_stat(session: Session, **fields) -> PedigreeStat | None:
    """pedigree_stats テーブルへ 1 件 upsert する。"""
    be = _active_backend()
    if be is not None:
        be.upsert_pedigree_stat(**fields)
        return None
    merged = session.merge(PedigreeStat(**fields))
    session.commit()
    return merged


def add_scrape_log(
    session: Session, url: str, status_code: int, etag: str | None = None
) -> ScrapeLog | None:
    """scrape_log に 1 行追記する（監査ログ。更新はしない）。"""
    be = _active_backend()
    if be is not None:
        be.add_scrape_log(url, status_code, etag)
        return None
    log = ScrapeLog(url=url, status_code=status_code, etag=etag or "")
    session.add(log)
    session.commit()
    return log


# ---------------------------------------------------------------------------
# get（読み出し）
# ---------------------------------------------------------------------------
def get_race(session: Session, race_id: str) -> Race | None:
    """race_id でレースを取得。無ければ None。"""
    be = _active_backend()
    if be is not None:
        return be.get_race(race_id)
    return session.get(Race, race_id)


def get_horse(session: Session, horse_id: str) -> Horse | None:
    """horse_id で馬を取得。無ければ None。"""
    be = _active_backend()
    if be is not None:
        return be.get_horse(horse_id)
    return session.get(Horse, horse_id)


def get_entries(session: Session, race_id: str) -> list[RaceEntry]:
    """指定レースの出走馬一覧を馬番順で取得。無ければ空リスト。"""
    be = _active_backend()
    if be is not None:
        return be.get_entries(race_id)
    stmt = (
        select(RaceEntry)
        .where(RaceEntry.race_id == race_id)
        .order_by(RaceEntry.horse_number)
    )
    return list(session.scalars(stmt))


def get_races_by_date(session: Session, date: str) -> list[Race]:
    """指定開催日(YYYYMMDD)のレース一覧を取得。無ければ空リスト。"""
    be = _active_backend()
    if be is not None:
        return be.get_races_by_date(date)
    stmt = select(Race).where(Race.date == date).order_by(Race.venue, Race.race_no)
    return list(session.scalars(stmt))


def get_track_bias(
    session: Session, date: str, venue: str, surface: str
) -> TrackBiasDaily | None:
    """指定日・会場・馬場のトラックバイアスを取得。無ければ None。"""
    be = _active_backend()
    if be is not None:
        return be.get_track_bias(date, venue, surface)
    return session.get(TrackBiasDaily, (date, venue, surface))


def get_pedigree_stat(
    session: Session, sire_id: str, distance_bucket: str, surface: str
) -> PedigreeStat | None:
    """血統統計を取得。未取得（ゼロ件）なら None を返す。

    呼び出し側（pedigree_score）は None のとき中立スコアにフォールバックする。
    """
    be = _active_backend()
    if be is not None:
        return be.get_pedigree_stat(sire_id, distance_bucket, surface)
    return session.get(PedigreeStat, (sire_id, distance_bucket, surface))


def has_pedigree_for_sire(session: Session, sire_id: str) -> bool:
    """その種牡馬の成績が pedigree_stats に 1 件でもあれば True。

    True なら種牡馬ページを再取得しない（sire_id 単位キャッシュの判定）。
    """
    be = _active_backend()
    if be is not None:
        return be.has_pedigree_for_sire(sire_id)
    stmt = select(PedigreeStat.sire_id).where(PedigreeStat.sire_id == sire_id).limit(1)
    return session.scalars(stmt).first() is not None


# ---------------------------------------------------------------------------
# 鮮度判定ヘルパー
# ---------------------------------------------------------------------------
def is_fresh(fetched_at: datetime | None, ttl_hours: int | None = None) -> bool:
    """fetched_at が有効期限内なら True（再取得不要の判定に使う）。

    Args:
        fetched_at: 対象レコードの取得時刻。None なら未取得とみなし False。
        ttl_hours: 有効期間（時間）。None なら config.CACHE_TTL_HOURS。
    """
    if fetched_at is None:
        return False
    ttl = config.CACHE_TTL_HOURS if ttl_hours is None else ttl_hours
    return datetime.now() - fetched_at <= timedelta(hours=ttl)
