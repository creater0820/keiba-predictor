"""5/31 東京優駿（日本ダービー）の予想を実走し、Markdown に保存する。

- 東京の同日 race_list から「優駿」「ダービー」を含むレースを自動探索。
  見つからなければ 11R、それも無ければ東京全レースを予想して別ファイルに保存。
- 部分的に失敗しても可能な範囲で Markdown を出力する（自律実装モードの方針）。
- オッズは best-effort で取得を試み、不可なら確率ベース提案にフォールバック。

実行::

    python scripts/predict_derby.py
"""

from __future__ import annotations

import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import pipeline  # noqa: E402
from src.analysis.combiner import RaceProbabilities  # noqa: E402
from src.betting.suggester import BettingSuggestion, suggest_bets  # noqa: E402
from src.scraper.client import NetkeibaClient  # noqa: E402
from src.scraper.race_list import fetch_race_list  # noqa: E402

DATE = "20260531"
VENUE = "東京"
WEIGHTS = {"bias": 0.33, "pedigree": 0.34, "style": 0.33}
TEMPERATURE = 1.0

OUT_DIR = Path("/Users/yasuakinakamura/Documents/Claude/Projects/自動化で稼ぐ/predictions")
SURFACE_JP = {"turf": "芝", "dirt": "ダート", "jump": "障害"}


# ---------------------------------------------------------------------------
# オッズ取得（best-effort）
# ---------------------------------------------------------------------------
def try_fetch_win_odds(client: NetkeibaClient, race_id: str) -> dict | None:
    """単勝オッズ（馬番→倍率）を best-effort で取得。失敗・未確定なら None。"""
    import json
    import re

    url = (
        "https://race.netkeiba.com/api/api_get_jra_odds.html"
        f"?race_id={race_id}&type=1&action=init"
    )
    try:
        raw = client.fetch(url)
        data = json.loads(raw)
        # data["data"]["odds"]["1"] = {馬番: [単勝, ...]} のような構造を想定
        odds_block = data.get("data", {}).get("odds", {}).get("1", {})
        result: dict[int, float] = {}
        for umaban, vals in odds_block.items():
            num = int(re.sub(r"\D", "", str(umaban)) or 0)
            val = vals[0] if isinstance(vals, list) else vals
            o = float(val)
            if num > 0 and o > 1.0:
                result[num] = o
        return result or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# レース探索
# ---------------------------------------------------------------------------
def find_derby(client: NetkeibaClient):
    """東京の同日レースからダービーを探す。(race_no, race_name) or None。"""
    races = fetch_race_list(client, DATE)
    tokyo = [r for r in races if r.venue == VENUE]
    for r in tokyo:
        if "優駿" in r.race_name or "ダービー" in r.race_name:
            return r.race_no, r.race_name
    # フォールバック1: 11R
    for r in tokyo:
        if r.race_no == 11:
            return 11, r.race_name
    return None, None


# ---------------------------------------------------------------------------
# Markdown 生成
# ---------------------------------------------------------------------------
def _conf_badge(c: float) -> str:
    return "🟢高" if c >= 0.7 else ("🟡中" if c >= 0.4 else "🔴低")


