"""資料來源清單與抓取工具。

三個來源都是免金鑰、免註冊的政府開放資料：

  1. 內政部戶政司 ODRP014「村里戶數、單一年齡人口（新增區域代碼）」
     → P(a)：單一年齡人口數。Spec §6.3 死穴 1 的解答。
     欄位長 people_age_018_m / people_age_018_f 這樣，一歲一欄到 100up。

  2. 主計總處 mp04020「歷年年齡組別勞動力參與率」
     → r(a) 的基底。只有全國、只到五歲組（15-19、20-24…）。

  3. 主計總處 mp04031「歷年年齡組別失業率」
     → 公式 D 借用的「形狀」曲線。同樣是全國、五歲組。

抓下來的原始檔一律進 data/_cache/。比賽現場網路不能賭，
demo 前先跑一次 build_reference.py，現場就算離線也叫得動。
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent
CACHE_DIR = DATA_DIR / "_cache"

# 六都。整條 pipeline 都以 region 參數化；新北市維持原本的檔名（reference_ntpc.json、
# unified.json、preview.html），其他縣市的檔名帶縣市名，舊的指令與測試才不會壞。
SIX_CITIES = ["新北市", "臺北市", "桃園市", "臺中市", "臺南市", "高雄市"]


def reference_path(region: str = "新北市"):
    return DATA_DIR / ("reference_ntpc.json" if region == "新北市" else f"reference_{region}.json")


def unified_path(region: str = "新北市"):
    return DATA_DIR / ("unified.json" if region == "新北市" else f"unified_{region}.json")


def preview_path(region: str = "新北市"):
    return DATA_DIR.parent / ("preview.html" if region == "新北市" else f"preview_{region}.html")


def region_arg(argv, default: str = "新北市") -> str:
    """--region 臺北市 這種參數；沒給就是新北市。"""
    for i, a in enumerate(argv):
        if a == "--region" and i + 1 < len(argv):
            return argv[i + 1].replace("台", "臺")
        if a.startswith("--region="):
            return a.split("=", 1)[1].replace("台", "臺")
    return default

USER_AGENT = "YouthLens/0.3 (hackathon prototype; stdlib urllib)"
TIMEOUT = 60

POPULATION_API = "https://www.ris.gov.tw/rs-opendata/api/v1/datastore/ODRP014/{period}"
# 107 年 7 月以前只有無區域代碼的 ODRP005（欄位相同：site_id、village、people_age_XXX_m/f）；實測 10607 有、10507 查無資料
POPULATION_API_OLD = "https://www.ris.gov.tw/rs-opendata/api/v1/datastore/ODRP005/{period}"
POPULATION_OLD_BEFORE = "10707"


def population_api(period: str) -> str:
    return (POPULATION_API_OLD if period < POPULATION_OLD_BEFORE else POPULATION_API).format(period=period)
POPULATION_DATASET = "內政部戶政司 ODRP014 村里戶數、單一年齡人口"
POPULATION_LANDING = "https://data.gov.tw/dataset/77132"   # 「新增區域代碼」版，跟程式打的 ODRP014 一致（32973 是無區域代碼的 ODRP005）

_DGBAS = "https://ws.dgbas.gov.tw/001/Upload/461/relfile/11525/236096/{table}.xml"
LFPR_XML = _DGBAS.format(table="mp04020")
LFPR_DATASET = "行政院主計總處 人力資源調查－歷年年齡組別勞動力參與率"
UNEMPLOYMENT_XML = _DGBAS.format(table="mp04031")
UNEMPLOYMENT_DATASET = "行政院主計總處 人力資源調查－歷年年齡組別失業率"


def fetch(url: str, cache_name: str, *, refresh: bool = False) -> bytes:
    """抓一個 URL，順便存進 _cache/。已經有快取就直接用。"""
    CACHE_DIR.mkdir(exist_ok=True)
    cached = CACHE_DIR / cache_name
    if cached.exists() and not refresh:
        return cached.read_bytes()

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        body = resp.read()
    cached.write_bytes(body)
    return body


def fetch_json(url: str, cache_name: str, *, refresh: bool = False) -> dict:
    return json.loads(fetch(url, cache_name, refresh=refresh).decode("utf-8"))
