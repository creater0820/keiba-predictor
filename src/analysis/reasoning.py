"""スコアの breakdown を「なぜその点数か」の日本語短文に変換する純粋関数。

I/O は持たない。各 *_score.py が返す（拡張済み）breakdown dict を受け取り、
競馬ファンに通じる平易な言葉で 1〜2 行の理由を返す。専門用語は最小限にし、
使う場合は括弧で補足する（例: 位置率0.35（先頭から35%地点））。

breakdown に想定キーが無くても落ちないよう、各関数は防御的に書く。
"""

from __future__ import annotations

_SURFACE_JP = {"turf": "芝", "dirt": "ダート", "jump": "障害"}
_DEFAULT = "詳細情報なし"


def reason_track_bias(bd: dict) -> str:
    """トラックバイアス（馬場傾向×枠×脚質）の理由を返す。"""
    try:
        n_races = bd.get("bias_n_races", 0)
        if not n_races:
            return "馬場傾向データ不足のため中立評価（補正なし）。"

        post = int(bd.get("horse_post_position", bd.get("post_position", 0)) or 0)
        style = bd.get("horse_running_style", bd.get("running_style", "")) or ""
        io = float(bd.get("bias_inside_outside", 0.0) or 0.0)
        pace = float(bd.get("bias_pace", 0.0) or 0.0)
        matched = bd.get("matched_advantage", []) or []

        frame_word = "内枠" if 0 < post <= 4 else ("外枠" if post else "")
        style_part = f"×{style}" if style and style != "不明" else ""
        date_label = _fmt_date(bd.get("bias_data_date", ""))
        venue = bd.get("bias_venue", "") or ""
        io_word = _io_word(io)
        pace_word = "前残り" if pace < -0.05 else ("差し有利" if pace > 0.05 else "フラット")

        head = "・".join(matched) if matched else "馬場傾向の影響は小さめ"
        lead = f"{frame_word}（{post}枠）{style_part}" if frame_word else "枠・脚質"
        return (
            f"{lead}：{head}。"
            f"{date_label}{venue}馬場 内外{io:+.2f}（{io_word}）・ペース{pace:+.2f}（{pace_word}）"
        )
    except Exception:
        return _DEFAULT


def reason_pedigree(bd: dict) -> str:
    """血統（父・母父の距離×馬場成績）の理由を返す。"""
    try:
        s_n = int(bd.get("sire_sample_size", 0) or 0)
        d_n = int(bd.get("dam_sire_sample_size", 0) or 0)
        if s_n == 0 and d_n == 0:
            return "血統データ未取得のため中立評価（全体平均で評価）。"

        sname = bd.get("sire_name") or "父"
        dname = bd.get("dam_sire_name") or "母父"
        s_wr = float(bd.get("sire_distance_win_rate", 0.0) or 0.0)
        d_wr = float(bd.get("dam_sire_distance_win_rate", 0.0) or 0.0)
        surface = _SURFACE_JP.get(bd.get("surface", ""), "")
        dist = int(bd.get("distance_m", 0) or 0)
        cond = f"{surface}{dist}m前後" if dist else f"{surface}同距離"

        parts: list[str] = []
        if s_n > 0:
            parts.append(f"父{sname} {cond}勝率{s_wr * 100:.0f}%（n={s_n}）")
        else:
            parts.append(f"父{sname} 当該条件のデータ不足")
        if d_n > 0:
            parts.append(f"母父{dname} 同条件{d_wr * 100:.0f}%（n={d_n}）")
        text = "。".join(parts)

        if bd.get("bayes_adjusted"):
            text += "。サンプル少なめのため全体平均に寄せて評価（ベイズ補正）"
        if bd.get("is_first_surface"):
            text += "。初の馬場種別で減点"
        return text
    except Exception:
        return _DEFAULT


def reason_running_style(bd: dict) -> str:
    """脚質（推定脚質×想定ペース×枠）の理由を返す。"""
    try:
        style = bd.get("style", "不明")
        if style == "不明":
            n = int(bd.get("style_confidence_n_races", 0) or 0)
            extra = f"（過去走{n}走のみ）" if n else ""
            return f"過去走が少なく脚質を推定できないため中立評価{extra}。"

        n = int(bd.get("style_confidence_n_races", 0) or 0)
        avg = bd.get("avg_position_ratio")
        pace = bd.get("estimated_pace", "ミドル")
        fit = float(bd.get("fit_component", 0.0) or 0.0)

        if avg is not None:
            ratio_txt = f"（直近{n}走 平均位置率{float(avg):.2f}＝先頭から{float(avg) * 100:.0f}%地点）"
        elif n:
            ratio_txt = f"（直近{n}走から推定）"
        else:
            ratio_txt = ""

        verdict = _style_verdict(style, fit)
        text = f"{style}{ratio_txt}。想定{pace}ペース → {verdict}"

        penalty = bd.get("post_position_penalty", "")
        if penalty:
            text += f"。{penalty}（枠順減点）"
        return text
    except Exception:
        return _DEFAULT


# ----------------------------------------------------------------------
# 補助
# ----------------------------------------------------------------------
def _fmt_date(yyyymmdd: str) -> str:
    """'20260530' → '5/30 '。空や不正は ''。"""
    s = str(yyyymmdd or "")
    if len(s) == 8 and s.isdigit():
        return f"{int(s[4:6])}/{int(s[6:8])} "
    return ""


def _io_word(io: float) -> str:
    """内外バイアス値を言葉に。負=内有利、正=外有利。"""
    if io <= -0.3:
        return "内有利"
    if io < -0.05:
        return "やや内有利"
    if io >= 0.3:
        return "外有利"
    if io > 0.05:
        return "やや外有利"
    return "中立"


def _style_verdict(style: str, fit_component: float) -> str:
    """fit（展開適合の寄与）から有利/不利/互角を言葉にする。"""
    if fit_component > 2:
        return f"{style}有利"
    if fit_component < -2:
        return f"{style}には不利な展開"
    return "展開は互角"