def build_markdown(result: RaceProbabilities, suggestion: BettingSuggestion,
                   fetched_at: str, notes: list[str]) -> str:
    m = result.meta
    lines: list[str] = []
    lines.append(f"# {m.get('race_name','(レース名不明)')} 予想 — {m.get('venue','')}{m.get('race_no','')}R")
    lines.append("")
    lines.append(f"- **開催日**: {DATE[:4]}年{int(DATE[4:6])}月{int(DATE[6:8])}日")
    lines.append(f"- **コース**: {SURFACE_JP.get(m.get('surface',''),'')}{m.get('distance_m','')}m / "
                 f"馬場 {m.get('track_condition','')} / 天候 {m.get('weather','')} / 発走 {m.get('start_time','')}")
    lines.append(f"- **出走頭数**: {m.get('field_size','')}頭")
    lines.append(f"- **トラックバイアス**: {m.get('bias_describe','')} "
                 f"(内外={m.get('bias_inside_outside')}, ペース={m.get('bias_pace')})")
    lines.append(f"- **重み**: 馬場 {result.weights.track_bias:.0%} / 血統 {result.weights.pedigree:.0%} / "
                 f"脚質 {result.weights.running_style:.0%} / temperature={result.temperature}")
    lines.append(f"- **算出日時**: {fetched_at}")
    lines.append("")

    # 確率ランキング表
    lines.append("## 確率ランキング")
    lines.append("")
    lines.append("| 順位 | 馬番 | 馬名 | 勝率% | 信頼度 | バイアス | 血統 | 脚質 | 合成 |")
    lines.append("|---:|---:|:--|---:|:--:|---:|---:|---:|---:|")
    for rank, h in enumerate(result.horses, start=1):
        b = h.breakdown
        lines.append(
            f"| {rank} | {h.horse_number} | {h.horse_name} | {h.win_pct:.1f} | "
            f"{_conf_badge(h.confidence)}({h.confidence:.2f}) | "
            f"{b['track_bias_score']:.0f} | {b['pedigree_score']:.0f} | "
            f"{b['running_style_score']:.0f} | {h.final_score:.1f} |"
        )
    lines.append("")

    # 上位5頭の推奨理由
    lines.append("## 上位5頭の推奨理由")
    lines.append("")
    for rank, h in enumerate(result.horses[:5], start=1):
        b = h.breakdown
        lines.append(f"### {rank}位: {h.horse_number} {h.horse_name}（勝率 {h.win_pct:.1f}%）")
        lines.append(_reason_text(h, m))
        lines.append("")

    # 買い目
    lines.append("## 買い目提案")
    lines.append("")
    mode = "オッズ連動（期待値ベース）" if suggestion.has_odds else "確率ベース（オッズ未取得）"
    lines.append(f"モード: **{mode}** / 予算: {suggestion.bankroll:,}円")
    lines.append("")
    if suggestion.rows:
        lines.append("| 券種 | 馬番 | 金額 | 期待値 | 理由 |")
        lines.append("|:--|:--|---:|:--:|:--|")
        for r in suggestion.rows:
            ev = f"+{r.ev_pct:.0f}%" if r.ev_pct is not None else "—"
            lines.append(f"| {r.bet_type} | {'-'.join(map(str,r.horses))} | {r.amount:,}円 | {ev} | {r.reason} |")
    else:
        lines.append(f"(提案なし) {suggestion.note}")
    if suggestion.note:
        lines.append("")
        lines.append(f"> {suggestion.note}")
    lines.append("")

    # 注意・備考
    if notes:
        lines.append("## 備考（処理上の注意）")
        lines.append("")
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("⚠️ **注意**: 本予想は netkeiba 公開情報をもとにした自動生成の参考情報です。"
                 "トラックバイアス・血統・脚質の3要素のみで算出しており、調教・展開の細部・"
                 "馬場の急変などは反映していません。**投資判断は自己責任**でお願いします。")
    return "\n".join(lines)


