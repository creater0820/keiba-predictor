"""3 つのスコア関数（純粋関数）をダミーデータで動かす最小デモ。

ネットワーク・DB に一切触れない。スコア関数の入出力と debug_breakdown の
形を目で確認するためのもの。

実行::

    python scripts/demo_scores.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.analysis.pedigree_score import (  # noqa: E402
    PedigreeScoreInput,
    SireStat,
    score_pedigree,
)
from src.analysis.running_style_score import (  # noqa: E402
    RunningStyleScoreInput,
    score_running_style,
)
from src.analysis.combiner import (  # noqa: E402
    CombineWeights,
    HorseScores,
    combine_race,
)
from src.analysis.track_bias_score import (  # noqa: E402
    TrackBiasScoreInput,
    score_track_bias,
)


def show(title: str, result: tuple[float, float, dict]) -> None:
    score, conf, breakdown = result
    print(f"\n■ {title}")
    print(f"  score={score}  confidence={conf}")
    print("  breakdown=" + json.dumps(breakdown, ensure_ascii=False))


def main() -> None:
    print("=== 1) トラックバイアススコア ===")
    # 外有利・前残りバイアスの日に、内枠の逃げ馬 vs 外枠の差し馬
    show(
        "内枠(1)の逃げ馬 / 外有利・前残りバイアス",
        score_track_bias(TrackBiasScoreInput(
            post_position=1, running_style="逃げ",
            inside_outside_bias=0.33, pace_bias=-0.33, bias_n_races=6,
        )),
    )
    show(
        "外枠(8)の追込馬 / 外有利・前残りバイアス",
        score_track_bias(TrackBiasScoreInput(
            post_position=8, running_style="追込",
            inside_outside_bias=0.33, pace_bias=-0.33, bias_n_races=6,
        )),
    )
    show(
        "中立バイアス(データ不足) → 50・confidence 0",
        score_track_bias(TrackBiasScoreInput(
            post_position=4, running_style="先行",
            inside_outside_bias=0.0, pace_bias=0.0, bias_n_races=0,
        )),
    )

    print("\n=== 2) 血統スコア ===")
    GLOBAL = 0.075  # 全体平均勝率の例
    show(
        "好成績の父(勝率12%,標本800)＋平均的母父",
        score_pedigree(PedigreeScoreInput(
            sire=SireStat(win_rate=0.12, sample_size=800),
            dam_sire=SireStat(win_rate=0.08, sample_size=300),
            global_avg_win_rate=GLOBAL,
        )),
    )
    show(
        "標本不足の血統(父 勝率20%だが標本5) → ベイズで中立寄り・低confidence",
        score_pedigree(PedigreeScoreInput(
            sire=SireStat(win_rate=0.20, sample_size=5),
            dam_sire=None,
            global_avg_win_rate=GLOBAL,
        )),
    )
    show(
        "初ダート＋500m距離延長のペナルティ",
        score_pedigree(PedigreeScoreInput(
            sire=SireStat(win_rate=0.10, sample_size=400),
            dam_sire=SireStat(win_rate=0.09, sample_size=200),
            global_avg_win_rate=GLOBAL,
            is_first_surface=True, distance_change_m=500,
        )),
    )

    print("\n=== 3) 脚質スコア ===")
    # ハイペース想定の馬群（逃げ・先行が多い）
    hi_pace_field = ["逃げ", "逃げ", "先行", "先行", "先行", "差し", "差し", "追込"]
    show(
        "ハイペース想定での追込馬(内枠)",
        score_running_style(RunningStyleScoreInput(
            running_style="追込", post_position=2, field_styles=hi_pace_field,
        )),
    )
    show(
        "ハイペース想定での逃げ馬(大外8枠) → 展開不利＋枠減点",
        score_running_style(RunningStyleScoreInput(
            running_style="逃げ", post_position=8, field_styles=hi_pace_field,
        )),
    )
    # スローペース想定の馬群（差し・追込が多い）
    slow_field = ["差し", "差し", "差し", "追込", "追込", "先行", "逃げ", "差し"]
    show(
        "スローペース想定での逃げ馬",
        score_running_style(RunningStyleScoreInput(
            running_style="逃げ", post_position=3, field_styles=slow_field,
        )),
    )
    show(
        "脚質不明の馬 → 50・confidence 0",
        score_running_style(RunningStyleScoreInput(
            running_style="不明", post_position=5, field_styles=hi_pace_field,
        )),
    )

    print("\n=== 4) 合成 → 勝率（softmax）===")
    # ダミー 4 頭の (track_bias, pedigree, running_style) = (score, confidence)
    horses = [
        HorseScores(1, "アルファ", (72, 1.0), (65, 0.9), (60, 1.0)),
        HorseScores(2, "ブラボー", (55, 1.0), (80, 1.0), (45, 1.0)),
        HorseScores(3, "チャーリー", (50, 0.0), (50, 0.1), (50, 0.0)),  # 低信頼
        HorseScores(4, "デルタ", (60, 1.0), (58, 0.8), (75, 1.0)),
    ]
    weights = CombineWeights(track_bias=0.4, pedigree=0.3, running_style=0.3)
    for temp, label in [(1.0, "標準 t=1.0"), (0.5, "尖り t=0.5"), (2.5, "平坦 t=2.5")]:
        race = combine_race("DEMO", horses, weights, temperature=temp)
        print(f"\n  ▼ {label}（重み 内訳={race.weights.track_bias:.2f}/"
              f"{race.weights.pedigree:.2f}/{race.weights.running_style:.2f}）")
        for h in race.horses:
            print(f"    {h.horse_number} {h.horse_name:<6} "
                  f"勝率 {h.win_pct:5.1f}%  合成 {h.final_score:5.1f}  信頼度 {h.confidence:.2f}")

    print("\n✅ 3 スコア関数 + 合成すべてダミーデータで動作（I/O なし）")


if __name__ == "__main__":
    main()
