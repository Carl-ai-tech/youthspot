"""青年移入的驅動模型。—— 六都 158 個行政區的橫斷面迴歸（推拉理論的量化）

局長講的因果鏈：收入低＋房價高 → 外移 → 流向租金低、通勤可接受的區 → 聚集。
經濟學上是推拉理論（Lee 1966）與空間均衡（Roback 1982：人在「工資 − 租金 ＋ 生活品質」
之間移動到均衡）。這支把它量化：

    淨遷入率(區) = β₀ + β₁ ln(工作機會密度) + β₂ ln(所得中位數) + β₃ ln(每坪月租)
                 + β₄ ln(18–35 歲人口) + 縣市固定效果 + ε

  y     世代淨遷入率近三年平均（%）—— 用近三年避開 2022／2023 戶籍事件年，也壓掉單年跳動
  X     全部取 ln，係數讀成「X 高 10%，淨遷入率差幾個百分點」；縣市固定效果吸掉六都之間的水準差，
        所以比的是「同一個都會區裡」哪種區在吸青年 —— 這正是「下一個淡水」要問的
  方法  普通最小平方，正規方程 + 高斯消去，標準函式庫。158 個點、10 個參數，沒有過擬合的空間

刻意**不用機器學習**：每區只有 8 個年度點、六都只有 158 區，任何複雜模型都是在擬合雜訊，
而且係數講不出來。OLS 的每個係數都能上台講、都有標準誤。

輸出兩件事：
  1. 係數：哪些條件的區在吸青年（附 t 值、顯著與否、R²）
  2. 每區的殘差：實際移入 − 條件解釋的移入。正殘差＝有模型沒看到的拉力（社宅、捷運通車）；
     負殘差＝條件好但青年沒來，值得問為什麼
  3. 每區的 z 分數向量：前端拿來算「跟淡水最像的區」（標準化歐氏距離）

不能做的：不宣稱因果（係數是相關）；不把兩層混成一個分數；不用來預測單一年。
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.fetch_district_income import fetch_district_income  # noqa: E402
from data.fetch_district_jobs import fetch_district_jobs  # noqa: E402
from data.fetch_migration import fetch_migration  # noqa: E402
from data.fetch_population import fetch_population_by_district, latest_period  # noqa: E402
from data.fetch_rent import MIN_N, fetch_rent  # noqa: E402
from data.forecast import _t_critical  # noqa: E402
from data.sources import DATA_DIR, SIX_CITIES  # noqa: E402

OUT = DATA_DIR / "drivers.json"
Y_YEARS = 3                                   # 近三年平均
FEATURES = ["ln_jobs_density", "ln_income", "ln_rent", "ln_youth"]
LABELS = {"ln_jobs_density": "工作機會密度", "ln_income": "所得中位數", "ln_rent": "每坪月租", "ln_youth": "青年人口規模",
          "ln_rent_burden": "租金負擔（租金÷所得）"}
# 「像淡水」的四個維度：相對本市的租金（便宜）、工作機會密度、所得、規模。距離用 z 分數算
SIMILARITY = ["rel_rent", "ln_jobs_density", "ln_income", "ln_youth"]


# ── 純數學 ────────────────────────────────────────────────────────────────

def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """高斯消去（部分主元）解 a x = b。"""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < 1e-12:
            raise ValueError("設計矩陣奇異（有變數共線或全為常數）")
        m[c], m[p] = m[p], m[c]
        for r in range(n):
            if r != c and m[r][c] != 0:
                f = m[r][c] / m[c][c]
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    return [m[i][n] / m[i][i] for i in range(n)]


def _inverse(a: list[list[float]]) -> list[list[float]]:
    n = len(a)
    cols = [_solve(a, [1.0 if i == j else 0.0 for i in range(n)]) for j in range(n)]
    return [[cols[j][i] for j in range(n)] for i in range(n)]


def ols(X: list[list[float]], y: list[float], names: list[str]) -> dict:
    """OLS 含截距。回傳係數、標準誤、t、顯著、R²、殘差。"""
    n, k = len(y), len(names)
    Xc = [[1.0] + row for row in X]
    p = k + 1
    xtx = [[sum(Xc[i][a] * Xc[i][b] for i in range(n)) for b in range(p)] for a in range(p)]
    xty = [sum(Xc[i][a] * y[i] for i in range(n)) for a in range(p)]
    beta = _solve(xtx, xty)
    fitted = [sum(Xc[i][a] * beta[a] for a in range(p)) for i in range(n)]
    resid = [y[i] - fitted[i] for i in range(n)]
    sse = sum(r * r for r in resid)
    ybar = sum(y) / n
    sst = sum((v - ybar) ** 2 for v in y)
    df = n - p
    sigma2 = sse / df if df > 0 else float("nan")
    inv = _inverse(xtx)
    se = [math.sqrt(sigma2 * inv[a][a]) for a in range(p)]
    tcrit = _t_critical(df)
    # HC3 穩健標準誤：行政區之間有空間相關、殘差變異不齊，一般 se 可能低估。
    # (X'X)⁻¹ X' diag(e²/(1−h)²) X (X'X)⁻¹，h = x_i (X'X)⁻¹ x_iᵀ（帽子矩陣對角）
    hat = [sum(Xc[i][a] * sum(inv[a][b] * Xc[i][b] for b in range(p)) for a in range(p)) for i in range(n)]
    w = [resid[i] ** 2 / max(1e-9, (1 - hat[i]) ** 2) for i in range(n)]
    meat = [[sum(Xc[i][a] * w[i] * Xc[i][b] for i in range(n)) for b in range(p)] for a in range(p)]
    tmp = [[sum(inv[a][k] * meat[k][b] for k in range(p)) for b in range(p)] for a in range(p)]
    hc3 = [[sum(tmp[a][k] * inv[k][b] for k in range(p)) for b in range(p)] for a in range(p)]
    se_hc3 = [math.sqrt(max(0.0, hc3[a][a])) for a in range(p)]
    coef = {}
    for a, name in enumerate(["截距"] + names):
        t = beta[a] / se[a] if se[a] > 0 else float("nan")
        t3 = beta[a] / se_hc3[a] if se_hc3[a] > 0 else float("nan")
        coef[name] = {"b": round(beta[a], 4), "se": round(se[a], 4), "t": round(t, 2), "significant": abs(t) >= tcrit,
                      "se_hc3": round(se_hc3[a], 4), "t_hc3": round(t3, 2), "significant_hc3": abs(t3) >= tcrit}
    return {"coef": coef, "r2": round(1 - sse / sst, 3) if sst else None, "n": n, "df": df,
            "t_critical": tcrit, "fitted": fitted, "resid": resid}


# ── 面板 ─────────────────────────────────────────────────────────────────

def build_panel(*, refresh: bool = False) -> list[dict]:
    """六都每個行政區一列：y 與四個驅動變數，缺任何一個就記缺（不進迴歸，但留在表裡）。"""
    period = latest_period(refresh=refresh)
    rows = []
    for city in SIX_CITIES:
        mig = fetch_migration(city, period=period, refresh=refresh)
        pop, _ = fetch_population_by_district(city, period=period, age_range=(15, 64), refresh=refresh)
        jobs = fetch_district_jobs(city, refresh=refresh)["areas"]
        inc = fetch_district_income(city, refresh=refresh)["latest"]
        rent = fetch_rent(city, refresh=refresh, districts=list(pop))["latest"]
        for area, a in mig["areas"].items():
            if area == city:
                continue
            rates = [r for r in a["rate"][-Y_YEARS:] if r is not None]
            counts = pop.get(area) or {}
            p1564 = sum(v for ag, v in counts.items() if 15 <= ag <= 64)
            youth = sum(v for ag, v in counts.items() if 18 <= ag <= 35)
            w = (jobs.get(area) or {}).get("workers")
            inc_med = (inc.get(area) or {}).get("median")
            r = rent.get(area) or {}
            row = {"area": area, "city": city, "short": area.replace(city, ""),
                   "y": round(sum(rates) / len(rates) * 100, 3) if len(rates) == Y_YEARS else None,
                   "rate_series": a["rate"], "years": mig["years"],
                   "jobs_density": round(w / p1564, 3) if w and p1564 else None,
                   "income": round(inc_med / 10, 1) if inc_med else None,           # 萬元/年
                   "rent": r.get("median") if r.get("n", 0) >= MIN_N else None,      # 元/坪/月
                   "rent_n": r.get("n", 0), "youth": youth or None, "pop_15_64": p1564 or None}
            row["ln_jobs_density"] = math.log(row["jobs_density"]) if row["jobs_density"] else None
            row["ln_income"] = math.log(inc_med) if inc_med else None
            row["ln_rent"] = math.log(row["rent"]) if row["rent"] else None
            row["ln_youth"] = math.log(youth) if youth else None
            # 租金負擔：每坪月租 ÷ 所得中位數（局長的因果鏈是「收入低＋房價高」，是相對的不是絕對的）
            row["ln_rent_burden"] = math.log(row["rent"] / inc_med) if row["rent"] and inc_med else None
            rows.append(row)
    # 相對本市的租金：ln(租金) − 本市各區 ln(租金) 平均。「便宜」是相對同一個都會區講的
    for city in SIX_CITIES:
        vals = [r["ln_rent"] for r in rows if r["city"] == city and r["ln_rent"] is not None]
        mean = sum(vals) / len(vals) if vals else None
        for r in rows:
            if r["city"] == city:
                r["rel_rent"] = round(r["ln_rent"] - mean, 4) if r["ln_rent"] is not None and mean is not None else None
    return rows, period


def _fit(rows: list[dict], features: list[str], tag: str) -> dict:
    complete = [r for r in rows if r["y"] is not None and all(r[f] is not None for f in features)]
    cities = SIX_CITIES[1:]                         # 新北市當基準
    names = features + [f"fe_{c}" for c in cities]
    X = [[r[f] for f in features] + [1.0 if r["city"] == c else 0.0 for c in cities] for r in complete]
    y = [r["y"] for r in complete]
    res = ols(X, y, names)
    for r, fv, rv in zip(complete, res["fitted"], res["resid"]):
        r[f"fitted{tag}"] = round(fv, 3); r[f"resid{tag}"] = round(rv, 3)

    def corr(f):
        xs = [r[f] for r in complete]
        mx, my = sum(xs) / len(xs), sum(y) / len(y)
        num = sum((a - mx) * (b - my) for a, b in zip(xs, y))
        den = math.sqrt(sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in y))
        return round(num / den, 2) if den else None

    sdy = _sd(y)
    for f in features:
        c = res["coef"][f]
        c["beta_std"] = round(c["b"] * _sd([r[f] for r in complete]) / sdy, 3)   # 標準化 β：哪個因子影響大
        c["label"] = LABELS[f]
        c["per_10pct"] = round(c["b"] * math.log(1.1), 3)                       # X 高 10% → y 差幾 pp
    res["corr"] = {f: corr(f) for f in features}
    res["features"] = features
    res.pop("fitted"); res.pop("resid")
    return res


def _sd(vals):
    m = sum(vals) / len(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))


def fit_drivers(rows: list[dict]) -> dict:
    """兩個模型：A 不含租金（六都行政區全進）、B 含租金（租金樣本 ≥ 30 的區）。
    租金缺的多半是郊區 —— 正是流出區，只跑含租金的模型會把它們丟掉，所以兩個都報。"""
    base = _fit(rows, [f for f in FEATURES if f != "ln_rent"], "")
    full = _fit(rows, FEATURES, "_full")
    # 模型 C：把局長的因果模型放進去 —— 租金相對所得的負擔，而不是絕對租金
    burden = _fit(rows, ["ln_jobs_density", "ln_rent_burden", "ln_youth"], "_burden")
    # z 分數（相似度用）
    stats = {}
    for f in SIMILARITY:
        vals = [r[f] for r in rows if r.get(f) is not None]
        stats[f] = (sum(vals) / len(vals), _sd(vals))
    for r in rows:
        z = {}
        for f in SIMILARITY:
            if r.get(f) is not None:
                m, s = stats[f]
                z[f] = round((r[f] - m) / s, 3)
        r["z"] = z if len(z) == len(SIMILARITY) else None
    return {"base": base, "full": full, "burden": burden}


def similar_to(rows: list[dict], area: str, k: int = 8) -> list[dict]:
    """跟某區條件最像的區（z 分數歐氏距離），附它們目前的移入狀況。"""
    ref = next((r for r in rows if r["area"] == area), None)
    if not ref or not ref.get("z"):
        return []
    out = []
    for r in rows:
        if r is ref or not r.get("z"):
            continue
        d = math.sqrt(sum((r["z"][f] - ref["z"][f]) ** 2 for f in SIMILARITY))
        out.append({"area": r["area"], "city": r["city"], "distance": round(d, 2), "y": r["y"],
                    "rent": r["rent"], "jobs_density": r["jobs_density"], "income": r["income"], "youth": r["youth"]})
    return sorted(out, key=lambda x: x["distance"])[:k]


KEEP = ("area", "city", "short", "y", "rate_series", "jobs_density", "income", "rent", "rent_n", "youth",
        "rel_rent", "fitted", "resid", "fitted_full", "resid_full", "z")


def build(*, refresh: bool = False) -> dict:
    rows, period = build_panel(refresh=refresh)
    models = fit_drivers(rows)
    years = rows[0]["years"]
    return {
        "spec": ("淨遷入率(區, 近三年平均) = β₀ + β₁ ln(工作機會密度) + β₂ ln(所得中位數) + β₃ ln(每坪月租) "
                 "+ β₄ ln(18–35 歲人口) + 縣市固定效果；OLS。模型 A 不含租金（全部行政區）、模型 B 含租金"),
        "y": f"18–35 歲世代淨遷入率 {years[-Y_YEARS]}–{years[-1]} 平均（%）",
        "period": period, "features": FEATURES, "labels": LABELS, "similarity": SIMILARITY,
        "models": models,
        "excluded_full": [f"{r['area']}（缺：{'、'.join(LABELS.get(f, f) for f in FEATURES if r[f] is None)}{'、近三年淨遷入' if r['y'] is None else ''}）"
                          for r in rows if r["y"] is None or any(r[f] is None for f in FEATURES)],
        "districts": [{k: r[k] for k in KEEP if k in r} for r in rows],
        "years": years,
        "reference_similar": {"新北市淡水區": similar_to(rows, "新北市淡水區")},
        "sources": ["內政部戶政司 ODRP014（世代淨遷入、人口）", "行政院主計總處 110 年工商普查（在地工作機會）",
                    "財政部財政資訊中心 綜稅各區所得中位數", "內政部地政司 實價登錄租賃案件（每坪月租中位數）"],
        "caveats": ["係數是相關不是因果", "租金樣本偏向代管／包租物件（110 年 7 月起才強制申報），郊區樣本常不足",
                    "工作機會是 110 年底普查，五年一次", "戶籍 ≠ 實際居住", "近三年平均，不預測單一年"],
    }


def main(argv: list[str]) -> int:
    out = build(refresh="--refresh" in argv)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{out['y']}\n{out['spec']}")
    for tag, title in (("base", "模型 A：不含租金（六都全部行政區）"), ("full", "模型 B：含租金（租金樣本 ≥ 30 筆的區）"),
                       ("burden", "模型 C：租金負擔（租金÷所得）取代所得與租金")):
        m = out["models"][tag]
        print(f"\n{title}　n = {m['n']}，R² = {m['r2']}，t 臨界 {m['t_critical']}")
        print(f"{'變數':<10}{'係數':>9}{'標準誤':>9}{'t':>7}{'顯著':>5}{'高10%→pp':>10}{'標準化β':>9}{'單相關':>8}")
        for f in m["features"]:
            c = m["coef"][f]
            print(f"{LABELS[f]:<10}{c['b']:>9.3f}{c['se']:>9.3f}{c['t']:>7.2f}{'★' if c['significant'] else '':>5}{c['per_10pct']:>10.3f}{c['beta_std']:>9.2f}{m['corr'][f]:>8.2f}")
        for c in SIX_CITIES[1:]:
            cc = m["coef"][f"fe_{c}"]
            print(f"{'固定效果 ' + c:<10}{cc['b']:>9.3f}{cc['se']:>9.3f}{cc['t']:>7.2f}{'★' if cc['significant'] else '':>5}")
    print(f"\n模型 B 未納入 {len(out['excluded_full'])} 區（多半租金樣本不足）")
    print("\n跟淡水最像的區：")
    for s in out["reference_similar"]["新北市淡水區"]:
        print(f"  {s['area']:<10} 距離 {s['distance']:.2f}  近三年淨遷入 {s['y']:+.2f}%  租金 {s['rent']} 元/坪  密度 {s['jobs_density']}  所得 {s['income']} 萬")
    ds = [d for d in out["districts"] if d.get("resid") is not None]
    print("\n殘差最大（條件之外還有拉力）：", "、".join(f"{d['area']} {d['resid']:+.1f}" for d in sorted(ds, key=lambda d: -d["resid"])[:5]))
    print("殘差最小（條件好但青年沒來）：", "、".join(f"{d['area']} {d['resid']:+.1f}" for d in sorted(ds, key=lambda d: d["resid"])[:5]))
    print(f"\n→ {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
