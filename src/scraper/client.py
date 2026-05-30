"""netkeiba への HTTP アクセスを一手に引き受けるクライアント。

このモジュールが「スクレイピングのマナー」をすべて担当する：

1. robots.txt を尊重（取得が許可された URL かを確認）
2. リクエスト間隔を最低 1.5 秒空ける（+ ランダムなゆらぎ）
3. 失敗時は最大 3 回まで指数バックオフでリトライ
4. 取得した HTML は SQLite にキャッシュし、当日分は再取得しない

他のスクレイパ（race_list.py 等）は、このクライアントの ``fetch()`` を
呼ぶだけでよく、マナーを個別に意識しなくて済む設計にしている。

使い方の例::

    from src.scraper.client import NetkeibaClient

    client = NetkeibaClient()
    html = client.fetch("https://yoso.netkeiba.com/?pid=race_list")
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from random import Random
from urllib import robotparser
from urllib.parse import urlparse

import requests

import config


# モジュール内で使う擬似乱数生成器（ジッタ用）。
# グローバルな random の状態を汚さないよう、専用インスタンスを持つ。
_rng = Random()


class RobotsDisallowedError(Exception):
    """robots.txt によって取得が禁止されている URL にアクセスしようとした時の例外。"""


class FetchError(Exception):
    """リトライを使い切っても取得に失敗した時の例外。"""


@dataclass
class FetchResult:
    """1 回の取得結果をまとめた小さな入れ物。

    Attributes:
        url: 取得した URL
        html: 取得した HTML 本文
        status_code: HTTP ステータスコード（キャッシュ由来なら保存時の値）
        from_cache: キャッシュから返したかどうか
        fetched_at: 実際に取得した日時（ISO 文字列）
    """

    url: str
    html: str
    status_code: int
    from_cache: bool
    fetched_at: str


class NetkeibaClient:
    """netkeiba 専用の、マナーを守る HTTP クライアント。

    1 つのアプリ実行につき 1 インスタンスを使い回す想定。
    内部に「前回リクエスト時刻」を持ち、間隔を自動で調整する。
    """

    def __init__(
        self,
        db_path: str | None = None,
        respect_robots: bool = True,
    ) -> None:
        """クライアントを初期化する。

        Args:
            db_path: キャッシュ DB のパス。None なら config.DB_PATH を使う。
            respect_robots: True なら robots.txt を確認する（テスト時は False にできる）。
        """
        config.ensure_dirs()  # data/ ディレクトリが無ければ作る

        self._db_path = str(db_path) if db_path else str(config.DB_PATH)
        self._respect_robots = respect_robots

        # requests のセッション（接続を使い回して効率化）
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": config.USER_AGENT})

        # 直近のリクエスト時刻（レート制限の計算に使う）。最初は 0。
        self._last_request_time: float = 0.0

        # robots.txt のパーサ（最初に fetch する時に遅延読み込みする）
        self._robot_parser: robotparser.RobotFileParser | None = None

        # HTTP キャッシュ用テーブルを準備
        self._init_cache_db()

    # ------------------------------------------------------------------
    # キャッシュ DB
    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        """SQLite に接続する。呼び出し側で close すること。"""
        return sqlite3.connect(self._db_path)

    def _init_cache_db(self) -> None:
        """生の HTML を保存する http_cache テーブルを（無ければ）作る。

        構造化データ用のテーブルは後の storage/models.py が担当する。
        ここはあくまで「同じ URL を何度も叩かない」ための生キャッシュ。
        """
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS http_cache (
                    url          TEXT PRIMARY KEY,
                    html         TEXT NOT NULL,
                    status_code  INTEGER NOT NULL,
                    etag         TEXT,
                    fetched_at   TEXT NOT NULL
                )
                """
            )

    def _read_cache(self, url: str) -> FetchResult | None:
        """キャッシュを読む。有効期限内のものがあれば FetchResult を返す。

        期限切れ・未取得なら None を返す。
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT html, status_code, fetched_at FROM http_cache WHERE url = ?",
                (url,),
            ).fetchone()

        if row is None:
            return None

        html, status_code, fetched_at_str = row

        # 取得日時を datetime に戻し、有効期限（CACHE_TTL_HOURS）を超えていないか確認
        try:
            fetched_at = datetime.fromisoformat(fetched_at_str)
        except ValueError:
            return None  # 壊れた値なら無効扱い

        if datetime.now() - fetched_at > timedelta(hours=config.CACHE_TTL_HOURS):
            return None  # 期限切れ

        return FetchResult(
            url=url,
            html=html,
            status_code=status_code,
            from_cache=True,
            fetched_at=fetched_at_str,
        )

    def _write_cache(self, url: str, html: str, status_code: int, etag: str | None) -> str:
        """取得した HTML をキャッシュに保存し、保存日時を返す。"""
        fetched_at = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            # 同じ URL は上書き（INSERT OR REPLACE）
            conn.execute(
                """
                INSERT OR REPLACE INTO http_cache (url, html, status_code, etag, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (url, html, status_code, etag, fetched_at),
            )
        return fetched_at

    # ------------------------------------------------------------------
    # robots.txt
    # ------------------------------------------------------------------
    def _load_robots(self) -> None:
        """robots.txt を取得してパーサにセットする（初回のみ実行）。

        取得に失敗した場合は「安全側」に倒し、空のルール（全許可ではなく
        全 URL を要確認のままにする）として扱う。
        """
        if self._robot_parser is not None:
            return  # すでに読み込み済み

        parser = robotparser.RobotFileParser()
        parser.set_url(config.ROBOTS_TXT_URL)
        try:
            parser.read()  # ここで robots.txt を HTTP 取得する
        except Exception:
            # robots.txt 自体が取れない場合、can_fetch は False を返しがち。
            # 明示的に「読めなかった」状態のパーサを保持しておく。
            pass
        self._robot_parser = parser

    def _check_robots(self, url: str) -> None:
        """指定 URL の取得が robots.txt で許可されているか確認する。

        禁止されていれば RobotsDisallowedError を投げる。
        """
        if not self._respect_robots:
            return

        self._load_robots()
        assert self._robot_parser is not None
        if not self._robot_parser.can_fetch(config.USER_AGENT, url):
            raise RobotsDisallowedError(
                f"robots.txt によりこの URL の取得は許可されていません: {url}"
            )

    # ------------------------------------------------------------------
    # レート制限
    # ------------------------------------------------------------------
    def _respect_rate_limit(self) -> None:
        """前回リクエストから十分な時間が経つまで待機する。

        待機時間 = 設定間隔 + ランダムなジッタ。
        ジッタを入れることで、機械的な等間隔アクセスを避ける。
        """
        now = time.monotonic()
        elapsed = now - self._last_request_time

        # この回に確保したい最小間隔（基本間隔 + 0〜JITTER のランダム値）
        wait_target = config.REQUEST_INTERVAL_SEC + _rng.uniform(
            0.0, config.REQUEST_JITTER_SEC
        )

        if elapsed < wait_target:
            time.sleep(wait_target - elapsed)

        self._last_request_time = time.monotonic()

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------
    def fetch(self, url: str, force_refresh: bool = False) -> str:
        """URL を取得して HTML 文字列を返す（マナー込み）。

        処理の流れ:
            1. キャッシュ確認（force_refresh=False のとき）
            2. robots.txt 確認
            3. レート制限の待機
            4. HTTP 取得（失敗時はリトライ）
            5. キャッシュ保存

        Args:
            url: 取得したい URL。
            force_refresh: True ならキャッシュを無視して必ず再取得する
                （UI の「データ再取得」ボタン用）。

        Returns:
            HTML 本文の文字列。

        Raises:
            RobotsDisallowedError: robots.txt で禁止されている場合。
            FetchError: リトライを使い切っても取得できなかった場合。
        """
        return self.fetch_detailed(url, force_refresh=force_refresh).html

    def fetch_detailed(self, url: str, force_refresh: bool = False) -> FetchResult:
        """fetch() と同じだが、キャッシュ由来かどうか等の詳細も返す版。"""
        # --- 1. キャッシュ確認 ---
        if not force_refresh:
            cached = self._read_cache(url)
            if cached is not None:
                return cached

        # --- 2. robots.txt 確認 ---
        self._check_robots(url)

        # --- 3 & 4. レート制限 + リトライ付き取得 ---
        html, status_code, etag = self._fetch_with_retry(url)

        # --- 5. キャッシュ保存 ---
        fetched_at = self._write_cache(url, html, status_code, etag)

        return FetchResult(
            url=url,
            html=html,
            status_code=status_code,
            from_cache=False,
            fetched_at=fetched_at,
        )

    def _fetch_with_retry(self, url: str) -> tuple[str, int, str | None]:
        """実際の HTTP 取得。失敗時は指数バックオフでリトライする。

        Returns:
            (html, status_code, etag) のタプル。

        Raises:
            FetchError: 全リトライが失敗した場合。
        """
        last_error: Exception | None = None

        # 試行回数: 0, 1, 2, ... MAX_RETRIES まで（合計 MAX_RETRIES+1 回）
        for attempt in range(config.MAX_RETRIES + 1):
            # 2 回目以降はバックオフして待つ（1, 2, 4 秒 ...）
            if attempt > 0:
                backoff = config.BACKOFF_FACTOR_SEC * (2 ** (attempt - 1))
                time.sleep(backoff)

            # どの試行でもレート制限は守る
            self._respect_rate_limit()

            try:
                response = self._session.get(
                    url, timeout=config.REQUEST_TIMEOUT_SEC
                )
            except requests.RequestException as exc:
                # 接続エラー・タイムアウト等 → リトライ対象
                last_error = exc
                continue

            # 一時的な障害ステータスならリトライ
            if response.status_code in config.RETRY_STATUS_CODES:
                last_error = FetchError(
                    f"一時エラー status={response.status_code} url={url}"
                )
                continue

            # 4xx（429 を除く）などはリトライしても無駄なので即エラー
            if response.status_code >= 400:
                raise FetchError(
                    f"取得失敗 status={response.status_code} url={url}"
                )

            # 文字化け対策: netkeiba は EUC-JP / UTF-8 が混在し、さらに一部ページは
            # Content-Type の charset を空（charset=）で返すため requests の推定が
            # 効かない。エンコーディングが未指定・空・iso-8859-1 のときは
            # apparent_encoding（中身から推定）に切り替える。
            enc = (response.encoding or "").lower()
            if not enc or enc == "iso-8859-1":
                response.encoding = response.apparent_encoding

            etag = response.headers.get("ETag")
            return response.text, response.status_code, etag

        # ここに来たら全リトライ失敗
        raise FetchError(
            f"リトライ上限（{config.MAX_RETRIES} 回）に達しました: {url}"
        ) from last_error

    # ------------------------------------------------------------------
    # 補助
    # ------------------------------------------------------------------
    @staticmethod
    def is_same_domain(url: str) -> bool:
        """その URL が netkeiba ドメイン内かどうかを判定する（安全確認用）。"""
        host = urlparse(url).hostname or ""
        return host.endswith(config.NETKEIBA_DOMAIN)

    def close(self) -> None:
        """セッションを閉じる（アプリ終了時に呼ぶ）。"""
        self._session.close()
