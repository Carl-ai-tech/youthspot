"""抓新北市自己的長期勞動力序列。—— 新北市資料開放平臺（命題建議的來源之一）

https://data.ntpc.gov.tw/api/datasets/{id}/json　免金鑰，直接回 JSON。

這份資料沒有分年齡，所以不是青年專屬。它的價值在兩點：
  1. **19 個年度（2006 起）**，是我們手上最長的新北市本地序列
  2. **可以拿來交叉驗證** —— 用它算出的失業率應該要跟主計總處表37 吻合

欄位名稱在 API 裡是 field1／percent2…percent20，完全看不出意思。
下面的對照是用數字反推並驗證出來的，例如「料理家務」男 2 千人、女 423 千人 ——
這種懸殊只可能是家務欄。`_verify()` 每次執行都會重新確認幾條恆等式。

單獨執行：python data/fetch_ntpc_labour.py
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.sources import CACHE_DIR, TIMEOUT, USER_AGENT  # noqa: E402

DATASET_ID = "13f9c688-9b29-48a6-ab91-2d03e34fc80b"
API = f"https://data.ntpc.gov.tw/api/datasets/{DATASET_ID}/json"
DATASET = "新北市資料開放平臺－勞動力（新北市政府主計處）"
LANDING = f"https://data.ntpc.gov.tw/datasets/{DATASET_ID}"
UNIT = "千人"

# 欄位對照（男／女），由數值反推並以恆等式驗證
COLS = {
    "民間人口": ("percent2", "percent3"),
    "勞動力": ("percent4", "percent5"),
    "就業": ("percent6", "percent7"),
    "失業": ("percent8", "percent9"),
    "非勞動力": ("percent10", "percent11"),
}


def _num(row: dict, key: str) -> float:
    try:
        return float(str(row.get(key, "")).replace(",", ""))
    except ValueError:
        return 0.0


def _both(row: dict, name: str) -> float:
    m, f = COLS[name]
    return _num(row, m) + _num(row, f)


def _verify(row: dict, year: str) -> None:
    """兩條恆等式。欄位若對錯，這裡會立刻抓到。"""
    pop, lab, non = _both(row, "民間人口"), _both(row, "勞動力"), _both(row, "非勞動力")
    emp, unemp = _both(row, "就業"), _both(row, "失業")
    if abs(lab + non - pop) > max(3.0, pop * 0.01):
        raise ValueError(f"{year}：勞動力 {lab} ＋ 非勞動力 {non} 對不上民間人口 {pop}")
    if abs(emp + unemp - lab) > max(3.0, lab * 0.01):
        raise ValueError(f"{year}：就業 {emp} ＋ 失業 {unemp} 對不上勞動力 {lab}")


def fetch_ntpc_labour(*, refresh: bool = False) -> list[dict]:
    """回傳逐年的 {年, 勞參率, 失業率, 就業人數…}，依年份排序。"""
    CACHE_DIR.mkdir(exist_ok=True)
    cached = CACHE_DIR / "ntpc_labour.json"
    if cached.exists() and not refresh:
        raw = json.loads(cached.read_text(encoding="utf-8"))
    else:
        req = urllib.request.Request(API, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        cached.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    out = []
    for row in raw:
        year = str(row.get("field1", "")).strip()
        if not year.isdigit():
            continue
        _verify(row, year)
        pop, lab = _both(row, "民間人口"), _both(row, "勞動力")
        pm, pf = COLS["民間人口"]
        lm, lf = COLS["勞動力"]
        pop_m, pop_f = _num(row, pm), _num(row, pf)
        lab_m, lab_f = _num(row, lm), _num(row, lf)
        out.append({
            "year": int(year),
            "population": pop,
            "labour": lab,
            "employed": _both(row, "就業"),
            "unemployed": _both(row, "失業"),
            "lfpr": round(lab / pop, 4) if pop else 0.0,
            "unemployment": round(_both(row, "失業") / lab, 4) if lab else 0.0,
            # 性別分開的勞參率（全年齡）。這是手上唯一帶性別的時間序列。
            "lfpr_m": round(lab_m / pop_m, 4) if pop_m else 0.0,
            "lfpr_f": round(lab_f / pop_f, 4) if pop_f else 0.0,
        })
    return sorted(out, key=lambda r: r["year"])


if __name__ == "__main__":
    rows = fetch_ntpc_labour()
    print(f"{DATASET}\n{len(rows)} 個年度：{rows[0]['year']}–{rows[-1]['year']}（單位 {UNIT}）\n")
    print(f"  {'年':<6}{'勞參率':>8}{'失業率':>8}{'就業':>10}")
    for r in rows:
        if r["year"] % 3 == 0 or r is rows[-1]:
            print(f"  {r['year']:<6}{r['lfpr']:>8.1%}{r['unemployment']:>8.1%}{r['employed']:>9,.0f}")
    print()
    print(f"  交叉驗證：最新一年失業率 {rows[-1]['unemployment']:.1%}"
          f"（主計總處表37 的新北市總計為 3.4%，兩者應吻合）")
