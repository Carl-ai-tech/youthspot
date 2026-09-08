"""六都比較。

「我們在六都排第幾」是首長最常問的問題，而資料早就在手上 ——
表6、表29、表37 每一張都有全部 22 個縣市，先前是我們只篩新北市，
把其他縣市丟掉了。

三個指標都取 25-29 歲：那是《青年基本法》範圍內、
官方分組剛好對得上、不需要任何插補的一段（信心度 high）。
拿沒插補過的數字做跨縣市比較，才不會把插補誤差當成城市差異。

單獨執行：python data/fetch_benchmark.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.fetch_lfpr_local import BAND_COLUMNS, TABLE29  # noqa: E402
from data.fetch_salary import BANDS as SALARY_BANDS  # noqa: E402
from data.fetch_salary import COL_MEAN_TOTAL, MEDIAN_OFFSET, TABLE6  # noqa: E402
from data.fetch_unemployment_local import TABLE37  # noqa: E402
from data.odsreader import clean, download_ods, to_float  # noqa: E402

SIX_CITIES = ["臺北市", "新北市", "桃園市", "臺中市", "臺南市", "高雄市"]
BAND = (25, 29)          # 官方分組剛好對得上，不需插補


def _six_from(url: str, cache: str, col: int, *, scale: float = 1.0,
              refresh: bool = False) -> dict[str, float]:
    """從一張表裡把六都的某一欄挑出來。每個縣市只取第一次出現（最新年度）。"""
    rows = download_ods(url, cache_name=cache, refresh=refresh)
    out: dict[str, float] = {}
    for row in rows:
        if not row or len(row) <= col:
            continue
        # 表6 的縣市在第 0 欄，表29／表37 在第 1 欄
        name = clean(row[0]) or (clean(row[1]) if len(row) > 1 else "")
        for city in SIX_CITIES:
            if name.startswith(city) and city not in out:
                v = to_float(row[col])
                if v is not None:
                    out[city] = v * scale
    return out


def fetch_benchmark(*, refresh: bool = False) -> dict:
    salary_mean = _six_from(TABLE6, "dgbas_table6_salary.ods",
                            SALARY_BANDS[BAND], refresh=refresh)
    salary_median = _six_from(TABLE6, "dgbas_table6_salary.ods",
                              SALARY_BANDS[BAND] + MEDIAN_OFFSET, refresh=refresh)
    salary_total = _six_from(TABLE6, "dgbas_table6_salary.ods",
                             COL_MEAN_TOTAL, refresh=refresh)
    lfpr = _six_from(TABLE29, "dgbas_table29_lfpr.ods",
                     BAND_COLUMNS[BAND], scale=0.01, refresh=refresh)
    unemp = _six_from(TABLE37, "dgbas_table37_unemp.ods",
                      BAND_COLUMNS[BAND], scale=0.01, refresh=refresh)

    def ranked(d: dict[str, float], *, higher_is_better: bool) -> dict:
        order = sorted(d, key=lambda c: d[c], reverse=higher_is_better)
        return {
            "values": {c: d[c] for c in SIX_CITIES if c in d},
            "rank": {c: i + 1 for i, c in enumerate(order)},
            "higher_is_better": higher_is_better,
        }

    return {
        "band": f"{BAND[0]}-{BAND[1]}",
        "cities": [c for c in SIX_CITIES if c in salary_mean],
        "note": "取 25-29 歲：官方分組剛好對得上，不需插補，"
                "所以城市之間的差異是真的差異，不是插補誤差",
        "metrics": {
            "平均年薪": {**ranked(salary_mean, higher_is_better=True), "unit": "萬元/年"},
            "中位數年薪": {**ranked(salary_median, higher_is_better=True), "unit": "萬元/年"},
            "全年齡平均年薪": {**ranked(salary_total, higher_is_better=True), "unit": "萬元/年"},
            "勞動力參與率": {**ranked(lfpr, higher_is_better=True), "unit": "%"},
            "失業率": {**ranked(unemp, higher_is_better=False), "unit": "%"},
        },
    }


if __name__ == "__main__":
    b = fetch_benchmark()
    print(f"六都比較（{b['band']} 歲）\n")
    for metric, block in b["metrics"].items():
        arrow = "高者佳" if block["higher_is_better"] else "低者佳"
        print(f"  {metric}（{arrow}）")
        pairs = sorted(block["values"].items(), key=lambda kv: block["rank"][kv[0]])
        for city, v in pairs:
            txt = f"{v:.1%}" if block["unit"] == "%" else f"{v:.1f} {block['unit']}"
            mark = "　←" if city == "新北市" else ""
            print(f"    {block['rank'][city]}. {city:<6}{txt:>12}{mark}")
        print()
