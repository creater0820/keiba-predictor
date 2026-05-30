"""analysis 配下の純粋スコア関数が共有する小さなユーティリティと定数。

ここには I/O を一切置かない（ファイル・ネットワーク・DB に触れない）。
"""

from __future__ import annotations

# 脚質を「末脚寄り度」に変換する値（負=前に行く、正=後ろから差す）
#   逃げ -1.0 / 先行 -0.5 / 差し +0.5 / 追込 +1.0 / 不明 0.0
STYLE_CLOSER_VALUE: dict[str, float] = {
    "逃げ": -1.0,
    "先行": -0.5,
    "差し": 0.5,
    "追込": 1.0,
    "不明": 0.0,
}

# 中立スコア（データ不足時の既定）
NEUTRAL_SCORE: float = 50.0


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    """value を [low, high] に収める。"""
    return max(low, min(high, value))


def clamp_unit(value: float) -> float:
    """value を [-1.0, 1.0] に収める。"""
    return max(-1.0, min(1.0, value))
