"""トラックバイアス → 各馬のスコア(0〜100)を算出する純粋関数。

I/O は持たない。馬場傾向（内外・ペース）と、その馬の枠・脚質を受け取り、
「有利な側にいるほど高得点」を返す。データ不足（バイアスが中立）なら 50 に近づく。

スコアの考え方:
    score = 50 + 25*frame_alignment + 25*pace_alignment
      frame_alignment = inside_outside_bias × 枠の内外（外+1〜内-1）
      pace_alignment  = pace_bias × 脚質の末脚寄り度（差し+1〜逃げ-1）
    いずれも「バイアスの向き」と「馬の特徴」が一致すると正、逆なら負。
"""

from __future__ import annotations

from dataclasses import dataclass

from src.analysis._common import STYLE_CLOSER_VALUE, clamp, clamp_unit

# 枠番の最大値（JRA は最大 8 枠）。内外比率の正規化に使う。
MAX_FRAME = 8
# バイアスの信頼度が最大になるレース数（これ以上は頭打ち）
FULL_CONFIDENCE_RACES = 6


@dataclass
class TrackBiasScoreInput:
    """track_bias の 1 頭分の入力（純粋データ。スクレイパとは独立）。"""

    post_position: int          # 枠番(1〜8)
    running_style: str          # 逃げ/先行/差し/追込/不明
    inside_outside_bias: float  # -1(内有利) 〜 +1(外有利)
    pace_bias: float            # -1(前残り) 〜 +1(差し有利)
    bias_n_races: int = 0       # バイアス算出に使ったレース数（信頼度用）


def score_track_bias(inp: TrackBiasScoreInput) -> tuple[float, float, dict]:
    """トラックバイアス適合スコアを返す。

    Returns:
        (score 0〜100, confidence 0〜1, debug_breakdown) のタプル。
        debug_breakdown は base + frame_component + pace_component = score の形。
    """
    # 枠の内外（内=-1 〜 外=+1）。post=1→ほぼ-1、post=8→+1。
    frame_signed = clamp_unit(((inp.post_position / MAX_FRAME) - 0.5) * 2)

    # 脚質の末脚寄り度（前-1 〜 後+1）
    style_signed = STYLE_CLOSER_VALUE.get(inp.running_style, 0.0)

    # バイアスの向きと一致するほど大きい（-1〜+1）
    frame_alignment = clamp_unit(inp.inside_outside_bias * frame_signed)
    pace_alignment = clamp_unit(inp.pace_bias * style_signed)

    frame_component = 25.0 * frame_alignment
    pace_component = 25.0 * pace_alignment
    score = clamp(50.0 + frame_component + pace_component)

    # 信頼度: バイアスのレース数で決まる。脚質不明なら少し下げる。
    conf_bias = min(1.0, inp.bias_n_races / FULL_CONFIDENCE_RACES)
    style_factor = 1.0 if inp.running_style != "不明" else 0.7
    confidence = round(conf_bias * style_factor, 3)

    breakdown = {
        "base": 50.0,
        "frame_component": round(frame_component, 2),
        "pace_component": round(pace_component, 2),
        "frame_signed": round(frame_signed, 3),
        "style_signed": style_signed,
        "inside_outside_bias": inp.inside_outside_bias,
        "pace_bias": inp.pace_bias,
        "post_position": inp.post_position,
        "running_style": inp.running_style,
        "bias_n_races": inp.bias_n_races,
        "score": round(score, 2),
    }
    return (round(score, 2), confidence, breakdown)
