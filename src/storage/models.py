"""SQLAlchemy のテーブル定義（ORM モデル）。

スクレイピングで得たデータを SQLite に永続化するためのスキーマを定義する。
DB ファイルは client.py の HTTP キャッシュと同じ data/cache.db を共有する
（テーブルが別なので衝突しない）。

役割分担（重要）:
    - http_cache       : client.py 所有。HTTP バイト層のキャッシュ（HTML 本体）。
                         fetch() が毎回参照し、再ダウンロードを防ぐ。
    - 各テーブルの fetched_at : 解析済みデータの鮮度判定に使う。
    - scrape_log       : 追記専用の監査ログ。何も gate しない（履歴・透明性のみ）。

マイグレーションツール（Alembic）は使わない。create_all で全テーブルを作る。
スキーマを変更したら data/cache.db を削除して作り直すこと（README 参照）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """全モデルの基底クラス（SQLAlchemy 2.0 スタイル）。"""


def _now() -> datetime:
    """fetched_at のデフォルト値に使う現在時刻（ローカル）。"""
    return datetime.now()


class Race(Base):
    """レース 1 件。出走表ページから得られる基本情報。"""

    __tablename__ = "races"

    race_id: Mapped[str] = mapped_column(String, primary_key=True)
    date: Mapped[str] = mapped_column(String, index=True)        # 開催日 YYYYMMDD
    venue: Mapped[str] = mapped_column(String)                   # 会場名（東京 等）
    race_no: Mapped[int] = mapped_column(Integer)               # レース番号
    race_name: Mapped[str] = mapped_column(String, default="")  # レース名
    distance: Mapped[int] = mapped_column(Integer, default=0)   # 距離(m)
    surface: Mapped[str] = mapped_column(String, default="")    # turf/dirt/jump
    course_condition: Mapped[str] = mapped_column(String, default="")  # firm/good/...
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    # このレースの出走馬（race_entries）への参照
    entries: Mapped[list["RaceEntry"]] = relationship(
        back_populates="race", cascade="all, delete-orphan"
    )


class Horse(Base):
    """馬 1 頭。複数レースで使い回されるためレースとは別テーブル。"""

    __tablename__ = "horses"

    horse_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, default="")
    sex: Mapped[str] = mapped_column(String, default="")        # 牡/牝/セ
    age: Mapped[int] = mapped_column(Integer, default=0)
    sire_id: Mapped[str] = mapped_column(String, default="")        # 父
    dam_sire_id: Mapped[str] = mapped_column(String, default="")    # 母父
    running_style: Mapped[str] = mapped_column(String, default="")  # 逃げ/先行/差し/追込（推定）
    # 脚質の信頼度（推定に使った過去走数）。0 なら不明。
    running_style_confidence: Mapped[int] = mapped_column(Integer, default=0)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class RaceEntry(Base):
    """ある馬の、あるレースへの出走（レース×馬の中間テーブル）。"""

    __tablename__ = "race_entries"

    # 1 レース内で 1 頭は 1 回だけ → (race_id, horse_id) を主キーにする
    race_id: Mapped[str] = mapped_column(
        ForeignKey("races.race_id"), primary_key=True
    )
    horse_id: Mapped[str] = mapped_column(String, primary_key=True)
    post_position: Mapped[int] = mapped_column(Integer, default=0)  # 枠番
    horse_number: Mapped[int] = mapped_column(Integer, default=0)   # 馬番
    jockey: Mapped[str] = mapped_column(String, default="")
    weight: Mapped[float] = mapped_column(Float, default=0.0)       # 斤量
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    race: Mapped["Race"] = relationship(back_populates="entries")


class TrackBiasDaily(Base):
    """日付×会場×馬場ごとのトラックバイアス（馬場傾向）。"""

    __tablename__ = "track_bias_daily"

    # 同じ日・会場・馬場で 1 レコード
    date: Mapped[str] = mapped_column(String, primary_key=True)     # YYYYMMDD
    venue: Mapped[str] = mapped_column(String, primary_key=True)
    surface: Mapped[str] = mapped_column(String, primary_key=True)  # turf/dirt
    inside_outside_bias: Mapped[float] = mapped_column(Float, default=0.0)  # 内(-)〜外(+)
    pace_bias: Mapped[float] = mapped_column(Float, default=0.0)            # 前残り(-)〜差し(+)
    raw_json: Mapped[str] = mapped_column(Text, default="")  # 算出元データの控え
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class PedigreeStat(Base):
    """血統（種牡馬）の距離×馬場ごとの成績統計。

    オンデマンドで取得・キャッシュする想定。未取得なら単にレコードが無い
    （ゼロ件）状態で、分析側は中立スコア＋ベイズ補正にフォールバックする。
    """

    __tablename__ = "pedigree_stats"

    sire_id: Mapped[str] = mapped_column(String, primary_key=True)
    distance_bucket: Mapped[str] = mapped_column(String, primary_key=True)  # 例 "1400-1600"
    surface: Mapped[str] = mapped_column(String, primary_key=True)          # turf/dirt
    win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ScrapeLog(Base):
    """スクレイピングの監査ログ（追記専用）。

    キャッシュ判定には一切使わない。「いつ・どの URL を・どの結果で取得したか」
    の履歴を残し、UI の最終取得表示やデバッグ、透明性のために使う。
    """

    __tablename__ = "scrape_log"

    # 追記専用なので代理主キー（自動採番）を持たせる
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    status_code: Mapped[int] = mapped_column(Integer, default=0)
    etag: Mapped[str] = mapped_column(String, default="")
