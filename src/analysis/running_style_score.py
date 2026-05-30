"""脚質 → 各馬のスコア(0〜100)を算出する純粋関数。

I/O は持たない。出走馬全体の脚質構成から想定ペースを推定し、各馬の脚質が
その展開に合うほど高得点にする。枠順との相互作用（大外の逃げ馬は減点）も加味。

想定ペース:
    pace_pressure = (逃げ頭数*1.0 + 先行頭数*0.5) / 出走頭数
    これが基準より高い → ハイペース想定 → 差し・追込有利
                  低い → スローペース想定 → 逃げ・先行有利

スコア:
    score = 50 + FIT_SCALE * fit + frame_penalty
      fit = pace_signed × 脚質の末脚寄り度（ハイペース×差し馬 などで正）
    脚質が「不明」の馬は中立 50・低 confidence を返す。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.analysis._common import STYLE_CLOSER_VALUE, clamp, clamp_unit

# 想定ペースの基準値（典型的な逃げ・先行構成での pace_pressure）
PACE_BASELINE = 0.20
# fit がスコアに与える振れ幅
FIT_SCALE = 30.0
# 大外の逃げ・先行馬への減点（枠比率がこの値以上で発動）
OUTSIDE_FRAME_RATIO = 0.75
FRAME_PENALTY = 10.0
MAX_FRAME = 8


@dataclass
class RunningStyleScoreInput:
    """脚質スコアの 1 頭分の入力（純粋データ）。"""

    running_style: str        # この馬の脚質（逃げ/先行/差し/追込/不明）
    post_position: int        # 枠番(1〜8)
    field_styles: list[str]   # 出走馬全頭の脚質リスト（自分を含む）


def _estimate_pace(field_styles: list[str]) -> tuple[float, int, int, int]:
    """脚質構成から想定ペース指標を返す。

    Returns:
        (pace_pressure, n_escape, n_front, n_known)。
    """
    n_total = len(field_styles)
    if n_total == 0:
        return (PACE_BASELINE, 0, 0, 0)
    n_escape = sum(1 for s in field_styles if s == "逃げ")
    n_senko = sum(1 for s in field_styles if s == "先行")
    n_known = sum(1 for s in field_styles if s in STYLE_CLOSER_VALUE and s != "不明")
    pressure = (n_escape * 1.0 + n_senko * 0.5) / n_total
    return (pressure, n_escape, n_escape + n_senko, n_known)


def score_running_style(inp: RunningStyleScoreInput) -> tuple[float, float, dict]:
    """脚質×展開の適合スコアを返す。

    Returns:
        (score 0〜100, confidence 0〜1, debug_breakdown) のタプル。
    """
    pressure, n_escape, n_front, n_known = _estimate_pace(inp.field_styles)
    field_size = len(inp.field_styles)

    # ハイペース度（-1〜+1）。基準より速いほど正（差し有利）。
    pace_signed = clamp_unit((pressure - PACE_BASELINE) / PACE_BASELINE) if PACE_BASELINE > 0 else 0.0

    style_value = STYLE_CLOSER_VALUE.get(inp.running_style, 0.0)  # 前-1〜後+1
    fit = clamp_unit(pace_signed * style_value)
    fit_component = FIT_SCALE * fit

    # 枠ペナルティ: 大外（枠比率が高い）の逃げ・先行は包まれやすく減点
    frame_ratio = inp.post_position / MAX_FRAME
    frame_penalty = 0.0
    if frame_ratio >= OUTSIDE_FRAME_RATIO and inp.running_style in ("逃げ", "先行"):
        frame_penalty = -FRAME_PENALTY

    if inp.running_style == "不明":
        # 脚質不明 → 中立・低信頼
        score = clamp(50.0)
        confidence = 0.0
        fit_component = 0.0
        frame_penalty = 0.0
    else:
        score = clamp(50.0 + fit_component + frame_penalty)
        # 信頼度: 出走馬の脚質がどれだけ判明しているか
        confidence = round((n_known / field_size) if field_size else 0.0, 3)

    breakdown = {
        "base": 50.0,
        "fit_component": round(fit_component, 2),
        "frame_penalty": round(frame_penalty, 2),
        "pace_pressure": round(pressure, 3),
        "pace_signed": round(pace_signed, 3),
        "n_escape": n_escape,
        "n_front": n_front,
        "field_size": field_size,
        "style": inp.running_style,
        "style_value": style_value,
        "post_position": inp.post_position,
        "score": round(score, 2),
    }
    return (round(score, 2), confidence, breakdown)
