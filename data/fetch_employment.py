"""抓縣市別就業者人數。—— 主計總處人力資源調查 表32

這是引擎真正的用武之地：來源是五歲組，我們要 18-24 / 25-29 / 30-35。
其中 25-29 完全對上（high），另外兩個需要插補。

表32 同時帶教育程度，是 Spec P1-1「教育程度 × 薪資」交叉分析的來源。

版面很容易因為年度改版而位移，所以下面的欄位對照表配了三道加總檢查，
對不上就直接 raise —— 寧可失敗，不要安靜地讀到錯的欄位。

單獨執行：python data/fetch_employment.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.odsreader import clean, download_ods, to_float  # noqa: E402

TABLE32 = "https://ws.dgbas.gov.tw/001/Upload/463/relfile/11516/236078/table32.ods"
DATASET = "行政院主計總處 人力資源調查－表32 就業者之教育程度與年齡"
UNIT = "千人"
YEAR = 2025               # 民國 114 年

# 欄位索引 → 意義。改年度時先跑本檔驗證。
AGE_COLUMNS = {
    (15, 24): 12,
    (25, 29): 14,
    (30, 34): 15,
    (35, 39): 16,
    (40, 44): 17,
    (45, 49): 19,
    (50, 54): 20,
    (55, 59): 21,
    (60, 64): 22,
}
EDUCATION_COLUMNS = {"國中及以下": 3, "高級中等": 6, "大專及以上": 7}
EDUCATION_DETAIL = {"國小及以下": 4, "國中": 5, "專科": 8, "大學": 9, "研究所": 10}
COL_TOTAL, COL_25_44, COL_45_64, COL_65UP = 2, 13, 18, 23


def _verify(row: list[str], region: str) -> None:
    """三道加總檢查，確認欄位沒有位移。"""
    g = lambda i: to_float(row[i]) or 0.0

    edu = sum(g(i) for i in EDUCATION_COLUMNS.values())
    if abs(edu - g(COL_TOTAL)) > max(2.0, g(COL_TOTAL) * 0.01):
        raise ValueError(f"{region}：教育程度分項加總 {edu} 對不上總計 {g(COL_TOTAL)}，欄位可能位移")

    sub = sum(g(AGE_COLUMNS[b]) for b in [(25, 29), (30, 34), (35, 39), (40, 44)])
    if abs(sub - g(COL_25_44)) > max(2.0, g(COL_25_44) * 0.01):
        raise ValueError(f"{region}：25-44 細分加總 {sub} 對不上小計 {g(COL_25_44)}，欄位可能位移")

    big = g(AGE_COLUMNS[(15, 24)]) + g(COL_25_44) + g(COL_45_64) + g(COL_65UP)
    if abs(big - g(COL_TOTAL)) > max(2.0, g(COL_TOTAL) * 0.01):
        raise ValueError(f"{region}：年齡分組加總 {big} 對不上總計 {g(COL_TOTAL)}，欄位可能位移")


def fetch_employment(region: str = "新北市", *, refresh: bool = False) -> dict:
    """回傳 {'by_age': {(lo,hi): 千人}, 'by_education': {...}, 'total': 千人}。"""
    rows = download_ods(TABLE32, cache_name="dgbas_table32_employment.ods", refresh=refresh)

    for row in rows:
        if len(row) < 24 or not row[1]:
            continue
        name = clean(row[1])
        if not name.startswith(region):
            continue
        if to_float(row[COL_TOTAL]) is None:
            continue                       # 百分比那一段（第 43 列附近）跳過
        if abs((to_float(row[COL_TOTAL]) or 0) - 100.0) < 0.001:
            continue
        _verify(row, region)
        return {
            "region": region,
            "year": YEAR,
            "unit": UNIT,
            "dataset": DATASET,
            "total": to_float(row[COL_TOTAL]),
            "by_age": {b: to_float(row[i]) for b, i in AGE_COLUMNS.items()},
            # 65 歲以上超出引擎的 15-64 涵蓋範圍，不進 by_age，
            # 但掃描檔測驗卷需要它才湊得出表上的合計。
            "age_65up": to_float(row[COL_65UP]),
            "by_education": {k: to_float(row[i]) for k, i in EDUCATION_COLUMNS.items()},
            "by_education_detail": {k: to_float(row[i]) for k, i in EDUCATION_DETAIL.items()},
        }
    raise RuntimeError(f"表32 裡找不到 {region}")


if __name__ == "__main__":
    d = fetch_employment()
    print(f"{d['region']}　{d['year']} 年就業者　{d['total']:,.0f} {d['unit']}")
    print("  按年齡")
    for (lo, hi), v in d["by_age"].items():
        mark = "　← 與我們的 25-29 完全吻合" if (lo, hi) == (25, 29) else ""
        print(f"    {lo}-{hi} 歲　{v:>7,.0f} {d['unit']}{mark}")
    print("  按教育程度")
    for k, v in d["by_education"].items():
        print(f"    {k}　{v:>7,.0f} {d['unit']}")
