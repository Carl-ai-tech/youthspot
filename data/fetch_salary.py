"""抓縣市別平均／中位數年薪。—— 主計總處 表6

年齡分組：未滿25 / 25-29 / 30-39 / 40-49 / 50-64 / 65+
其中 **25-29 與我們的目標分組完全吻合**，直接抄，不用插補。

⚠️ 薪資是**比率型**（平均值），不能乘權重。
   未滿25 → 18-24：兩者實質上是同一群人（未滿 18 幾乎沒有全時受僱），標 medium。
   30-39 → 30-35：需要分齡受僱人數當分母才切得動，我們沒有 → 標 low 並照抄。
   這不是偷懶，是誠實：沒有新資訊就不要假裝有。

檔案裡含 6 個年度的區塊，本程式取最新的那一個。

單獨執行：python data/fetch_salary.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.odsreader import clean, download_ods, to_float  # noqa: E402

TABLE6 = (
    "https://ws.dgbas.gov.tw/001/Upload/463/relfile/11753/232642/"
    "表6　工業及服務業全年總薪資統計－本國籍全時受僱員工按工作場所所在縣市別及年齡別分.ods"
)
DATASET = "行政院主計總處 工業及服務業全年總薪資統計－表6 按縣市別及年齡別分"
UNIT = "萬元/年"

# 欄位索引：平均數在左半，中位數在右半，順序相同
BANDS = {(0, 24): 3, (25, 29): 4, (30, 39): 5, (40, 49): 6, (50, 64): 7}
COL_MEAN_TOTAL, COL_MEAN_U30 = 1, 2
MEDIAN_OFFSET = 8


def _verify(row: list[str], region: str) -> None:
    g = lambda i: to_float(row[i])
    u25, b2529, u30 = g(BANDS[(0, 24)]), g(BANDS[(25, 29)]), g(COL_MEAN_U30)
    if None in (u25, b2529, u30):
        raise ValueError(f"{region}：關鍵欄位讀不到數字，欄位可能位移")
    # 「未滿30」是「未滿25」與「25-29」的混合，必須落在兩者之間
    if not (u25 <= u30 <= b2529):
        raise ValueError(
            f"{region}：未滿30（{u30}）沒有落在未滿25（{u25}）與25-29（{b2529}）之間，欄位可能位移"
        )
    # 薪資分布右偏，中位數必定低於平均數
    for band, col in BANDS.items():
        mean, med = g(col), g(col + MEDIAN_OFFSET)
        if mean is None or med is None or med > mean:
            raise ValueError(f"{region}：{band} 的中位數 {med} 不低於平均數 {mean}，欄位可能位移")


def fetch_salary(region: str = "新北市", *, refresh: bool = False) -> dict:
    rows = download_ods(TABLE6, cache_name="dgbas_table6_salary.ods", refresh=refresh)

    year = None
    for row in rows:
        for cell in row:
            m = re.fullmatch(r"(\d{3})年", clean(cell))
            if m and year is None:
                year = 1911 + int(m.group(1))
        if not row or clean(row[0]) != region:
            continue
        if len(row) < 17:
            continue
        _verify(row, region)
        return {
            "region": region,
            "year": year,
            "unit": UNIT,
            "dataset": DATASET,
            "total_mean": to_float(row[COL_MEAN_TOTAL]),
            "mean": {b: to_float(row[c]) for b, c in BANDS.items()},
            "median": {b: to_float(row[c + MEDIAN_OFFSET]) for b, c in BANDS.items()},
        }
    raise RuntimeError(f"表6 裡找不到 {region}")


def fetch_salary_series(region: str = "新北市", *, refresh: bool = False) -> list[dict]:
    """回傳該縣市**每一個年度**的分齡薪資，依年份排序。

    表6 的檔案裡放了六個年度，每一段各有一次全部縣市。
    先前只取最新一年，其他五年白白丟掉 —— 那正是「青年動態」需要的東西。
    """
    rows = download_ods(TABLE6, cache_name="dgbas_table6_salary.ods", refresh=refresh)
    out, year = [], None
    for row in rows:
        for cell in row:
            m = re.fullmatch(r"(\d{3})年", clean(cell))
            if m:
                year = 1911 + int(m.group(1))
        if not row or clean(row[0]) != region or len(row) < 17:
            continue
        _verify(row, region)
        out.append({
            "year": year,
            "total_mean": to_float(row[COL_MEAN_TOTAL]),
            "mean": {b: to_float(row[c]) for b, c in BANDS.items()},
            "median": {b: to_float(row[c + MEDIAN_OFFSET]) for b, c in BANDS.items()},
        })
    return sorted(out, key=lambda r: r["year"] or 0)


if __name__ == "__main__":
    d = fetch_salary()
    print(f"{d['region']}　{d['year']} 年全時受僱員工年薪　總計平均 {d['total_mean']} {d['unit']}")
    print(f"  {'年齡':<12}{'平均數':>9}{'中位數':>9}")
    for band in d["mean"]:
        lo, hi = band
        label = f"未滿 {hi + 1} 歲" if lo == 0 else f"{lo}-{hi} 歲"
        mark = "　← 與我們的 25-29 完全吻合" if band == (25, 29) else ""
        print(f"  {label:<12}{d['mean'][band]:>9.1f}{d['median'][band]:>9.1f}{mark}")