def _reason_text(h, meta) -> str:
    """breakdown を 2〜3 行の文章にする。"""
    b = h.breakdown
    factors = [
        ("トラックバイアス", b["track_bias_score"], b["w_track_bias"]),
        ("血統", b["pedigree_score"], b["w_pedigree"]),
        ("脚質", b["running_style_score"], b["w_running_style"]),
    ]
    # 寄与（重み×スコア）が大きい順
    contrib = sorted(factors, key=lambda x: -x[1] * x[2])
    top = contrib[0]
    parts = []
    parts.append(
        f"3要素スコアは バイアス {b['track_bias_score']:.0f} / 血統 {b['pedigree_score']:.0f} / "
        f"脚質 {b['running_style_score']:.0f}（合成 {h.final_score:.1f}）。"
    )
    parts.append(
        f"最も効いたのは「{top[0]}」（重み{top[2]:.0%}×スコア{top[1]:.0f}）。"
        f"総合信頼度は {h.confidence:.2f}（{_conf_badge(h.confidence)}）。"
    )
    # 信頼度が低い場合は注意喚起
    if h.confidence < 0.4:
        parts.append("※ データ充足度が低く、評価は不確実です（過去走・血統サンプル不足の可能性）。")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    t0 = time.monotonic()
    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # レース探索
    client = NetkeibaClient()
    try:
        race_no, race_name = find_derby(client)
    except Exception as exc:
        race_no, race_name = None, None
        notes.append(f"レース探索に失敗: {exc}")
    finally:
        client.close()

    if race_no is None:
        # フォールバック2: 東京全レースを予想
        notes.append("ダービー/優駿が見つからず、東京全レースを予想して保存します。")
        _predict_all_tokyo(notes)
        return

    print(f"対象レース: 東京 {race_no}R {race_name}")

    # 予想実行
    try:
        result = pipeline.predict_race(
            date=DATE, venue=VENUE, race_no=race_no,
            weights=WEIGHTS, temperature=TEMPERATURE,
            progress=lambda f, msg: print(f"  [{f*100:3.0f}%] {msg}"),
        )
    except Exception as exc:
        notes.append(f"予想計算が失敗しました: {exc}")
        traceback.print_exc()
        # 最低限のエラー Markdown を残す
        (OUT_DIR / "derby_20260531.md").write_text(
            f"# ダービー予想 — 生成失敗\n\n算出日時: {fetched_at}\n\nエラー: {exc}\n\n"
            + "\n".join(f"- {n}" for n in notes),
            encoding="utf-8",
        )
        return

    # オッズ（best-effort）
    odds = None
    try:
        c2 = NetkeibaClient()
        odds = try_fetch_win_odds(c2, result.meta["race_id"])
        c2.close()
    except Exception:
        odds = None
    if odds:
        notes.append(f"単勝オッズを取得（{len(odds)}頭分）→ 期待値ベースで提案。")
    else:
        notes.append("単勝オッズは未取得（未確定/取得不可）→ 確率ベースで提案。")

    if result.meta.get("bias_source", "").startswith("中立"):
        notes.append("トラックバイアスはデータ不足のため中立(50)で算出しています。")

    # 確率の校正に関する注意（0〜100スコアをlogitに使うため t=1.0 は尖りやすい）
    notes.append(
        "確率は3スコア(0〜100)をsoftmax(temperature=1.0)した相対値で、"
        "実際の勝率より尖りやすい傾向があります。より穏当な分布が欲しい場合は"
        "temperatureを2.0〜3.0に上げてください（UIのスライダーで調整可）。"
    )
    if odds:
        notes.append(
            "期待値(EV)はモデル確率×オッズで、モデルが過信気味だと過大に出ます。"
            "EVは市場との乖離の目安であり、確実な利益を意味しません。"
        )

    suggestion = suggest_bets(result, odds, bankroll=1000)
    md = build_markdown(result, suggestion, fetched_at, notes)

    out_path = OUT_DIR / "derby_20260531.md"
    out_path.write_text(md, encoding="utf-8")

    elapsed = time.monotonic() - t0
    print(f"\n✅ 保存: {out_path}")
    print(f"   所要時間: {elapsed:.0f} 秒")
    print("   上位3頭:", ", ".join(
        f"{h.horse_number}{h.horse_name}({h.win_pct:.0f}%)" for h in result.horses[:3]))


def _predict_all_tokyo(notes: list[str]) -> None:
    """フォールバック: 東京全レースを予想して 1 ファイルにまとめる。"""
    client = NetkeibaClient()
    try:
        races = [r for r in fetch_race_list(client, DATE) if r.venue == VENUE]
    finally:
        client.close()

    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sections = [f"# {VENUE} 全レース予想 — {DATE}", f"算出日時: {fetched_at}", ""]
    for r in races:
        try:
            res = pipeline.predict_race(DATE, VENUE, r.race_no, WEIGHTS, TEMPERATURE)
            sug = suggest_bets(res, None, 1000)
            sections.append(build_markdown(res, sug, fetched_at, []))
            sections.append("\n\n")
        except Exception as exc:
            sections.append(f"## {r.race_no}R {r.race_name} — 失敗: {exc}\n")
    (OUT_DIR / "tokyo_all_20260531.md").write_text("\n".join(sections), encoding="utf-8")
    print(f"✅ 保存: {OUT_DIR / 'tokyo_all_20260531.md'}")


if __name__ == "__main__":
    main()
