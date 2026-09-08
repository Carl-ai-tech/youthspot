"""抓教育程度別薪資。—— 主計總處 表1（全國）

命題點名的交叉：「教育程度與薪資水準」。
這張表只有全國、沒有縣市，但配上表32 的**新北市**分教育程度就業者人數，
就能做出命題要的那張交叉圖：新北市有多少人是這個學歷，這個學歷平均賺多少。

⚠️ 薪資是全國值，圖上必須標明。這是誠實標示，不是缺陷。

單獨執行：python data/fetch_salary_education.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.odsreader import clean, download_ods, to_float  # noqa: E402

TABLE1 = (
    "https://ws.dgbas.gov.tw/001/Upload/463/relfile/11753/232642/"
    "表1　各業受僱員工全年總薪資統計－按性別及教育程度分.ods"
)
DATASET = "行政院主計總處 工業及服務業全年總薪資統計－表1 按性別及教育程度分（全國）"
UNIT = "萬元/年"

# 欄位索引。表32 的教育程度只分三級，這裡把專科大學與研究所合併對應「大專及以上」。
EDU_COLUMNS = {"國中及以下": 4, "高級中等": 5, "專科及大學": 6, "研究所": 7}
MEDIAN_OFFSET = 7        # 平均數區塊佔 idx 1-7，中位數從 idx 8 開始
COL_ALL = 1


def fetch_salary_by_education(*, refresh: bool = False) -> dict:
    rows = download_ods(TABLE1, cache_name="dgbas_table1_salary_edu.ods", refresh=refresh)

    year = None
    for row in rows:
        for cell in row:
            m = re.fullmatch(r"(\d{3})年", clean(cell))
            if m and year is None:
                year = 1911 + int(m.group(1))
        if not row or clean(row[0]) != "全體" or len(row) < 15:
            continue
        mean = {k: to_float(row[i]) for k, i in EDU_COLUMNS.items()}
        median = {k: to_float(row[i + MEDIAN_OFFSET]) for k, i in EDU_COLUMNS.items()}
        if None in mean.values():
            raise ValueError("表1 教育程度欄位讀不到數字，欄位可能位移")
        # 學歷越高薪資越高，這個單調性是驗證欄位有沒有對的最好方法
        order = ["國中及以下", "高級中等", "專科及大學", "研究所"]
        vals = [mean[k] for k in order]
        if vals != sorted(vals):
            raise ValueError(f"表1 教育程度薪資不是遞增（{vals}），欄位可能位移")
        return {
            "year": year, "unit": UNIT, "dataset": DATASET, "scope": "全國",
            "all_mean": to_float(row[COL_ALL]),
            "mean": mean, "median": median,
        }
    raise RuntimeError("表1 裡找不到「全體」那一列")


def fetch_salary_by_industry(*, refresh: bool = False) -> dict[str, float]:
    """表1 的每一列是一個行業，回傳 {行業: 平均年薪（萬元）}。

    行業分類與職缺調查（mp05005）用的是同一套標準行業分類，可以直接對照 ——
    這讓「哪些領域缺工」能再加一句「而且薪水如何」。
    """
    rows = download_ods(TABLE1, cache_name="dgbas_table1_salary_edu.ods", refresh=refresh)
    out: dict[str, float] = {}
    for row in rows:
        if len(row) < 8 or not row[0]:
            continue
        name = clean(row[0])
        v = to_float(row[COL_ALL])
        if name and v and name not in ("全體",):
            out[name] = v
    return out


if __name__ == "__main__":
    d = fetch_salary_by_education()
    print(f"{d['scope']} {d['year']} 年受僱員工年薪　全體平均 {d['all_mean']} {d['unit']}")
    print(f"  {'教育程度':<12}{'平均數':>9}{'中位數':>9}")
    for k in d["mean"]:
        print(f"  {k:<12}{d['mean'][k]:>9.1f}{d['median'][k]:>9.1f}")
