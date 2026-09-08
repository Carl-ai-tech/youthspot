"""抓縣市別的分齡失業率。—— 主計總處人力資源調查 表37

表37 與表29（勞參率）的版面完全一樣，所以共用同一組欄位對照，
並且同樣拿臺灣地區那一列跟 mp04031 的全國值逐一驗證。

⚠️ 只有縣市層級。官方沒有行政區別的失業率，
   所以「汐止區的失業率」這種問題答不出來 —— 不是我們沒做，是資料不存在。

單獨執行：python data/fetch_unemployment_local.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.fetch_lfpr_local import BAND_COLUMNS, COL_TOTAL, _row_for  # noqa: E402
from data.odsreader import download_ods, to_float  # noqa: E402

TABLE37 = "https://ws.dgbas.gov.tw/001/Upload/463/relfile/11516/236078/table37.ods"
DATASET = "行政院主計總處 人力資源調查－表37 年齡組別失業率"
YEAR = 2025

# 驗證用：臺灣地區的已知值（mp04031，2025 年平均）
NATIONAL_CHECK = {(25, 29): 5.77, (30, 34): 3.37, (35, 39): 2.53, (40, 44): 2.33}


def _verify(rows: list[list[str]]) -> None:
    tw = _row_for(rows, "臺灣地區")
    for band, expected in NATIONAL_CHECK.items():
        got = to_float(tw[BAND_COLUMNS[band]])
        if got is None or abs(got - expected) > 0.05:
            raise ValueError(
                f"表37 欄位驗證失敗：臺灣地區 {band[0]}-{band[1]} 讀到 {got}，"
                f"應為 {expected}（來自 mp04031）。欄位可能位移。"
            )


def fetch_local_unemployment(region: str = "新北市", *, refresh: bool = False) -> dict:
    rows = download_ods(TABLE37, cache_name="dgbas_table37_unemp.ods", refresh=refresh)
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
    local = fetch_local_unemployment()
    tw = fetch_local_unemployment("臺灣地區")
    print(f"{local['region']} {local['year']} 年分齡失業率（欄位已對照全國值驗證）")
    print(f"  總計 {local['total']:.1%}\n")
    print(f"  {'年齡組':<10}{'新北市':>9}{'全國':>9}")
    for band, v in local["by_band"].items():
        print(f"  {f'{band[0]}-{band[1]} 歲':<10}{v:>9.1%}{tw['by_band'][band]:>9.1%}")
