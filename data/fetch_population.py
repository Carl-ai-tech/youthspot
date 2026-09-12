"""抓單一年齡人口 P(a)。—— Spec §6.3 死穴 1

來源：內政部戶政司 ODRP014「村里戶數、單一年齡人口（新增區域代碼）」
免金鑰、免註冊，一次回全台約 7,800 個村里，四頁分頁。

我們抓全台再自己過濾 site_id（"新北市板橋區" 這種），
不是因為懶，是因為這支 API 沒有縣市參數 —— 只能整包拉回來篩。
好處是換縣市只要改一個字串，題目要擴到全國也不用改程式。

單獨執行：python data/fetch_population.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.sources import POPULATION_API, fetch_json  # noqa: E402

MAX_AGE_FIELD = 100          # 資料到 people_age_100up_*，我們只取到 64


def roc_period(d: date) -> str:
    return f"{d.year - 1911}{d.month:02d}"


def _previous(period: str) -> str:
    y, m = int(period[:3]), int(period[3:])
    return f"{y - 1}12" if m == 1 else f"{y}{m - 1:02d}"


def latest_period(*, back: int = 18, refresh: bool = False) -> str:
    """從這個月往回找，第一個抓得到資料的月份就是最新一期。

    戶政司大約落後兩個月，而且更新時間不固定，寫死月份遲早會爆。
    """
    period = roc_period(date.today())
    for _ in range(back):
        payload = fetch_json(
            POPULATION_API.format(period=period),
            f"odrp014_{period}_p1.json",
            refresh=refresh,
        )
        if payload.get("responseCode") == "OD-0101-S":
            return period
        period = _previous(period)
    raise RuntimeError(f"往回找了 {back} 個月都沒有資料，API 可能改了")


def fetch_population(
    region: str = "新北市",
    *,
    period: str | None = None,
    age_range: tuple[int, int] = (15, 64),
    refresh: bool = False,
) -> tuple[dict[int, int], dict]:
    """回傳 ({年齡: 人數}, 中繼資料)。"""
    period = period or latest_period(refresh=refresh)
    lo, hi = age_range
    counts = {a: 0 for a in range(lo, hi + 1)}
    villages = 0

    page, total_pages = 1, 1
    while page <= total_pages:
        payload = fetch_json(
            f"{POPULATION_API.format(period=period)}?page={page}",
            f"odrp014_{period}_p{page}.json",
            refresh=refresh,
        )
        if payload.get("responseCode") != "OD-0101-S":
            raise RuntimeError(f"{period} 第 {page} 頁抓失敗：{payload.get('responseMessage')}")
        total_pages = int(payload["totalPage"])
        for row in payload["responseData"]:
            if not row["site_id"].startswith(region):
                continue
            villages += 1
            for a in range(lo, hi + 1):
                counts[a] += int(row[f"people_age_{a:03d}_m"]) + int(row[f"people_age_{a:03d}_f"])
        page += 1

    if villages == 0:
        raise RuntimeError(f"{period} 找不到任何 site_id 開頭是 {region!r} 的村里")

    meta = {
        "region": region,
        "period": period,
        "roc_period_label": f"民國 {period[:3]} 年 {int(period[3:])} 月",
        "villages": villages,
        "unit": "人",
    }
    return counts, meta


def fetch_population_by_sex(
    region: str = "新北市",
    *,
    period: str | None = None,
    age_range: tuple[int, int] = (15, 64),
    refresh: bool = False,
) -> dict[str, dict[str, dict[int, int]]]:
    """{行政區: {"m": {年齡: 人數}, "f": {...}}}。

    戶政司的欄位本來就是 people_age_XXX_m / _f —— 性別維度一直都在，
    只是先前兩欄相加後就丟掉了。這支把它留下來。
    """
    period = period or latest_period(refresh=refresh)
    lo, hi = age_range
    out: dict[str, dict[str, dict[int, int]]] = {}
    page, total_pages = 1, 1
    while page <= total_pages:
        payload = fetch_json(
            f"{POPULATION_API.format(period=period)}?page={page}",
            f"odrp014_{period}_p{page}.json",
            refresh=refresh,
        )
        total_pages = int(payload["totalPage"])
        for row in payload["responseData"]:
            site = row["site_id"]
            if not site.startswith(region):
                continue
            d = out.setdefault(site, {"m": {a: 0 for a in range(lo, hi + 1)},
                                      "f": {a: 0 for a in range(lo, hi + 1)}})
            for a in range(lo, hi + 1):
                d["m"][a] += int(row[f"people_age_{a:03d}_m"])
                d["f"][a] += int(row[f"people_age_{a:03d}_f"])
        page += 1
    return out


def fetch_population_by_district(
    region: str = "新北市",
    *,
    period: str | None = None,
    age_range: tuple[int, int] = (15, 64),
    refresh: bool = False,
) -> tuple[dict[str, dict[int, int]], dict]:
    """同上，但按行政區分開回傳 —— 儀表板的「地區篩選」要用（P0-4）。"""
    period = period or latest_period(refresh=refresh)
    lo, hi = age_range
    by_district: dict[str, dict[int, int]] = {}
    villages = 0

    page, total_pages = 1, 1
    while page <= total_pages:
        payload = fetch_json(
            f"{POPULATION_API.format(period=period)}?page={page}",
            f"odrp014_{period}_p{page}.json",
            refresh=refresh,
        )
        total_pages = int(payload["totalPage"])
        for row in payload["responseData"]:
            site = row["site_id"]
            if not site.startswith(region):
                continue
            villages += 1
            counts = by_district.setdefault(site, {a: 0 for a in range(lo, hi + 1)})
            for a in range(lo, hi + 1):
                counts[a] += int(row[f"people_age_{a:03d}_m"]) + int(row[f"people_age_{a:03d}_f"])
        page += 1

    meta = {
        "region": region,
        "period": period,
        "roc_period_label": f"民國 {period[:3]} 年 {int(period[3:])} 月",
        "villages": villages,
        "districts": len(by_district),
    }
    return by_district, meta


if __name__ == "__main__":
    pop, meta = fetch_population()
    print(f"{meta['region']}　{meta['roc_period_label']}　{meta['villages']:,} 個村里")
    total = sum(pop.values())
    youth = sum(pop[a] for a in range(18, 36))
    print(f"  15-64 歲　{total:>9,} 人")
    print(f"  18-35 歲　{youth:>9,} 人　← 《青年基本法》口徑")
    print()
    for a in (15, 17, 18, 24, 25, 29, 30, 35):
        print(f"    {a:>2} 歲　{pop[a]:>7,}")
