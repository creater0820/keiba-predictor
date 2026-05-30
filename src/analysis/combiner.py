"""3 つのスコアを重み付け合成し、softmax で各馬の勝率(確率分布)に変換する。

純粋関数（I/O なし）。スコアと信頼度は呼び出し側が用意して渡す。

合成:
    final_score = w_bias*S_bias + w_ped*S_ped + w_style*S_style
    probabilities = softmax(final_score / temperature)

方針（重要）:
    - 重みは合計 1.0 で来ない前提。w/sum(w) で正規化する。合計が 0 以下なら
      均等重み(1/3 ずつ)にフォールバックする。
    - temperature は max(0.1, t) でガード（0 割・極端な尖りを防ぐ）。
    - 総合信頼度は各 component confidence の「重み付き加重平均」。
      min や幾何平均は使わない。
    - 低信頼度の馬の確率を弱める二重補正はしない。確率は final_score のみで決まり、
      信頼度は別途バッジ表示用に返すだけ。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# 確率がほぼ均等とみなせる温度の下限（0 割防止＆過度な尖り防止）
MIN_TEMPERATURE = 0.1


@dataclass
class CombineWeights:
    """3 要素の重み（UI スライダー由来）。合計 1.0 でなくてよい。"""

    track_bias: float = 1 / 3
    pedigree: float = 1 / 3
    running_style: float = 1 / 3

    def normalized(self) -> "CombineWeights":
        """合計 1.0 に正規化した重みを返す。合計 0 以下なら均等にフォールバック。"""
        total = self.track_bias + self.pedigree + self.running_style
        if total <= 0:
            return CombineWeights(1 / 3, 1 / 3, 1 / 3)
        return CombineWeights(
            self.track_bias / total,
            self.pedigree / total,
            self.running_style / total,
        )


@dataclass
class HorseScores:
    """1 頭分の 3 スコアと信頼度（各 component の出力をまとめたもの）。"""

    horse_number: int
    horse_name: str
    track_bias: tuple[float, float]     # (score 0〜100, confidence 0〜1)
    pedigree: tuple[float, float]
    running_style: tuple[float, float]
    # 各要素の詳細 breakdown（文言生成用。省略可）
    track_bias_detail: dict = field(default_factory=dict)
    pedigree_detail: dict = field(default_factory=dict)
    running_style_detail: dict = field(default_factory=dict)


@dataclass
class HorseProbability:
    """1 頭分の最終結果（確率・スコア・信頼度・内訳）。"""

    horse_number: int
    horse_name: str
    win_probability: float   # 0.0〜1.0
    final_score: float       # 重み付き合成スコア(0〜100)
    confidence: float        # 総合信頼度(0〜1)
    breakdown: dict          # DataFrame 化しやすい平坦な dict
    # 各要素の詳細 breakdown（文言生成用。breakdown とは別に保持しフラット性を保つ）
    details: dict = field(default_factory=dict)

    @property
    def win_pct(self) -> float:
        """勝率(%)。表示用。"""
        return round(self.win_probability * 100, 2)


@dataclass
class RaceProbabilities:
    """1 レース分の確率分布（確率降順）。"""

    race_id: str
    temperature: float
    weights: CombineWeights          # 正規化済み
    horses: list[HorseProbability] = field(default_factory=list)
    meta: dict = field(default_factory=dict)  # レース情報・データソース等（UI/出力用）

    def to_rows(self) -> list[dict]:
        """各馬の breakdown を行リスト(list[dict])で返す。pandas 不要。"""
        return [h.breakdown for h in self.horses]

    def to_dataframe(self):
        """pandas.DataFrame に変換（pandas は遅延 import）。"""
        import pandas as pd  # 遅延 import: combiner 自体は pandas に依存しない

        return pd.DataFrame(self.to_rows())


def _weighted_score(scores: tuple[float, float, float], w: CombineWeights) -> float:
    """3 スコアを正規化済み重みで合成する。"""
    s_bias, s_ped, s_style = scores
    return w.track_bias * s_bias + w.pedigree * s_ped + w.running_style * s_style


def _weighted_confidence(confs: tuple[float, float, float], w: CombineWeights) -> float:
    """3 信頼度の重み付き加重平均（重みは正規化済み＝合計1なのでそのまま和）。"""
    c_bias, c_ped, c_style = confs
    return w.track_bias * c_bias + w.pedigree * c_ped + w.running_style * c_style


def _softmax(values: list[float], temperature: float) -> list[float]:
    """softmax(values / temperature)。数値安定化のため最大値を引く。"""
    if not values:
        return []
    t = max(MIN_TEMPERATURE, temperature)
    logits = [v / t for v in values]
    m = max(logits)
    exps = [math.exp(x - m) for x in logits]
    total = sum(exps)
    if total <= 0:  # 理論上起きないが安全側に均等分布
        n = len(values)
        return [1 / n] * n
    return [e / total for e in exps]


def combine_race(
    race_id: str,
    horse_scores: list[HorseScores],
    weights: CombineWeights,
    temperature: float,
) -> RaceProbabilities:
    """1 レース分の 3 スコアを合成し、各馬の勝率(確率分布)を返す。

    Args:
        race_id: レース ID。
        horse_scores: 各馬の 3 スコアと信頼度。
        weights: 3 要素の重み（合計 1.0 でなくてよい）。
        temperature: softmax 温度（0.1 未満は 0.1 にガード）。

    Returns:
        RaceProbabilities（勝率降順にソート済み）。
    """
    w = weights.normalized()
    t = max(MIN_TEMPERATURE, temperature)

    # 各馬の合成スコアと総合信頼度
    final_scores: list[float] = []
    confidences: list[float] = []
    for hs in horse_scores:
        scores = (hs.track_bias[0], hs.pedigree[0], hs.running_style[0])
        confs = (hs.track_bias[1], hs.pedigree[1], hs.running_style[1])
        final_scores.append(_weighted_score(scores, w))
        confidences.append(_weighted_confidence(confs, w))

    # softmax で確率化（全馬同スコア → 均等、1 頭 → 1.0）
    probs = _softmax(final_scores, t)

    horses: list[HorseProbability] = []
    for hs, fs, conf, p in zip(horse_scores, final_scores, confidences, probs):
        breakdown = {
            "horse_number": hs.horse_number,
            "horse_name": hs.horse_name,
            "track_bias_score": round(hs.track_bias[0], 2),
            "pedigree_score": round(hs.pedigree[0], 2),
            "running_style_score": round(hs.running_style[0], 2),
            "track_bias_conf": round(hs.track_bias[1], 3),
            "pedigree_conf": round(hs.pedigree[1], 3),
            "running_style_conf": round(hs.running_style[1], 3),
            "w_track_bias": round(w.track_bias, 3),
            "w_pedigree": round(w.pedigree, 3),
            "w_running_style": round(w.running_style, 3),
            "final_score": round(fs, 2),
            "confidence": round(conf, 3),
            "win_probability": round(p, 4),
            "win_pct": round(p * 100, 2),
        }
        horses.append(HorseProbability(
            horse_number=hs.horse_number,
            horse_name=hs.horse_name,
            win_probability=p,
            final_score=fs,
            confidence=conf,
            breakdown=breakdown,
            details={
                "track_bias": hs.track_bias_detail,
                "pedigree": hs.pedigree_detail,
                "running_style": hs.running_style_detail,
            },
        ))

    # 勝率降順にソート（同率は馬番昇順）
    horses.sort(key=lambda h: (-h.win_probability, h.horse_number))

    return RaceProbabilities(
        race_id=race_id, temperature=t, weights=w, horses=horses,
    )
