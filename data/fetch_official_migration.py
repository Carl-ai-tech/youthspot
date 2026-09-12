"""官方遷入遷出人數（全年齡）。—— 內政部戶政司 ODRP011 遷入遷出統計表（新增區域代碼），每月、村里

這份**不分齡**，所以不能直接拿來做青年指標；它的用途是**交叉驗證**我們的世代淨遷入：
同一年、同一個區，我們用單一年齡人口兩期相減算出來的 18–35 歲淨遷入，
跟官方登記的全年齡遷入 − 遷出，方向與相對大小應該一致。相關高，方法就站得住；
相關低，就要回頭查是不是哪裡算錯。

欄位（實測 11507 開檔）：in_total_m/f 遷入（含國外、他縣市、他鄉鎮）、out_total_m/f 遷出、
first_reg 初設戶籍、deleted_reg 除籍（出境滿兩年）、move_in_other／move_out_other 其他、
in_migrants／out_migrants 同區里間遷徙（區的層級要排除）。

我們的「淨遷入」口徑跟官方對應：
    官方淨遷入(區) = Σ里 (in_total − out_total) + (first_reg − deleted_reg) + (move_in_other − move_out_other)
除籍與恢復戶籍要算進去，因為世代追蹤的兩期相減也含這些（2022／2023 的戶籍事件就是這樣來的）。

一個月一次呼叫、一個城市一頁（新北 1,032 個里剛好一頁）。快取進 _cache/odrp011_{city}_{yyymm}.json。
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.sources import CACHE_DIR, TIMEOUT, USER_AGENT  # noqa: E402

API = "https://www.ris.gov.tw/rs-opendata/api/v1/datastore/ODRP011/{yyymm}?PAGE={page}&COUNTY={county}"
DATASET = "內政部戶政司 ODRP011 遷入遷出統計表（新增區域代碼）"
LANDING = "https://www.ris.gov.tw/rs-opendata/api/Main/docs/v1"   # 戶政司開放資料 API 文件（ODRP011 在列表裡）；data.gov.tw 的 77141 是別的資料集


def _month_rows(region: str, yyymm: str, *, refresh: bool = False) -> list[dict]:
    CACHE_DIR.mkdir(exist_ok=True)
    cached = CACHE_DIR / f"odrp011_{region}_{yyymm}.json"
    if cached.exists() and not refresh:
        return json.loads(cached.read_text(encoding="utf-8"))
    rows, page = [], 1
    while True:
        url = API.format(yyymm=yyymm, page=page, county=urllib.parse.quote(region))
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=TIMEOUT * 2) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        rows += d.get("responseData") or []
        if page >= int(d.get("totalPage") or 1):
            break
        page += 1
    if not rows:
        raise RuntimeError(f"{region} {yyymm}：ODRP011 查無資料")
    cached.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return rows


def _months(end_yyymm: str, n: int = 12) -> list[str]:
    """往前推 n 個月（含 end 當月的前一個月起算：8 月到隔年 7 月，跟人口期別 7 月對齊）。"""
    y, m = int(end_yyymm[:3]), int(end_yyymm[3:])
    out = []
    for _ in range(n):
        out.append(f"{y:03d}{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def fetch_official_migration(region: str = "新北市", *, end_period: str = "11507", months: int = 12,
                             refresh: bool = False) -> dict:
    """回傳 {"months": [...], "areas": {區: {"in", "out", "first", "deleted", "other_in", "other_out", "net"}}}。
    區的 net 已含初設／除籍／其他，跟世代追蹤的口徑一致；里間遷徙不算。"""
    ms = _months(end_period, months)
    areas: dict[str, dict[str, int]] = {}
    for mm in ms:
        for r in _month_rows(region, mm, refresh=refresh):
            a = areas.setdefault(r["site_id"], {"in": 0, "out": 0, "first": 0, "deleted": 0, "other_in": 0, "other_out": 0})
            g = lambda k: int(r.get(k + "_m") or 0) + int(r.get(k + "_f") or 0)  # noqa: E731
            a["in"] += g("in_total"); a["out"] += g("out_total")
            a["first"] += g("first_reg"); a["deleted"] += g("deleted_reg")
            a["other_in"] += g("move_in_other"); a["other_out"] += g("move_out_other")
    for a in areas.values():
        a["net"] = a["in"] - a["out"] + a["first"] - a["deleted"] + a["other_in"] - a["other_out"]
        a["net_moves_only"] = a["in"] - a["out"]
    city = {k: sum(a[k] for a in areas.values()) for k in next(iter(areas.values()))}
    areas[region] = city
    return {"source": DATASET, "url": LANDING, "region": region, "months": ms, "areas": areas,
            "note": "全年齡官方登記遷徙；net 含初設／除籍／其他（與世代追蹤口徑一致），不含同區里間遷徙。"}


if __name__ == "__main__":
    region = sys.argv[1] if len(sys.argv) > 1 else "新北市"
    d = fetch_official_migration(region)
    print(f"{region} 官方遷入遷出（全年齡）{d['months'][0]}–{d['months'][-1]}")
    for k, a in sorted(d["areas"].items(), key=lambda kv: -kv[1]["net"]):
        print(f"{k.replace(region, '') or region:<8} 遷入 {a['in']:>7,} 遷出 {a['out']:>7,} 除籍 {a['deleted']:>5,} 淨 {a['net']:>+7,}")
