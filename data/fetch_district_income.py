"""各行政區綜合所得中位數。—— 財政部財政資訊中心「綜稅綜合所得總額各縣市鄉鎮村里統計分析表」，每年

這是**唯一**有行政區細分的所得資料。主計總處的薪資與人力調查都是抽樣，
樣本撐不到行政區；財政部這份是**全體申報戶的稅籍資料**，不是抽樣，
所以能細到村里。固定網址、每年八月更新、CSV，可以完全自動化：

    https://www.fia.gov.tw/WEB/fia/ias/ias{民國年}/{民國年}_165-F.csv      （165 = 新北市）

⚠️ 兩個口徑要講清楚：
  1. 這是「納稅單位（申報戶）」的綜合所得，不是青年、也不是薪資。年齡層寫「全體」。
  2. **108 年度起定義改變**（109 年公布的檔名從「所得總額」改成「綜合所得總額」，
     中位數整體下修約三成，新莊 627 → 422 千元）。跨越 108 年的比較沒有意義，
     所以 trends 只保留 108 年以後；跨區比較只用同一年度。

每年一道檢查：29 個行政區都在、中位數在 200–2000 千元之間、納稅單位 > 0。
"""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.sources import fetch  # noqa: E402

URL = "https://www.fia.gov.tw/WEB/fia/ias/ias{y}/{y}_165-F.csv"
DATASET = "財政部財政資訊中心 綜稅綜合所得總額各縣市鄉鎮村里統計分析表（新北市）"
LANDING = "https://data.gov.tw/dataset/17975"
FIRST_YEAR, LAST_YEAR = 101, 112          # 民國；找不到最新年會自動退一年
BREAK_YEAR = 108                           # 定義改變，跨越這年不比較
DISTRICTS = 29


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp950"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise RuntimeError("財政部 CSV 不是 UTF-8 也不是 Big5")


def _parse(text: str) -> dict[str, dict[str, float]]:
    """{行政區: {"units", "median", "mean", "q1", "q3"}}，只取每區的「合計」列。單位：千元。"""
    rows = list(csv.reader(io.StringIO(text)))
    out: dict[str, dict[str, float]] = {}
    for r in rows[1:]:
        if len(r) < 8 or r[1].strip() not in ("合計", "總計"):
            continue
        area = r[0].strip().replace("台", "臺")
        # 「新北市其他」是無法歸入行政區的申報戶，不是一個區
        if not area.startswith("新北市") or area == "新北市" or not area.endswith("區"):
            continue
        try:
            out[area] = {"units": float(r[2]), "total": float(r[3]), "mean": float(r[4]),
                         "median": float(r[5]), "q1": float(r[6]), "q3": float(r[7])}
        except ValueError:
            continue
    return out


def _verify(year: int, d: dict[str, dict[str, float]]) -> None:
    if len(d) != DISTRICTS:
        raise RuntimeError(f"{year} 年只讀到 {len(d)} 個行政區，應為 {DISTRICTS}：{sorted(d)[:5]}…")
    for area, v in d.items():
        if not (200 <= v["median"] <= 2000):
            raise RuntimeError(f"{year} 年 {area} 中位數 {v['median']} 千元不合理")
        if v["units"] <= 0 or v["mean"] < v["median"] * 0.8:
            raise RuntimeError(f"{year} 年 {area} 納稅單位或平均數不合理：{v}")


def fetch_district_income(*, refresh: bool = False) -> dict:
    """回傳 {"years": [西元], "latest_year", "latest": {區: {...}}, "series": {區: {"median": [...]}}, ...}"""
    by_year: dict[int, dict[str, dict[str, float]]] = {}
    for y in range(FIRST_YEAR, LAST_YEAR + 2):            # 多試一年，新檔出了就自動吃到
        try:
            raw = fetch(URL.format(y=y), f"fia_income_{y}.csv", refresh=refresh)
        except Exception as exc:  # noqa: BLE001
            if y > LAST_YEAR:
                break
            raise RuntimeError(f"{y} 年度財政部 CSV 抓不到：{exc}") from exc
        d = _parse(_decode(raw))
        _verify(y, d)
        by_year[y + 1911] = d
    years = sorted(by_year)
    latest = years[-1]
    areas = sorted(by_year[latest])
    post = [y for y in years if y >= BREAK_YEAR + 1911]
    return {
        "source": DATASET, "url": LANDING, "unit": "千元",
        "scope": "全體申報戶（非青年）",
        "years": post, "all_years": years, "latest_year": latest,
        "break_year": BREAK_YEAR + 1911,
        "latest": by_year[latest],
        "series": {a: {"median": [by_year[y][a]["median"] for y in post],
                       "units": [by_year[y][a]["units"] for y in post]} for a in areas},
        "note": ("納稅單位＝申報戶，是全體申報戶的綜合所得（含薪資、利息、股利…），不是青年、不是薪資。"
                 f"{BREAK_YEAR} 年度起定義改變、中位數整體下修，跨越該年不比較。"),
    }


if __name__ == "__main__":
    d = fetch_district_income()
    print(f"{DATASET}\n{d['all_years'][0]}–{d['all_years'][-1]}　最新 {d['latest_year']}　{len(d['latest'])} 區\n")
    top = sorted(d["latest"].items(), key=lambda kv: -kv[1]["median"])
    for a, v in top[:5] + [("…", None)] + top[-3:]:
        print(f"  {a:<10}{'' if v is None else f'{v['median']:>7.0f} 千元　{v['units']:>8,.0f} 戶'}")
