"""各行政區住宅租金。—— 內政部地政司 不動產成交案件實際資訊（實價登錄）租賃案件，每季

實價登錄的租賃檔是官方、免金鑰、固定網址、每季一個 zip：

    https://plvr.land.moi.gov.tw/DownloadSeason?season={民國年}S{季}&type=zip&fileName=lvr_landcsv.zip

zip 裡每個縣市一個 CSV，檔名代碼跟財政部不一樣（實測開檔確認）：
a 臺北、b 臺中、d 臺南、e 高雄、f 新北、h 桃園；`_c.csv` 是租賃。

指標：**住宅每坪月租中位數**（元／坪／月）＝「單價元平方公尺」× 3.30579 的中位數。
用中位數不用平均，因為租賃檔的離群值多（整棟出租、含車位、登錄錯位數）。

篩選（每一條都要能講出理由）：
  - 交易標的 以「租賃房屋」開頭（排除純車位、土地）
  - 建物型態 是住宅：住宅大樓／公寓／華廈／透天厝／套房（排除店面、辦公、廠辦、工廠）
  - 主要用途 住家用／集合住宅／住宅／住商用（空白也留，早期檔常空）
  - 單價 > 0
  - **排除「社會住宅包租轉租」**：包租業者轉租給弱勢的價格是市價八折以下，不是市場租金。
    「社會住宅代管」是房東與房客直接議定的租金，保留。

⚠️ 口徑要講清楚（卡片上要寫）：
  1. 租賃登錄從 110 年 7 月起只對**租賃住宅服務業與社會住宅**強制申報，一般房東自行成交的不用登錄；
     所以樣本偏向代管／包租物件，不是全市場。這是官方唯一有行政區細分的租金資料。
  2. 這是全體租賃案件，不是青年；年齡層寫「全體」。
  3. 每區每年樣本數差很多（三重 1,500 筆、烏來 0 筆）。n < 30 的區標 low，不進地圖顏色。

檢查：每個 CSV 的鄉鎮市區必須全部是該市的行政區（欄位錯位或拿錯檔會在這裡炸）；
每坪月租中位數在 200–5,000 元之間。
"""

from __future__ import annotations

import csv
import io
import statistics
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.sources import CACHE_DIR, TIMEOUT, USER_AGENT  # noqa: E402

URL = "https://plvr.land.moi.gov.tw/DownloadSeason?season={season}&type=zip&fileName=lvr_landcsv.zip"
CITY_CODE = {"臺北市": "a", "臺中市": "b", "臺南市": "d", "高雄市": "e", "新北市": "f", "桃園市": "h"}
DATASET = "內政部地政司 不動產成交案件實際資訊（實價登錄）租賃案件"
LANDING = "https://plvr.land.moi.gov.tw/DownloadOpenData"
FIRST_SEASON = "108S1"          # 2019。跟財政部所得同一個起點，模型好對齊
PING_PER_SQM = 3.30579
MIN_N = 30                       # 樣本低於這個數的區只給 low
RESIDENTIAL_TYPES = ("住宅大樓", "公寓", "華廈", "透天厝", "套房")
RESIDENTIAL_USES = ("住家用", "集合住宅", "住宅", "住商用", "")
EXCLUDE_SERVICE = ("社會住宅包租轉租",)


def _seasons(first: str, last: str) -> list[str]:
    y0, q0 = int(first[:3]), int(first[4])
    y1, q1 = int(last[:3]), int(last[4])
    out = []
    y, q = y0, q0
    while (y, q) <= (y1, q1):
        out.append(f"{y:03d}S{q}")
        q += 1
        if q == 5:
            y, q = y + 1, 1
    return out


def _download_season(season: str, *, refresh: bool = False) -> dict[str, str]:
    """回傳 {城市代碼: CSV 文字}。zip 15MB 只留六都的租賃 CSV 進快取。"""
    CACHE_DIR.mkdir(exist_ok=True)
    cached = {c: CACHE_DIR / f"lvr_{season}_{c}_c.csv" for c in CITY_CODE.values()}
    if all(p.exists() for p in cached.values()) and not refresh:
        return {c: p.read_text(encoding="utf-8") for c, p in cached.items()}
    req = urllib.request.Request(URL.format(season=season), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT * 3) as resp:
        body = resp.read()
    if body[:2] != b"PK":
        raise RuntimeError(f"{season}：不是 zip（{len(body)} bytes）—— 這一季可能還沒發布")
    z = zipfile.ZipFile(io.BytesIO(body))
    out = {}
    for c, p in cached.items():
        name = f"{c}_lvr_land_c.csv"
        text = z.read(name).decode("utf-8-sig") if name in z.namelist() else ""
        p.write_text(text, encoding="utf-8")
        out[c] = text
    return out


def latest_season(*, refresh: bool = False) -> str:
    """從快取裡最新的一季往後探，探到沒有為止。"""
    have = sorted(p.name.split("_")[1] for p in CACHE_DIR.glob("lvr_*_f_c.csv")) if CACHE_DIR.exists() else []
    cur = have[-1] if have else FIRST_SEASON
    while True:
        y, q = int(cur[:3]), int(cur[4])
        nxt = f"{y + (q == 4):03d}S{(q % 4) + 1}"
        try:
            _download_season(nxt, refresh=refresh)
        except Exception:  # noqa: BLE001 — 404、非 zip、逾時都代表這季還沒有
            return cur
        cur = nxt


