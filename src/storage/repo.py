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

from datetime import datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

import config
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


def get_engine(db_url: str | None = None) -> Engine:
    """SQLAlchemy エンジンを返す（最初の呼び出しで生成）。

    Args:
        db_url: 接続文字列。None なら config.DB_URL。テスト時に別 DB を渡せる。
    """
    global _engine, _SessionFactory
    if db_url is not None:
        # テスト用に明示指定された場合は、その都度新しいエンジンを作る
        engine = create_engine(db_url, future=True)
        return engine
    if _engine is None:
        # 既定パスは Turso 対応エンジン（未設定ならローカル SQLite にフォールバック）
        from src.storage import engine as engine_mod
        _engine = engine_mod.create_app_engine()
        _SessionFactory = sessionmaker(bind=_engine, future=True)
    return _engine


def init_db(engine: Engine | None = None) -> None:
    """全テーブルを作成する（無ければ作る・あれば何もしない）。

    Alembic は使わず、これだけでスキーマを用意する。スキーマ変更時は
    data/cache.db を削除してから再度呼ぶこと。
    """
    eng = engine or get_engine()
    Base.metadata.create_all(eng)


def get_session(engine: Engine | None = None) -> Session:
    """新しいセッションを返す。呼び出し側で with 構文で閉じること。"""
    if engine is not None:
        return Session(engine, future=True)
    get_engine()  # 共有エンジン・ファクトリを初期化
    assert _SessionFactory is not None
    return _SessionFactory()


# ---------------------------------------------------------------------------
# upsert（書き込み）
# ---------------------------------------------------------------------------
def upsert_race(session: Session, **fields) -> Race:
    """races テーブルへ 1 件 upsert する。fields は Race の属性名で渡す。"""
    obj = Race(**fields)
    merged = session.merge(obj)  # 主キー(race_id)一致で更新、無ければ挿入
    session.commit()
    return merged


def upsert_horse(session: Session, **fields) -> Horse:
    """horses テーブルへ 1 件 upsert する。"""
    merged = session.merge(Horse(**fields))
    session.commit()
    return merged


def upsert_entry(session: Session, **fields) -> RaceEntry:
    """race_entries テーブルへ 1 件 upsert する。"""
    merged = session.merge(RaceEntry(**fields))
    session.commit()
    return merged


def upsert_track_bias(session: Session, **fields) -> TrackBiasDaily:
    """track_bias_daily テーブルへ 1 件 upsert する。"""
    merged = session.merge(TrackBiasDaily(**fields))
    session.commit()
    return merged


def upsert_pedigree_stat(session: Session, **fields) -> PedigreeStat:
    """pedigree_stats テーブルへ 1 件 upsert する。"""
    merged = session.merge(PedigreeStat(**fields))
    session.commit()
    return merged


def add_scrape_log(
    session: Session, url: str, status_code: int, etag: str | None = None
) -> ScrapeLog:
    """scrape_log に 1 行追記する（監査ログ。更新はしない）。"""
    log = ScrapeLog(url=url, status_code=status_code, etag=etag or "")
    session.add(log)
    session.commit()
    return log


# ---------------------------------------------------------------------------
# get（読み出し）
# ---------------------------------------------------------------------------
def get_race(session: Session, race_id: str) -> Race | None:
    """race_id でレースを取得。無ければ None。"""
    return session.get(Race, race_id)


def get_horse(session: Session, horse_id: str) -> Horse | None:
    """horse_id で馬を取得。無ければ None。"""
    return session.get(Horse, horse_id)


def get_entries(session: Session, race_id: str) -> list[RaceEntry]:
    """指定レースの出走馬一覧を馬番順で取得。無ければ空リスト。"""
    stmt = (
        select(RaceEntry)
        .where(RaceEntry.race_id == race_id)
        .order_by(RaceEntry.horse_number)
    )
    return list(session.scalars(stmt))


def get_races_by_date(session: Session, date: str) -> list[Race]:
    """指定開催日(YYYYMMDD)のレース一覧を取得。無ければ空リスト。"""
    stmt = select(Race).where(Race.date == date).order_by(Race.venue, Race.race_no)
    return list(session.scalars(stmt))


def get_track_bias(
    session: Session, date: str, venue: str, surface: str
) -> TrackBiasDaily | None:
    """指定日・会場・馬場のトラックバイアスを取得。無ければ None。"""
    return session.get(TrackBiasDaily, (date, venue, surface))


def get_pedigree_stat(
    session: Session, sire_id: str, distance_bucket: str, surface: str
) -> PedigreeStat | None:
    """血統統計を取得。未取得（ゼロ件）なら None を返す。

    呼び出し側（pedigree_score）は None のとき中立スコアにフォールバックする。
    """
    return session.get(PedigreeStat, (sire_id, distance_bucket, surface))


def has_pedigree_for_sire(session: Session, sire_id: str) -> bool:
    """その種牡馬の成績が pedigree_stats に 1 件でもあれば True。

    True なら種牡馬ページを再取得しない（sire_id 単位キャッシュの判定）。
    """
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
