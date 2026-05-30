"""running_style.py（脚質推定）のテスト。

parse は保存済み fixture、estimate はダミーデータで検証（どちらもオフライン）。

実行::

    pytest -v tests/test_running_style.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.scraper.running_style import (  # noqa: E402
    PastRun,
    estimate_running_style,
    parse_horse_runs,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_parse_horse_runs_from_fixture():
    """実 HTML（イクイノックス）から過去走の頭数・1 コーナー順位が取れること。"""
    html = (FIXTURES / "horse_result_2019105219.html").read_text(encoding="utf-8")
    runs = parse_horse_runs(html)
    assert len(runs) >= 5
    # 全走で頭数・通過順位が正の整数
    assert all(r.field_size > 0 and r.first_corner > 0 for r in runs)
    # 1 コーナー順位は頭数以下のはず
    assert all(r.first_corner <= r.field_size for r in runs)


def test_estimate_from_fixture_returns_valid_style():
    """実 HTML から有効な脚質（不明でない）が、妥当な値域で推定されること。"""
    html = (FIXTURES / "horse_result_2019105219.html").read_text(encoding="utf-8")
    runs = parse_horse_runs(html)
    style, n_runs, bd = estimate_running_style(runs)
    assert style in ("逃げ", "先行", "差し", "追込")  # 不明でない
    assert n_runs >= 3
    assert 0.0 <= bd["avg_position_ratio"] <= 1.0


# --- estimate のロジックはダミーデータで網羅 ---
def test_estimate_escaper():
    """常に先頭付近（位置比率 ~0.07）→ 逃げ。"""
    runs = [PastRun("", 14, 1, 1) for _ in range(5)]
    style, n, _ = estimate_running_style(runs)
    assert style == "逃げ"
    assert n == 5


def test_estimate_closer():
    """常に後方（16 頭中 14 番手 ~0.875）→ 追込。"""
    runs = [PastRun("", 16, 14, 8) for _ in range(5)]
    style, _, _ = estimate_running_style(runs)
    assert style == "追込"


def test_estimate_stalker():
    """中団前め（12 頭中 4 番手 ~0.33）→ 先行。"""
    runs = [PastRun("", 12, 4, 3) for _ in range(5)]
    style, _, _ = estimate_running_style(runs)
    assert style == "先行"


def test_estimate_insufficient_runs_is_unknown():
    """走数が下限（3）未満なら不明、confidence=走数。"""
    runs = [PastRun("", 16, 2, 1), PastRun("", 16, 3, 2)]
    style, n, _ = estimate_running_style(runs)
    assert style == "不明"
    assert n == 2


def test_estimate_uses_only_recent_runs():
    """max_runs を超える古い走は使わないこと。"""
    # 直近 3 走は前、古い 5 走は後ろ → 直近重視で先行系になる
    recent = [PastRun("", 14, 2, 1) for _ in range(3)]
    old = [PastRun("", 14, 13, 10) for _ in range(5)]
    style, n, _ = estimate_running_style(recent + old, max_runs=3)
    assert n == 3
    assert style in ("逃げ", "先行")


def test_estimate_empty_is_unknown():
    """過去走ゼロなら不明・0。"""
    style, n, _ = estimate_running_style([])
    assert style == "不明"
    assert n == 0
