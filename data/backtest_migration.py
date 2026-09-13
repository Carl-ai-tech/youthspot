"""世代淨遷入的回測（hindcast）。—— 「訊號到底提前多久」的答案

做法：假裝現在是 T 年（2022、2023、2024），只用 T 年以前的序列跑跟正式一樣的
線性外推＋訊號判定，再拿 T+1、T+2 的實際值對答案。兩個問題：

  1. 預測誤差：外推 T+1 的淨遷入率跟實際差多少（MAE），跟「沿用去年值」「取歷年平均」比
  2. 訊號命中：T 年標「持續移入／轉為移入」的區，T+1～T+2 平均是不是真的淨移入（precision）；
     反過來，T+1～T+2 真的淨移入 ≥ 0.5% 的區，T 年有幾成被標到（recall）

刻意把 2022（疫情除籍）、2023（恢復戶籍）兩個戶籍事件年留在序列裡 —— 那是真實會遇到的雜訊，
拿掉才是作弊。結果會顯示：含事件年的 cutoff 表現較差，這是誠實的限制。

局長點名的區（淡水、林口、汐止、新莊）另外列出每個 cutoff 的訊號，上台直接講。

跟 build_unified._migration_trend 用同一個 classify()，回測跟正式產出不會兩套邏輯。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.fetch_migration import fetch_migration  # noqa: E402
from data.forecast import MIN_POINTS, _t_critical, fit  # noqa: E402
from data.sources import DATA_DIR, SIX_CITIES  # noqa: E402

OUT = DATA_DIR / "backtest_migration.json"
CUTOFFS = (2022, 2023, 2024)
INFLOW = ("持續移入", "轉為移入")
OUTFLOW = ("持續流出", "流出加劇", "轉為流出")
HIT_RATE = 0.005          # T+1～T+2 平均 ≥ 0.5% 算「真的移入」
FLAT_BAND = 0.01          # 平均的 95% 信賴區間整段落在 ±1% 內 → 持平
NAMED = ["新北市淡水區", "新北市林口區", "新北市汐止區", "新北市新莊區"]


SHOCK_PAIR = (2022, 2023)   # 疫情出境滿兩年除籍（2022/7 期）與恢復戶籍（2023/7 期）：一負一正，互相抵銷


def neutralize_shock(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """兩個事件年都在序列裡時，**合併成一個時間點** t = 2022.5、值取兩者平均，n 從 8 變 7。
    除籍與恢復是同一批人，平均掉之後剩下的才是搬遷。先前的做法是兩點都換成平均但仍當兩個觀測 ——
    兩個一模一樣的點會人為縮小殘差、放大 t 值（評估報告 2-1 抓到：新莊因此被判成顯著轉為流出）。
    只有其中一年（回測 cutoff 2022）就不動 —— 那是誠實的限制，回測結果也會反映。"""
    d = dict(pts)
    if all(y in d for y in SHOCK_PAIR):
        m = sum(d[y] for y in SHOCK_PAIR) / len(SHOCK_PAIR)
        for y in SHOCK_PAIR:
            del d[y]
        d[sum(SHOCK_PAIR) / len(SHOCK_PAIR)] = m
    return sorted(d.items())


def classify(pts: list[tuple[int, float]]) -> dict | None:
    """跟正式產出同一套：水準（連續正／負）＋方向（斜率顯著）。pts 至少 3 點。
    2022／2023 事件年先互相抵銷（neutralize_shock），再做所有判定。"""
    if len(pts) < 3:
        return None
    last_year = max(x for x, _ in pts)          # 原始序列的最後一年；合併事件年後最後一個 x 可能是 2022.5
    pts = neutralize_shock(pts)
    tr = fit(pts)
    if tr is None:
        return None
    pos_years = sum(1 for _, r in pts if r > 0)
    last = pts[-1][1]
    d = tr.direction()
    if pos_years >= len(pts) - 1 and last > 0:
        signal = "持續移入" if d != "down" else "移入減速"
    elif pos_years <= 1 and last < 0:
        signal = "流出加劇" if d == "down" else "持續流出"
    elif d == "up":
        signal = "轉為移入"
    elif d == "down":
        signal = "轉為流出"
    else:
        signal = "方向不明"
    # F06：預測時點一律是「原始最後一年 + 1」。cutoff 2023 合併後最後 x 是 2022.5，
    # 之前用 pts[-1][0] + 1 = 2023.5 去對 2024 的實際值，提前了半年。
    yhat, margin = tr.predict(last_year + 1)
    # 水準檢定：這個區「平均起來」是不是真的在淨移入／流出（單樣本 t，H0：平均 = 0）。
    # 跟斜率檢定是兩件事：淡水每年都 +2～4%，斜率 ≈ 0（不顯著）但水準非常顯著 ——
    # 「持續移入」靠的是水準，訊號的信心度也要看水準，不然穩定的區反而被標成低信心。
    from math import sqrt
    ys = [r for _, r in pts]
    n = len(ys)
    mean = sum(ys) / n
    sd = sqrt(sum((y - mean) ** 2 for y in ys) / (n - 1)) if n > 1 else 0.0
    level_t = (mean / (sd / sqrt(n))) if sd > 0 else (float("inf") if mean else 0.0)
    level_crit = _t_critical(n - 1)
    level_sig = abs(level_t) >= level_crit
    level_edge = level_t not in (float("inf"), float("-inf")) and 0.8 * level_crit <= abs(level_t) <= 1.2 * level_crit
    # 持平：不是沒訊號，是「平均在 0 附近而且我們很確定」—— 平均的 95% 信賴區間整段落在 ±FLAT_BAND 內
    # （等價性檢定的做法）。新莊、板橋、中和都是這種：青年沒有在走，也沒有在來。
    ci = _t_critical(n - 1) * sd / sqrt(n) if sd > 0 else 0.0
    # 方向訊號只是邊緣顯著、而平均又確定在 0 附近 → 那個「轉向」站不住，改判持平（仍標邊緣）
    dir_edge = signal in ("轉為移入", "轉為流出", "移入減速", "流出加劇") and tr.edge
    if (signal == "方向不明" or dir_edge) and abs(mean) + ci <= FLAT_BAND:
        signal = "持平"
    # 邊緣：決定這個訊號的那個檢定，|t| 落在臨界值 ±20% 內。換一種合理的算法（合併／剔除事件年）就可能翻，
    # 所以信心一律低、訊號標「邊緣」—— 這是誠實地說「我們的方法裡有不確定」。
    edge = False
    if signal in ("持續移入", "持續流出"):
        edge = level_edge
        confidence = "medium" if level_sig and n >= MIN_POINTS and not edge else "low"
    elif signal == "持平":
        edge = dir_edge
        confidence = "medium" if n >= MIN_POINTS and not edge else "low"
    elif signal in ("移入減速", "流出加劇", "轉為移入", "轉為流出"):
        edge = tr.edge
        confidence = "low" if edge else tr.confidence            # 方向的訊號看斜率
    else:
        confidence = "low"
    return {"signal": signal, "forecast": yhat, "margin": margin, "slope": tr.slope,
            "significant": tr.significant, "confidence": confidence, "n": n, "trend": tr,
            "mean": mean, "sd": sd, "level_t": level_t, "level_significant": level_sig, "mean_ci": ci,
            "edge": edge, "points": pts, "forecast_year": last_year + 1}


def _hindcast_city(region: str, period: str | None) -> dict:
    mig = fetch_migration(region, period=period)
    years = mig["years"]
    out = {"region": region, "cutoffs": {}, "named": {}}
    for T in CUTOFFS:
        if T not in years or T + 1 not in years:
            continue
        rows = []
        for area, a in mig["areas"].items():
            if area == region:
                continue
            series = [(y, r) for y, r in zip(years, a["rate"]) if r is not None]
            past = [(y, r) for y, r in series if y <= T]
            fut = {y: r for y, r in series if y > T}
            c = classify(past)
            if not c or (T + 1) not in fut:
                continue
            actual1 = fut[T + 1]
            actual2 = [fut[y] for y in (T + 1, T + 2) if y in fut]
            fut_mean = sum(actual2) / len(actual2)
            rows.append({
                "area": area, "signal": c["signal"], "forecast": c["forecast"], "actual": actual1,
                "last": past[-1][1], "mean": sum(r for _, r in past) / len(past), "future_mean": fut_mean,
                "mean3": sum(r for _, r in past[-3:]) / len(past[-3:]),
            })
            if area in NAMED:
                out["named"].setdefault(area, {})[T] = {
                    "signal": c["signal"], "forecast": round(c["forecast"] * 100, 2), "actual_next": round(actual1 * 100, 2),
                    "future_mean": round(fut_mean * 100, 2), "n": c["n"],
                }
        if not rows:
            continue
        n = len(rows)
        mae = lambda key: sum(abs(r[key] - r["actual"]) for r in rows) / n * 100  # noqa: E731
        flagged_in = [r for r in rows if r["signal"] in INFLOW]
        flagged_out = [r for r in rows if r["signal"] in OUTFLOW]
        truly_in = [r for r in rows if r["future_mean"] >= HIT_RATE]
        truly_out = [r for r in rows if r["future_mean"] <= -HIT_RATE]
        hit_in = [r for r in flagged_in if r["future_mean"] > 0]
        hit_out = [r for r in flagged_out if r["future_mean"] < 0]
        # 對照組：不用我們的訊號，只看「去年正負」或「近三年平均正負」會多準？方向的價值要跟這個比，不是跟 0 比
        naive_last_in = [r for r in rows if r["last"] > 0]; naive_mean3_in = [r for r in rows if r["mean3"] > 0]
        naive_last_out = [r for r in rows if r["last"] < 0]
        base_pos = sum(1 for r in rows if r["future_mean"] > 0)
        out["cutoffs"][T] = {
            "n": n, "points_used": T - years[0] + 1,
            "mae_model_pp": round(mae("forecast"), 2), "mae_last_pp": round(mae("last"), 2), "mae_mean_pp": round(mae("mean"), 2),
            "inflow_flagged": len(flagged_in), "inflow_hits": len(hit_in),
            "inflow_precision": round(len(hit_in) / len(flagged_in), 2) if flagged_in else None,
            "inflow_recall": round(sum(1 for r in truly_in if r["signal"] in INFLOW) / len(truly_in), 2) if truly_in else None,
            "truly_inflow": len(truly_in),
            "outflow_flagged": len(flagged_out), "outflow_hits": len(hit_out),
            "outflow_precision": round(len(hit_out) / len(flagged_out), 2) if flagged_out else None,
            "outflow_recall": round(sum(1 for r in truly_out if r["signal"] in OUTFLOW) / len(truly_out), 2) if truly_out else None,
            "truly_outflow": len(truly_out),
            "naive_last_flagged": len(naive_last_in), "naive_last_hits": sum(1 for r in naive_last_in if r["future_mean"] > 0),
            "naive_mean3_flagged": len(naive_mean3_in), "naive_mean3_hits": sum(1 for r in naive_mean3_in if r["future_mean"] > 0),
            "naive_last_out_flagged": len(naive_last_out), "naive_last_out_hits": sum(1 for r in naive_last_out if r["future_mean"] < 0),
            "base_positive": base_pos,
            "flagged_in_names": [r["area"].replace(region, "") for r in flagged_in],
            "missed_in_names": [r["area"].replace(region, "") for r in truly_in if r["signal"] not in INFLOW],
        }
    return out


def build(regions: list[str] | None = None, *, period: str | None = None) -> dict:
    regions = regions or SIX_CITIES
    cities = {r: _hindcast_city(r, period) for r in regions}
    # 六都合計
    pooled = {}
    for T in CUTOFFS:
        rows = [c["cutoffs"][T] for c in cities.values() if T in c["cutoffs"]]
        if not rows:
            continue
        n = sum(r["n"] for r in rows)
        w = lambda k: round(sum(r[k] * r["n"] for r in rows) / n, 2)  # noqa: E731
        fi, hi = sum(r["inflow_flagged"] for r in rows), sum(r["inflow_hits"] for r in rows)
        fo, ho = sum(r["outflow_flagged"] for r in rows), sum(r["outflow_hits"] for r in rows)
        ti = sum(r["truly_inflow"] for r in rows); to = sum(r["truly_outflow"] for r in rows)
        ri = sum(round(r["inflow_recall"] * r["truly_inflow"]) for r in rows if r["inflow_recall"] is not None)
        ro = sum(round(r["outflow_recall"] * r["truly_outflow"]) for r in rows if r["outflow_recall"] is not None)
        nl_f, nl_h = sum(r["naive_last_flagged"] for r in rows), sum(r["naive_last_hits"] for r in rows)
        nm_f, nm_h = sum(r["naive_mean3_flagged"] for r in rows), sum(r["naive_mean3_hits"] for r in rows)
        no_f, no_h = sum(r["naive_last_out_flagged"] for r in rows), sum(r["naive_last_out_hits"] for r in rows)
        pooled[T] = {"n": n, "points_used": rows[0]["points_used"],
                     "base_positive_share": round(sum(r["base_positive"] for r in rows) / n, 2),
                     "naive_last_precision": round(nl_h / nl_f, 2) if nl_f else None, "naive_last_flagged": nl_f, "naive_last_hits": nl_h,
                     "naive_mean3_precision": round(nm_h / nm_f, 2) if nm_f else None, "naive_mean3_flagged": nm_f, "naive_mean3_hits": nm_h,
                     "naive_last_out_precision": round(no_h / no_f, 2) if no_f else None,
                     "mae_model_pp": w("mae_model_pp"), "mae_last_pp": w("mae_last_pp"), "mae_mean_pp": w("mae_mean_pp"),
                     "inflow_flagged": fi, "inflow_hits": hi, "inflow_precision": round(hi / fi, 2) if fi else None,
                     "inflow_recall": round(ri / ti, 2) if ti else None, "truly_inflow": ti,
                     "outflow_flagged": fo, "outflow_hits": ho, "outflow_precision": round(ho / fo, 2) if fo else None,
                     "outflow_recall": round(ro / to, 2) if to else None, "truly_outflow": to}
    return {
        "method": ("假裝現在是 T 年，只用 T 年以前的世代淨遷入率跑正式的線性外推＋訊號判定，對 T+1、T+2 的實際值。"
                   "2022／2023 戶籍事件年合併成一個時間點（t = 2022.5，值取平均；cutoff 2022 只有一點、合併不了）。"),
        "hit_rate": HIT_RATE, "cutoffs": list(CUTOFFS), "pooled": pooled, "cities": cities,
        "named": {k: v for c in cities.values() for k, v in c["named"].items()},
    }


def main(argv: list[str]) -> int:
    regions = [a for a in argv if a in SIX_CITIES] or None
    out = build(regions)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("世代淨遷入回測　" + out["method"] + "\n")
    print(f"{'cutoff':<8}{'點數':>4}{'區數':>5}{'模型MAE':>9}{'沿用去年':>9}{'歷年平均':>9}{'標移入':>7}{'命中':>5}{'precision':>10}{'recall':>8}{'標流出':>7}{'命中':>5}{'precision':>10}{'recall':>8}")
    for T, r in out["pooled"].items():
        print(f"{T:<8}{r['points_used']:>4}{r['n']:>5}{r['mae_model_pp']:>9.2f}{r['mae_last_pp']:>9.2f}{r['mae_mean_pp']:>9.2f}"
              f"{r['inflow_flagged']:>7}{r['inflow_hits']:>5}{str(r['inflow_precision']):>10}{str(r['inflow_recall']):>8}"
              f"{r['outflow_flagged']:>7}{r['outflow_hits']:>5}{str(r['outflow_precision']):>10}{str(r['outflow_recall']):>8}")
    print("\n局長點名的區（每個 cutoff 的訊號 → 隔年實際）")
    for area, byT in out["named"].items():
        print("  " + area + "：" + "；".join(f"{T} 年{v['signal']}（預估 {v['forecast']:+.1f}%，實際 {v['actual_next']:+.1f}%）" for T, v in byT.items()))
    for city, c in out["cities"].items():
        for T, r in c["cutoffs"].items():
            if r["missed_in_names"] or r["flagged_in_names"]:
                print(f"  {city} {T}：標移入 {'、'.join(r['flagged_in_names']) or '—'}；漏掉 {'、'.join(r['missed_in_names']) or '—'}")
    print(f"\n→ {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
