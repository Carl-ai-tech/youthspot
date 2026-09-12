"""各行政區出生數按生母單一年齡。—— 內政部戶政司 ODRP056 嬰兒出生數按性別及生母單一年齡分（按發生），每年

局長被問「青年局應該負責讓年輕人結婚生小孩」時，系統原本只能說「沒資料」。這份接進來就能算
**各區 18–35 歲女性的一般生育率**（該年出生數（生母 18–35 歲）÷ 該區 18–35 歲女性人口 × 1000‰），
讓答辯從「沒資料」變成「有數字、但不是青年局主責」。

API：https://www.ris.gov.tw/rs-opendata/api/v1/datastore/ODRP056/{yyy}?PAGE=n（全國一次，14 頁；COUNTY 參數無效）
欄位（實測 112 年）：statistic_yyy、according（按發生日期分）、site_id（新北市板橋區）、
mother_age（未滿15歲、15歲…49歲、50歲以上）、birth_sex、birth_count。
按「發生」不按「登記」，年份是嬰兒實際出生年。

分母用同一期別的戶政單一年齡女性人口（ODRP014 的 people_age_XXX_f）—— 分子分母同一個機關、同一套區界。
限制：這是「生母戶籍所在區」的出生數，跟淨遷入一樣量的是設籍者。
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.sources import CACHE_DIR, TIMEOUT, USER_AGENT  # noqa: E402

API = "https://www.ris.gov.tw/rs-opendata/api/v1/datastore/ODRP056/{yyy}?PAGE={page}"
DATASET = "內政部戶政司 ODRP056 嬰兒出生數按性別及生母單一年齡分（按發生）"
LANDING = "https://www.ris.gov.tw/rs-opendata/api/Main/docs/v1"
AGE_LO, AGE_HI = 18, 35


def _year_rows(yyy: str, *, refresh: bool = False) -> list[dict]:
    CACHE_DIR.mkdir(exist_ok=True)
    cached = CACHE_DIR / f"odrp056_{yyy}.json"
    if cached.exists() and not refresh:
        return json.loads(cached.read_text(encoding="utf-8"))
    rows, page = [], 1
    while True:
        req = urllib.request.Request(API.format(yyy=yyy, page=page), headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=TIMEOUT * 2) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        rows += d.get("responseData") or []
        if page >= int(d.get("totalPage") or 1):
            break
        page += 1
    if not rows:
        raise RuntimeError(f"ODRP056 {yyy}：查無資料")
    cached.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return rows


def _mother_age(label: str) -> int | None:
    """「25歲」→ 25；「未滿15歲」→ 14；「50歲以上」→ 50；其他（不詳）→ None。"""
    if label.startswith("未滿"):
        return 14
    m = re.match(r"(\d+)歲", label)
    return int(m.group(1)) if m else None


def latest_year(*, refresh: bool = False) -> str:
    """從 114 往回探，第一個有資料的年份。"""
    for yyy in ("114", "113", "112", "111"):
        try:
            _year_rows(yyy, refresh=refresh)
            return yyy
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError("ODRP056 找不到任何年份的資料")


def fetch_births(region: str = "新北市", *, refresh: bool = False, year: str | None = None) -> dict:
    """回傳 {"year": 西元, "areas": {區: {"youth_mothers": 生母 18–35 的出生數, "total": 全部出生數}}}，含全市合計。"""
    yyy = year or latest_year(refresh=refresh)
    areas: dict[str, dict[str, int]] = {}
    for r in _year_rows(yyy, refresh=refresh):
        site = r.get("site_id", "")
        if not site.startswith(region):
            continue
        n = int(r.get("birth_count") or 0)
        a = areas.setdefault(site, {"youth_mothers": 0, "total": 0})
        a["total"] += n
        age = _mother_age(r.get("mother_age", ""))
        if age is not None and AGE_LO <= age <= AGE_HI:
            a["youth_mothers"] += n
    if not areas:
        raise RuntimeError(f"ODRP056 {yyy}：{region} 沒有任何行政區")
    city = {k: sum(a[k] for a in areas.values()) for k in ("youth_mothers", "total")}
    areas[region] = city
    return {"source": DATASET, "url": LANDING, "region": region, "roc_year": yyy, "year": int(yyy) + 1911,
            "areas": areas,
            "note": "按發生日期、生母戶籍所在區。18–35 歲女性一般生育率 = 生母 18–35 歲的出生數 ÷ 同期 18–35 歲女性人口 × 1000‰。"}


if __name__ == "__main__":
    region = sys.argv[1] if len(sys.argv) > 1 else "新北市"
    d = fetch_births(region)
    print(f"{region} {d['year']} 年出生數（生母 18–35 歲／全部）")
    for k, a in sorted(d["areas"].items(), key=lambda kv: -kv[1]["youth_mothers"]):
        print(f"{k.replace(region, '') or region:<8} {a['youth_mothers']:>6,} / {a['total']:>6,}")
