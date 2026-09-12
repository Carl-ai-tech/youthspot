"""各行政區的在地工作機會。—— 主計總處 110 年工業及服務業普查「新北市場所單位經營概況－按行政區別分」

「林口區的工作供需」這種問題，人力資源調查答不了（抽樣撐不到行政區），
但**普查**可以：它是全面清查，每一家場所單位都算，所以能細到行政區。
這份表給每一區的 年底場所單位數、年底從業員工人數、全年生產總額。

從業員工人數是「工作地在該區」的人，不是「住在該區」的人 —— 所以
    在地工作機會密度 = 區內從業員工人數 ÷ 區內 15–64 歲人口
密度低的區（淡水、林口這種住宅區）居民多半通勤出去工作；密度高的區（板橋、中和、
新莊、五股工業區）吸收外區勞動力。這就是行政區層級能講的「供需」。

⚠️ 普查五年一次，最新是 110 年（2021 年底）；下一次 115 年普查要 117 年才會公布。
固定網址（政府資料開放平臺 dataset 15706 列的第三個資源）：
    https://ws.dgbas.gov.tw/001/Upload/461/relfile/11525/234025/mp02013a110.xml
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.sources import fetch  # noqa: E402

XML = "https://ws.dgbas.gov.tw/001/Upload/461/relfile/11525/234025/mp02013a110.xml"
DATASET = "主計總處 110 年工業及服務業普查 新北市場所單位經營概況（按行政區別）"
LANDING = "https://data.gov.tw/dataset/15706"
YEAR = 2021
_UNITS_RE = re.compile(r"^年底場所單位數_家_總計_")
_WORKERS_RE = re.compile(r"^年底從業員工人數_人_")
_PROD_RE = re.compile(r"^全年生產總額_千元_")


def _clean(name: str) -> str:
    return re.sub(r"[\s　]+", "", name).replace("台", "臺")


def _parse(xml_bytes: bytes) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    root = ET.fromstring(xml_bytes)
    areas: dict[str, dict[str, float]] = {}
    total: dict[str, float] = {}
    for record in root:
        name = _clean(record[0].text or "")
        row: dict[str, float] = {}
        for f in record[1:]:
            txt = (f.text or "").strip().replace(",", "")
            if not txt:
                continue
            try:
                v = float(txt)
            except ValueError:
                continue
            if _UNITS_RE.match(f.tag):
                row["units"] = v
            elif _WORKERS_RE.match(f.tag):
                row["workers"] = v
            elif _PROD_RE.match(f.tag):
                row["production"] = v
        if not row:
            continue
        if name in ("總計", "合計"):
            total = row
        else:
            areas["新北市" + name if not name.startswith("新北市") else name] = row
    return areas, total


def _verify(areas: dict[str, dict[str, float]], total: dict[str, float]) -> None:
    if len(areas) != 29:
        raise RuntimeError(f"讀到 {len(areas)} 個行政區，應為 29")
    for key in ("units", "workers"):
        s = sum(a[key] for a in areas.values())
        if abs(s - total[key]) > max(5, total[key] * 0.001):
            raise RuntimeError(f"{key}：各區加總 {s:,.0f} ≠ 總計 {total[key]:,.0f}")


def fetch_district_jobs(*, refresh: bool = False) -> dict:
    areas, total = _parse(fetch(XML, "dgbas_census_110.xml", refresh=refresh))
    _verify(areas, total)
    return {"source": DATASET, "url": LANDING, "year": YEAR, "areas": areas, "total": total,
            "note": "從業員工人數是工作地在該區的人，不是居民；普查五年一次，這是 110 年底的數字。"}


if __name__ == "__main__":
    d = fetch_district_jobs()
    print(f"{DATASET}\n總計 {d['total']['units']:,.0f} 家、{d['total']['workers']:,.0f} 人\n")
    for a, v in sorted(d["areas"].items(), key=lambda kv: -kv[1]["workers"])[:6]:
        print(f"  {a:<10}{v['units']:>8,.0f} 家　{v['workers']:>9,.0f} 人")