def _rows(text: str) -> list[dict]:
    rows = list(csv.DictReader(io.StringIO(text)))
    return rows[1:] if rows and rows[0].get("鄉鎮市區", "").startswith("The") else rows


def _keep(r: dict) -> bool:
    if not r.get("交易標的", "").startswith("租賃房屋"):
        return False
    if not any(r.get("建物型態", "").startswith(t) for t in RESIDENTIAL_TYPES):
        return False
    if r.get("主要用途", "").strip() not in RESIDENTIAL_USES:
        return False
    if r.get("租賃住宅服務", "").strip() in EXCLUDE_SERVICE:
        return False
    try:
        return float(r.get("單價元平方公尺") or 0) > 0
    except ValueError:
        return False


def fetch_rent(region: str = "新北市", *, refresh: bool = False, districts: list[str] | None = None) -> dict:
    """回傳 {"years": [...], "latest": {"window": "114S3–115S2", 區: {"median","n","rent_median"}},
    "yearly": {區: {year: {"median","n"}}}, ...}。median 單位：元／坪／月。"""
    code = CITY_CODE[region]
    last = latest_season(refresh=refresh)
    seasons = _seasons(FIRST_SEASON, last)
    by_season: dict[str, list[dict]] = {}
    for s in seasons:
        rows = [r for r in _rows(_download_season(s, refresh=refresh)[code]) if _keep(r)]
        by_season[s] = rows
        if districts:
            bad = {r["鄉鎮市區"] for r in rows} - {d.replace(region, "") for d in districts}
            if bad:
                raise RuntimeError(f"{region} {s}：租賃檔出現不是本市的行政區 {sorted(bad)[:5]} —— 拿錯檔或欄位錯位")

    def summarize(rows):
        per = {}
        for r in rows:
            per.setdefault(region + r["鄉鎮市區"], []).append(r)
        out = {}
        for d, rs in per.items():
            ppp = sorted(float(r["單價元平方公尺"]) * PING_PER_SQM for r in rs)
            rent = sorted(float(r["總額元"]) for r in rs if r.get("總額元"))
            out[d] = {"median": round(statistics.median(ppp)), "n": len(rs),
                      "rent_median": round(statistics.median(rent)) if rent else None}
        return out

    # 最新四季：一年的樣本，季節與小樣本的雜訊比較小
    window = seasons[-4:]
    latest = summarize([r for s in window for r in by_season[s]])
    city_all = [r for s in window for r in by_season[s]]
    latest[region] = summarize(city_all).get(region) or {}
    ppp = sorted(float(r["單價元平方公尺"]) * PING_PER_SQM for r in city_all)
    rent = sorted(float(r["總額元"]) for r in city_all if r.get("總額元"))
    latest[region] = {"median": round(statistics.median(ppp)), "n": len(ppp),
                      "rent_median": round(statistics.median(rent))}

    # 年序列（以季歸年；最後一年可能不足四季）
    yearly: dict[str, dict[int, dict]] = {}
    years = sorted({int(s[:3]) + 1911 for s in seasons})
    for y in years:
        rows = [r for s in seasons if int(s[:3]) + 1911 == y for r in by_season[s]]
        for d, v in summarize(rows).items():
            yearly.setdefault(d, {})[y] = v
        if rows:
            p = sorted(float(r["單價元平方公尺"]) * PING_PER_SQM for r in rows)
            yearly.setdefault(region, {})[y] = {"median": round(statistics.median(p)), "n": len(p)}

    for d, v in latest.items():
        if v.get("n", 0) >= MIN_N and not 200 <= v["median"] <= 5000:
            raise RuntimeError(f"{d} 每坪月租中位數 {v['median']} 元不在 200–5,000 —— 單位錯了")

    return {"source": DATASET, "url": LANDING, "region": region, "seasons": seasons,
            "window": f"{window[0]}–{window[-1]}", "min_n": MIN_N, "years": years,
            "latest": latest, "yearly": yearly,
            "filters": "租賃房屋；住宅大樓／公寓／華廈／透天厝／套房；住家用途；排除社會住宅包租轉租；單價 > 0",
            "note": ("實價登錄租賃案件，全體案件非青年。110 年 7 月起只強制租賃住宅服務業與社宅申報，"
                     "一般房東自行成交不用登錄，樣本偏向代管／包租物件。每坪月租 = 單價元／平方公尺 × 3.30579，取中位數。")}


if __name__ == "__main__":
    region = sys.argv[1] if len(sys.argv) > 1 else "新北市"
    d = fetch_rent(region)
    print(f"{region} 住宅每坪月租中位數　{d['window']}（n ≥ {d['min_n']} 才可靠）\n")
    for name, v in sorted(d["latest"].items(), key=lambda kv: -kv[1].get("median", 0)):
        flag = "" if v.get("n", 0) >= d["min_n"] else "  (n 不足)"
        print(f"{name.replace(region, '') or region:<8}{v.get('median', 0):>7,} 元/坪　n={v.get('n', 0):>5}　月租中位數 {v.get('rent_median') or 0:>7,}{flag}")
