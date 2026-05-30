"""アプリ全体で使う定数を 1 か所にまとめた設定ファイル。

ここを編集すれば、レート制限やキャッシュ場所などの挙動をまとめて変更できる。
環境ごとに変えたい値（メールアドレス等）は .env で上書きできるようにしている。
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# .env ファイルがあれば読み込む（無くてもエラーにはならない）
load_dotenv()


# ---------------------------------------------------------------------------
# パス関連
# ---------------------------------------------------------------------------
# このファイル（config.py）が置かれているディレクトリ = プロジェクトのルート
BASE_DIR: Path = Path(__file__).resolve().parent

# SQLite キャッシュ DB の置き場所（data/cache.db）
DATA_DIR: Path = BASE_DIR / "data"
DB_PATH: Path = DATA_DIR / "cache.db"

# SQLAlchemy 用の接続文字列（sqlite:///絶対パス）
DB_URL: str = f"sqlite:///{DB_PATH}"


# ---------------------------------------------------------------------------
# netkeiba の URL
# ---------------------------------------------------------------------------
# スクレイピング対象は「公開ページのみ」。ログイン必須ページには触れない。
NETKEIBA_BASE_URL: str = "https://yoso.netkeiba.com/"
NETKEIBA_DOMAIN: str = "netkeiba.com"

# robots.txt の場所（起動時に取得して尊重する）
ROBOTS_TXT_URL: str = "https://yoso.netkeiba.com/robots.txt"


# ---------------------------------------------------------------------------
# HTTP リクエストのマナー設定
# ---------------------------------------------------------------------------
# User-Agent にはアプリ名と連絡先メールを含める（運営側が問い合わせできるように）。
# メールは .env の CONTACT_EMAIL で上書き可能。
CONTACT_EMAIL: str = os.getenv("CONTACT_EMAIL", "forbusi2020@gmail.com")
APP_NAME: str = "keiba_predictor"
APP_VERSION: str = "0.1.0"
USER_AGENT: str = (
    f"{APP_NAME}/{APP_VERSION} (personal use; contact: {CONTACT_EMAIL})"
)

# リクエスト間隔（秒）。逐次取得時の最低間隔。並列化と合わせ 1.5→1.0 に短縮。
REQUEST_INTERVAL_SEC: float = float(os.getenv("REQUEST_INTERVAL_SEC", "1.0"))
# 上記に加えてランダムなゆらぎ（ジッタ）を 0〜この秒数だけ足す。
REQUEST_JITTER_SEC: float = float(os.getenv("REQUEST_JITTER_SEC", "0.5"))

# ---------------------------------------------------------------------------
# 並列スクレイピング
# ---------------------------------------------------------------------------
# 同時接続数（ThreadPool のワーカー数）。netkeiba への負荷を抑えるため少なめ。
PARALLEL_MAX_CONCURRENT: int = int(os.getenv("PARALLEL_MAX_CONCURRENT", "3"))
# 1 ワーカーあたりの最小間隔（秒）。並列時のグローバル間隔は これ / 同時接続数。
MIN_INTERVAL_PER_WORKER: float = 1.0
# 429/503 が連続したときのクールダウン（秒）
RATELIMIT_COOLDOWN_SEC: float = float(os.getenv("RATELIMIT_COOLDOWN_SEC", "60"))
# 何回連続で 429/503 を食らったら中断するか
RATELIMIT_MAX_CONSECUTIVE: int = 3
# 1 レース処理あたりの実ネットワークリクエスト上限（暴走防止のハードキャップ）
MAX_REQUESTS_PER_RACE: int = int(os.getenv("MAX_REQUESTS_PER_RACE", "150"))

# 1 リクエストあたりのタイムアウト（秒）
REQUEST_TIMEOUT_SEC: float = 15.0

# リトライ設定（指数バックオフ: 1, 2, 4 秒 ...）
MAX_RETRIES: int = 3
BACKOFF_FACTOR_SEC: float = 1.0  # backoff = BACKOFF_FACTOR * (2 ** 試行回数)

# リトライ対象にする HTTP ステータスコード（一時的な障害）
RETRY_STATUS_CODES: tuple[int, ...] = (429, 500, 502, 503, 504)


# ---------------------------------------------------------------------------
# キャッシュ設定
# ---------------------------------------------------------------------------
# 同じ URL は「当日キャッシュ」があれば再取得しない。
# キャッシュの有効期間（時間）。同日運用なので 24 時間を既定にする。
CACHE_TTL_HOURS: int = int(os.getenv("CACHE_TTL_HOURS", "24"))


# ---------------------------------------------------------------------------
# スコアリングの既定値（UI 初期値）
# ---------------------------------------------------------------------------
# 3 要素の重み（合計 1.0 になるよう UI 側で正規化する）
DEFAULT_WEIGHT_BIAS: float = 0.34   # トラックバイアス
DEFAULT_WEIGHT_PEDIGREE: float = 0.33  # 血統
DEFAULT_WEIGHT_STYLE: float = 0.33  # 脚質

# softmax の温度（小さいほど 1 着候補に確率が集中して鋭くなる）
DEFAULT_TEMPERATURE: float = 1.0

# データ不足時に返す中立スコア
NEUTRAL_SCORE: float = 50.0

# 血統スコアのベイズ補正に使う「信頼できる」最小サンプル数
PEDIGREE_MIN_SAMPLE: int = 30

# ---------------------------------------------------------------------------
# 脚質推定の設定
# ---------------------------------------------------------------------------
# 脚質推定に使う過去走の最大本数（直近から）
RUNNING_STYLE_MAX_RUNS: int = 10
# これ未満の走数しか無ければ「不明」扱い（confidence 低）
RUNNING_STYLE_MIN_RUNS: int = 3
# 推定結果（horses.running_style）のキャッシュ有効日数
RUNNING_STYLE_TTL_DAYS: int = 30

# 1 コーナー通過順位を頭数で正規化した値（0=先頭〜1=最後方）の閾値で脚質を分類
#   <= 0.15 : 逃げ / <= 0.35 : 先行 / <= 0.65 : 差し / それ超: 追込
RUNNING_STYLE_THRESHOLDS: dict[str, float] = {
    "逃げ": 0.15,
    "先行": 0.35,
    "差し": 0.65,
    # 追込 はこれを超えるもの
}


def ensure_dirs() -> None:
    """必要なディレクトリ（data/）が無ければ作成する。

    アプリ起動時に 1 回呼ぶ想定。すでに存在していてもエラーにならない。
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
