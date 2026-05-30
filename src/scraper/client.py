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
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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


class RateLimitError(FetchError):
    """429/503 等のレート制限系エラーでリトライを使い切った時の例外。

    並列取得側がこれを検知して並列度を落とす（degrade）ために、通常の
    FetchError と区別する。
    """


class RequestBudgetExceeded(FetchError):
    """1 レースあたりのリクエスト上限（ハードキャップ）を超えた時の例外。"""


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

        # 直近のリクエスト時刻（逐次レート制限の計算に使う）。最初は 0。
        self._last_request_time: float = 0.0

        # robots.txt のパーサ（最初に fetch する時に遅延読み込みする）
        self._robot_parser: robotparser.RobotFileParser | None = None
        self._robots_lock = threading.Lock()  # 並列時の初期化競合を防ぐ

        # 並列レート制限用: 次にリクエストを投げてよい時刻（monotonic）。
        self._next_slot: float = 0.0
        self._slot_lock = threading.Lock()
        # 429/503 を食らったら True にして並列間隔を広げる（実効並列度を 1 に）。
        self._degraded: bool = False

        # 実ネットワークリクエスト数のカウンタ（ベンチ＆ハードキャップ用）
        self._network_count: int = 0
        self._max_requests: int | None = None
        self._count_lock = threading.Lock()

        # HTTP キャッシュ用テーブルを準備
        self._init_cache_db()

    # ------------------------------------------------------------------
    # リクエスト予算（ハードキャップ）・カウンタ
    # ------------------------------------------------------------------
    def set_request_budget(self, max_requests: int | None) -> None:
        """1 レース処理あたりの実リクエスト上限を設定し、カウンタを 0 に戻す。"""
        with self._count_lock:
            self._max_requests = max_requests
            self._network_count = 0

    @property
    def network_count(self) -> int:
        """これまでに実際にネットワークへ出た回数（キャッシュヒットは含まない）。"""
        return self._network_count

    @property
    def degraded(self) -> bool:
        """429/503 を受けて並列度を落とした状態かどうか。"""
        return self._degraded

    def _count_network_request(self, url: str) -> None:
        """実ネットワーク要求の直前に呼ぶ。上限超過なら例外。"""
        with self._count_lock:
            self._network_count += 1
            if self._max_requests is not None and self._network_count > self._max_requests:
                raise RequestBudgetExceeded(
                    f"リクエスト上限 {self._max_requests} を超えました（url={url}）"
                )

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

        # 並列取得時に複数スレッドが同時に読まないようロックで保護
        with self._robots_lock:
            if self._robot_parser is not None:
                return
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

    def _acquire_slot(self, spacing: float) -> None:
        """並列取得用のスレッドセーフなレート制限。

        「次に投げてよい時刻（_next_slot）」をロック下で予約し、ロックを離して
        から待機する。これにより各リクエストが spacing 秒ずつずれて発射され、
        複数ワーカーが居ても実効レートが spacing で頭打ちになる。
        """
        with self._slot_lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + spacing
            wait = slot - now
        if wait > 0:
            time.sleep(wait)

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
        """fetch() と同じだが、キャッシュ由来かどうか等の詳細も返す版（逐次）。"""
        return self._fetch_detailed_impl(url, force_refresh, self._respect_rate_limit)

    def _fetch_detailed_impl(self, url: str, force_refresh: bool, rate_limit_fn) -> FetchResult:
        """fetch_detailed の本体。レート制限のかけ方（逐次/並列）を差し替え可能にする。"""
        # --- 1. キャッシュ確認 ---
        if not force_refresh:
            cached = self._read_cache(url)
            if cached is not None:
                return cached

        # --- 2. robots.txt 確認 ---
        self._check_robots(url)

        # --- 3 & 4. レート制限 + リトライ付き取得 ---
        html, status_code, etag = self._fetch_with_retry(url, rate_limit_fn)

        # --- 5. キャッシュ保存 ---
        fetched_at = self._write_cache(url, html, status_code, etag)

        return FetchResult(
            url=url,
            html=html,
            status_code=status_code,
            from_cache=False,
            fetched_at=fetched_at,
        )

    def _fetch_with_retry(self, url: str, rate_limit_fn=None) -> tuple[str, int, str | None]:
        """実際の HTTP 取得。失敗時は指数バックオフでリトライする。

        Returns:
            (html, status_code, etag) のタプル。

        Args:
            url: 取得 URL。
            rate_limit_fn: 各試行前に呼ぶレート制限関数。None なら逐次用を使う。

        Raises:
            RateLimitError: 429/503 系でリトライを使い切った場合。
            FetchError: その他で全リトライが失敗した場合。
        """
        rl = rate_limit_fn or self._respect_rate_limit
        last_error: Exception | None = None
        hit_rate_limit = False

        # 試行回数: 0, 1, 2, ... MAX_RETRIES まで（合計 MAX_RETRIES+1 回）
        for attempt in range(config.MAX_RETRIES + 1):
            # 2 回目以降はバックオフして待つ（1, 2, 4 秒 ...）
            if attempt > 0:
                backoff = config.BACKOFF_FACTOR_SEC * (2 ** (attempt - 1))
                time.sleep(backoff)

            # どの試行でもレート制限は守る
            rl()

            # 実ネットワークに出る直前にカウント（ハードキャップ判定）
            self._count_network_request(url)

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
                hit_rate_limit = True
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

        # ここに来たら全リトライ失敗。429/503 起因なら RateLimitError で区別する。
        msg = f"リトライ上限（{config.MAX_RETRIES} 回）に達しました: {url}"
        if hit_rate_limit:
            raise RateLimitError(msg) from last_error
        raise FetchError(msg) from last_error

    # ------------------------------------------------------------------
    # 並列取得
    # ------------------------------------------------------------------
    def fetch_many(
        self,
        urls: list[str],
        max_concurrent: int | None = None,
        force_refresh: bool = False,
    ) -> dict[str, str | None]:
        """複数 URL を同時最大 max_concurrent 本で並列取得する。

        - グローバル間隔 = MIN_INTERVAL_PER_WORKER / max_concurrent でレート制御。
        - 429/503 を食らったら並列度を実質 1 に落とし（間隔を広げ）、
          クールダウン後に 1 回だけ再試行する。連続 RATELIMIT_MAX_CONSECUTIVE 回で中断。
        - キャッシュ済み URL は実通信せず即返る。

        Args:
            urls: 取得 URL のリスト（重複は除外して処理）。
            max_concurrent: 同時接続数。None なら config 値。
            force_refresh: True ならキャッシュ無視。

        Returns:
            {url: html or None} の辞書（失敗した URL は None）。

        Raises:
            RateLimitError: 429/503 が連続して中断した場合。
        """
        mc = max_concurrent or config.PARALLEL_MAX_CONCURRENT
        base_spacing = config.MIN_INTERVAL_PER_WORKER / max(1, mc)

        # 重複除外（順序は保たない・辞書で返す）
        unique = list(dict.fromkeys(urls))
        results: dict[str, str | None] = {}
        if unique:
            # Streamlit Cloud のログにも残る（並列度の確認用）
            print(f"[client] parallel fetch: {mc} workers, {len(unique)} urls", flush=True)

        state_lock = threading.Lock()
        consecutive = {"rate_errors": 0}
        aborted = {"flag": False}

        def _spacing() -> float:
            # degrade 中はワーカー全員が 1 秒間隔（実効並列度 1）になる
            return config.MIN_INTERVAL_PER_WORKER if self._degraded else base_spacing

        def _worker(url: str) -> tuple[str, str | None]:
            if aborted["flag"]:
                return url, None
            try:
                res = self._fetch_detailed_impl(
                    url, force_refresh, lambda: self._acquire_slot(_spacing())
                )
                with state_lock:
                    consecutive["rate_errors"] = 0
                    self._degraded = False  # 正常応答で回復
                return url, res.html
            except RateLimitError:
                # 並列度を落とし、クールダウン後に 1 回だけ再試行
                with state_lock:
                    self._degraded = True
                    consecutive["rate_errors"] += 1
                    if consecutive["rate_errors"] >= config.RATELIMIT_MAX_CONSECUTIVE:
                        aborted["flag"] = True
                        return url, None
                time.sleep(config.RATELIMIT_COOLDOWN_SEC)
                try:
                    res = self._fetch_detailed_impl(
                        url, force_refresh,
                        lambda: self._acquire_slot(config.MIN_INTERVAL_PER_WORKER),
                    )
                    with state_lock:
                        consecutive["rate_errors"] = 0
                    return url, res.html
                except Exception:
                    return url, None
            except Exception:
                # robots 拒否・その他は None（個別失敗。全体は止めない）
                return url, None

        with ThreadPoolExecutor(max_workers=mc) as ex:
            for url, html in ex.map(_worker, unique):
                results[url] = html

        if aborted["flag"]:
            raise RateLimitError(
                "429/503 が連続したため並列取得を中断しました（並列度を1に落として再試行後も継続）"
            )
        return results

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
