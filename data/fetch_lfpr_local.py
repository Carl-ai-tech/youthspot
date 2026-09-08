"""抓縣市別的分齡勞動力參與率。—— 主計總處人力資源調查 表29

意義：在這之前，我們的 r(a) 是**全國**曲線套到新北市，
所有衍生數字都得標 medium 並註明「借用全國形狀」。
接上這張表之後，新北市有自己的 25-29、30-34、35-39 勞參率。

表29 是多層表頭、左右兩個區塊拼接，欄位極容易對錯。
所以欄位對照是**用臺灣地區那一列跟 mp04020 的全國值逐一驗證**出來的，
而且 `_verify()` 每次執行都會重驗一次。改年度時這道檢查會自動抓到位移。

單獨執行：python data/fetch_lfpr_local.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.odsreader import clean, download_ods, to_float  # noqa: E402

TABLE29 = "https://ws.dgbas.gov.tw/001/Upload/463/relfile/11516/236078/table29.ods"
DATASET = "行政院主計總處 人力資源調查－表29 年齡組別勞動力參與率"
YEAR = 2025

# 欄位索引 → 年齡組（小計欄）。以臺灣地區列對照 mp04020 全國值驗證而得。
BAND_COLUMNS = {
    (15, 24): 5, (25, 29): 12, (30, 34): 15, (35, 39): 21,
    (40, 44): 24, (45, 49): 30, (50, 54): 34, (55, 59): 37, (60, 64): 40,
}
COL_TOTAL = 2

# 驗證用：臺灣地區在這幾組的已知值（來自 mp04020，2025 年平均）
NATIONAL_CHECK = {(25, 29): 92.71, (30, 34): 92.23, (35, 39): 92.69, (40, 44): 88.80}


def _row_for(rows: list[list[str]], region: str) -> list[str]:
    for row in rows:
        if len(row) > max(BAND_COLUMNS.values()) and clean(row[1] if len(row) > 1 else "").startswith(region):
            return row
    raise RuntimeError(f"表29 裡找不到 {region}")


def _verify(rows: list[list[str]]) -> None:
    """拿臺灣地區那一列對答案，確認欄位沒有位移。"""
    tw = _row_for(rows, "臺灣地區")
    for band, expected in NATIONAL_CHECK.items():
        got = to_float(tw[BAND_COLUMNS[band]])
        if got is None or abs(got - expected) > 0.05:
            raise ValueError(
                f"表29 欄位驗證失敗：臺灣地區 {band[0]}-{band[1]} 讀到 {got}，"
                f"應為 {expected}（來自 mp04020）。欄位可能位移，請重新對照。"
            )


def fetch_local_lfpr(region: str = "新北市", *, refresh: bool = False) -> dict:
    rows = download_ods(TABLE29, cache_name="dgbas_table29_lfpr.ods", refresh=refresh)
    _verify(rows)
    row = _row_for(rows, region)
    return {
        "region": region,
        "year": YEAR,
        "unit": "%",
        "dataset": DATASET,
        "total": (to_float(row[COL_TOTAL]) or 0) / 100,
        "by_band": {b: (to_float(row[i]) or 0) / 100 for b, i in BAND_COLUMNS.items()},
    }


if __name__ == "__main__":
    local = fetch_local_lfpr()
    rows = download_ods(TABLE29, cache_name="dgbas_table29_lfpr.ods")
    tw = fetch_local_lfpr("臺灣地區")
    print(f"{local['region']} {local['year']} 年分齡勞動力參與率（欄位已對照全國值驗證）")
    print(f"  總計 {local['total']:.1%}\n")
    print(f"  {'年齡組':<10}{'新北市':>9}{'全國':>9}{'差距':>9}")
    for band, v in local["by_band"].items():
        n = tw["by_band"][band]
        print(f"  {f'{band[0]}-{band[1]} 歲':<10}{v:>9.1%}{n:>9.1%}{v - n:>+9.1%}")
