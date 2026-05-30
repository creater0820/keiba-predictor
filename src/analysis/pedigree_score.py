"""血統 → 各馬のスコア(0〜100)を算出する純粋関数。

I/O は持たない。父・母父の「当該距離×馬場の勝率と標本数」を受け取り、
ベイズ平均で全体平均に寄せてからスコア化する。標本が少ない血統は自然に
中立(50)へ寄り、confidence も低くなる。

ベイズ平均（収縮）:
    adj = (n * win_rate + K * global_avg) / (n + K)
    K = config.PEDIGREE_MIN_SAMPLE（既定 30）。n が小さいほど global_avg に寄る。

スコア化:
    父(0.7) + 母父(0.3) で重み付けした調整勝率 blended を、全体平均との相対で
    score = 50 + RATE_SCALE * (blended - global) / global に写像する。
    初ダート/初芝・大幅な距離変更はペナルティで減点する。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import config
from src.analysis._common import clamp

# 父・母父の重み（合計 1.0）
SIRE_WEIGHT = 0.7
DAM_SIRE_WEIGHT = 0.3
# 調整勝率が全体平均から離れたときのスコア振れ幅
RATE_SCALE = 30.0
# ペナルティ量
PENALTY_FIRST_SURFACE = 8.0   # 初ダート・初芝
PENALTY_BIG_DISTANCE = 5.0    # 大幅な距離変更
BIG_DISTANCE_THRESHOLD_M = 400


@dataclass
class SireStat:
    """父または母父の、当該距離×馬場の成績。未取得は None を渡す（このクラスを使わない）。"""

    win_rate: float
    sample_size: int


@dataclass
class PedigreeScoreInput:
    """血統スコアの 1 頭分の入力（純粋データ）。"""

    sire: SireStat | None         # 父の成績（無ければ None）
    dam_sire: SireStat | None     # 母父の成績（無ければ None）
    global_avg_win_rate: float    # 全体平均勝率（ベイズの寄せ先・基準）
    is_first_surface: bool = False  # 初ダート/初芝か
    distance_change_m: int = 0      # 前走からの距離変更幅(m)。不明なら 0
    sire_name: str = ""           # 父名（文言用）
    dam_sire_name: str = ""       # 母父名（文言用）
    distance_m: int = 0           # レース距離(m)。文言用（"芝2400m前後" 等）
    context: dict = field(default_factory=dict)  # 追加情報（breakdown 用・任意）


def _bayes_adjust(stat: SireStat | None, global_avg: float, k: int) -> tuple[float, int, float]:
    """ベイズ平均で調整した勝率を返す。

    Returns:
        (raw_win_rate, sample_size, adjusted_win_rate)。stat が None なら
        (global_avg, 0, global_avg)（＝中立に寄せる）。
    """
    if stat is None or stat.sample_size <= 0:
        return (global_avg, 0, global_avg)
    n = stat.sample_size
    adj = (n * stat.win_rate + k * global_avg) / (n + k)
    return (stat.win_rate, n, adj)


def score_pedigree(inp: PedigreeScoreInput) -> tuple[float, float, dict]:
    """血統適合スコアを返す。

    Returns:
        (score 0〜100, confidence 0〜1, debug_breakdown) のタプル。
    """
    k = config.PEDIGREE_MIN_SAMPLE
    g = inp.global_avg_win_rate

    sire_raw, sire_n, sire_adj = _bayes_adjust(inp.sire, g, k)
    dam_raw, dam_n, dam_adj = _bayes_adjust(inp.dam_sire, g, k)

    # 父 0.7 + 母父 0.3 の調整勝率
    blended = SIRE_WEIGHT * sire_adj + DAM_SIRE_WEIGHT * dam_adj

    # 全体平均との相対でスコア化（global=0 のゼロ割回避）
    rate_component = RATE_SCALE * (blended - g) / g if g > 0 else 0.0

    # ペナルティ
    penalty = 0.0
    if inp.is_first_surface:
        penalty += PENALTY_FIRST_SURFACE
    if abs(inp.distance_change_m) >= BIG_DISTANCE_THRESHOLD_M:
        penalty += PENALTY_BIG_DISTANCE

    score = clamp(50.0 + rate_component - penalty)

    # 信頼度: 父＋母父の合計標本数。2K(=60)で頭打ち。
    total_n = sire_n + dam_n
    confidence = round(min(1.0, total_n / (2 * k)), 3)

    # サンプル不足（< K=30）でベイズ補正が大きく効いたか
    bayes_adjusted = (sire_n < k) or (dam_n < k)

    breakdown = {
        "base": 50.0,
        "rate_component": round(rate_component, 2),
        "penalty": round(-penalty, 2),
        "sire_win_rate": round(sire_raw, 4),
        "sire_sample": sire_n,
        "sire_adj": round(sire_adj, 4),
        "dam_sire_win_rate": round(dam_raw, 4),
        "dam_sire_sample": dam_n,
        "dam_sire_adj": round(dam_adj, 4),
        "blended_win_rate": round(blended, 4),
        "global_avg": round(g, 4),
        "is_first_surface": inp.is_first_surface,
        "distance_change_m": inp.distance_change_m,
        # --- 文言生成用メタ ---
        "sire_name": inp.sire_name,
        "sire_distance_win_rate": round(sire_raw, 4),
        "sire_sample_size": sire_n,
        "dam_sire_name": inp.dam_sire_name,
        "dam_sire_distance_win_rate": round(dam_raw, 4),
        "dam_sire_sample_size": dam_n,
        "distance_bucket": inp.context.get("distance_bucket", ""),
        "surface": inp.context.get("surface", ""),
        "distance_m": inp.distance_m,
        "bayes_adjusted": bayes_adjusted,
        "score": round(score, 2),
        **inp.context,
    }
    return (round(score, 2), confidence, breakdown)
