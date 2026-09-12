"""歷年就業者之行業。—— 主計總處 人力資源統計 mp04025，全國，1978–，18 行業，分性別

拿來跟職缺序列（mp05005）疊：職缺是需求，就業人數是供給。
兩條線的方向湊在一起，就是「供需錯配」的訊號：

    職缺 ↑ 就業 →/↓   缺工擴大但人沒進去 → 職訓、實習媒合的機會
    職缺 ↓ 就業 ↑/→   人還在進、缺卻在收 → 轉職輔導的風險

⚠️ 這是**全國、全年齡**。官方沒有「行業 × 年齡」的縣市表（114 年報 44 張表逐一看過），
所以這裡回答的是「哪些行業的供需在錯開」，不是「青年在哪些行業錯配」。
上台要講：青年是進入者，錯配行業對青年的影響最直接，但我們沒有行業 × 年齡的直接數字。

行業名稱跟職缺表完全一致（都是主計總處的行業分類），對得上才能疊。
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.sources import fetch  # noqa: E402

XML = "https://ws.dgbas.gov.tw/001/Upload/461/relfile/11525/236096/mp04025.xml"
DATASET = "主計總處 人力資源統計 歷年就業者之行業（分性別）"
LANDING = "https://www.stat.gov.tw/cp.aspx?n=2295"
UNIT = "千人"

_YEAR_RE = re.compile(r"Ave\.,\s*(\d{4})")
# 工業_製造業_小計_Industry_Manufacturing_Total_千人  /  服務業_教育業_男_..._Male_千人
_CELL_RE = re.compile(r"^(?:工業|服務業)_([^_]+)_(小計|男|女)_.*_千人$")
_TOTAL_RE = re.compile(r"^總計_Total_千人$")
_SECTOR_RE = re.compile(r"^(工業|服務業|農林漁牧業)_合計_.*_千人$")
SEX = {"小計": "total", "男": "m", "女": "f"}


def _parse(xml_bytes: bytes) -> dict[int, dict[str, dict[str, float]]]:
    root = ET.fromstring(xml_bytes)
    out: dict[int, dict[str, dict[str, float]]] = {}
    for record in root:
        label = (record[0].text or "").strip() if len(record) else ""
        if "年平均" not in label:
            continue
        m = _YEAR_RE.search(label)
        if not m:
            continue
        year = int(m.group(1))
        row: dict[str, dict[str, float]] = {}
        for field in record:
            txt = (field.text or "").strip()
            if not txt:
                continue
            try:
                v = float(txt.replace(",", ""))
            except ValueError:
                continue
            if _TOTAL_RE.match(field.tag):
                row.setdefault("總計", {})["total"] = v
                continue
            sec = _SECTOR_RE.match(field.tag)
            if sec:
                row.setdefault("_sector", {})[sec.group(1)] = v
                continue
            hit = _CELL_RE.match(field.tag)
            if hit:
                row.setdefault(hit.group(1), {})[SEX[hit.group(2)]] = v
        if row:
            out[year] = row
    return out


def _verify(series: dict[int, dict[str, dict[str, float]]]) -> None:
    """兩道恆等式：農林漁牧 + 工業 + 服務業 = 總計；各行業小計加總 = 工業 + 服務業；男 + 女 = 小計。"""
    if len(series) < 30:
        raise RuntimeError(f"就業者之行業只有 {len(series)} 個年度")
    for year, row in series.items():
        total = row.get("總計", {}).get("total")
        sec = row.get("_sector", {})
        if total and len(sec) == 3:
            if abs(sum(sec.values()) - total) > max(3.0, total * 0.002):
                raise RuntimeError(f"{year}：三大部門 {sum(sec.values()):.0f} ≠ 總計 {total:.0f}")
        parts = [v["total"] for k, v in row.items() if k not in ("總計", "_sector") and "total" in v]
        if parts and {"工業", "服務業"} <= set(sec):
            s, expect = sum(parts), sec["工業"] + sec["服務業"]
            if abs(s - expect) > max(3.0, expect * 0.002):
                raise RuntimeError(f"{year}：各行業加總 {s:.0f} ≠ 工業＋服務業 {expect:.0f}")
        for k, v in row.items():
            if k.startswith("_"):
                continue
            if {"total", "m", "f"} <= set(v) and abs(v["m"] + v["f"] - v["total"]) > 1.5:
                raise RuntimeError(f"{year} {k}：男 {v['m']} + 女 {v['f']} ≠ 小計 {v['total']}")


def fetch_employment_by_industry(*, refresh: bool = False) -> dict:
    """{"years": [...], "industries": [...], "series": {行業: [千人...]}, "series_f": {...}}"""
    series = _parse(fetch(XML, "dgbas_mp04025.xml", refresh=refresh))
    _verify(series)
    years = sorted(series)
    industries = sorted({k for y in years for k in series[y] if k not in ("總計", "_sector")},
                        key=lambda k: -series[years[-1]].get(k, {}).get("total", 0))
    return {
        "source": DATASET, "url": LANDING, "unit": UNIT, "scope": "全國、全年齡",
        "years": years, "industries": industries,
        "series": {k: [series[y].get(k, {}).get("total") for y in years] for k in industries},
        "series_f": {k: [series[y].get(k, {}).get("f") for y in years] for k in industries},
    }


if __name__ == "__main__":
    d = fetch_employment_by_industry()
    print(f"{DATASET}\n{len(d['years'])} 個年度 {d['years'][0]}–{d['years'][-1]}　{len(d['industries'])} 個行業（{UNIT}）\n")
    for k in d["industries"][:8]:
        s = d["series"][k]
        first = next((v for v in s if v is not None), 0)
        print(f"  {k:<16}{first:>8,.0f} → {s[-1]:>8,.0f}")
