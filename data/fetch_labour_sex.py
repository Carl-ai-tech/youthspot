"""分齡 × 性別 的勞參率與失業率。—— 主計總處 人力資源統計 mp04016 / mp04018 / mp04030，全國，1978–

先前整個專案都說「沒有性別維度」。那是錯的：主計總處這三張歷年表本來就把
每個五歲組拆成 男／女：

    mp04016  15 歲以上民間人口之年齡（分性別）
    mp04018  勞動力之年齡（分性別）
    mp04030  失業者之年齡（分性別）

三張相除就是 分齡 × 性別 的勞參率（勞動力 ÷ 民間人口）與失業率（失業者 ÷ 勞動力）。
命題 §4.2 舉的例子「30–35 歲女性勞參率在 2023 年後下降」正是這種序列 ——
不過只有全國，沒有縣市；上台要講清楚。

跟 fetch_labour.py 一樣：固定網址的 XML、只取「年平均」列、官方填 "-" 的格子跳過。
每一年都做一道恆等式檢查：男 + 女 = 小計（容忍四捨五入 1 千人）。
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.sources import fetch  # noqa: E402

_DIR = "https://ws.dgbas.gov.tw/001/Upload/461/relfile/11525/236096/"
TABLES = {
    "population": ("mp04016", "歷年15歲以上民間人口之年齡"),
    "labour": ("mp04018", "歷年勞動力之年齡"),
    "unemployed": ("mp04030", "歷年失業者之年齡"),
}
DATASET = "主計總處 人力資源統計 歷年分齡（分性別）民間人口／勞動力／失業者"
LANDING = "https://www.stat.gov.tw/cp.aspx?n=2295"

_YEAR_RE = re.compile(r"Ave\.,\s*(\d{4})")
# 年齡25-44歲_25-29歲_男_25-44_years_25-29_years_Male_千人
_CELL_RE = re.compile(r"^年齡\d+-\d+歲_(\d+)-(\d+)歲_(小計|男|女)_.*_千人$")
SEX = {"小計": "total", "男": "m", "女": "f"}
Band = tuple[int, int]


def _parse(xml_bytes: bytes) -> dict[int, dict[Band, dict[str, float]]]:
    """{西元年: {(下限, 上限): {"total": 千人, "m": 千人, "f": 千人}}}，只取年平均。"""
    root = ET.fromstring(xml_bytes)
    out: dict[int, dict[Band, dict[str, float]]] = {}
    for record in root:
        label = (record[0].text or "").strip() if len(record) else ""
        if "年平均" not in label:
            continue
        m = _YEAR_RE.search(label)
        if not m:
            continue
        year = int(m.group(1))
        bands: dict[Band, dict[str, float]] = {}
        for field in record:
            hit = _CELL_RE.match(field.tag)
            if not hit or not (field.text or "").strip():
                continue
            try:
                v = float(field.text.replace(",", ""))
            except ValueError:
                continue                                  # 官方填 "-"
            bands.setdefault((int(hit.group(1)), int(hit.group(2))), {})[SEX[hit.group(3)]] = v
        if bands:
            out[year] = bands
    return out


def _verify(name: str, series: dict[int, dict[Band, dict[str, float]]]) -> None:
    """男 + 女 = 小計。欄位對錯會在這裡炸，不會靜靜算出錯的比率。"""
    if len(series) < 30:
        raise RuntimeError(f"{name} 只有 {len(series)} 個年度，應該有四十多年")
    for year, bands in series.items():
        for band, v in bands.items():
            if {"total", "m", "f"} <= set(v) and abs(v["m"] + v["f"] - v["total"]) > 1.5:
                raise RuntimeError(f"{name} {year} {band}：男 {v['m']} + 女 {v['f']} ≠ 小計 {v['total']}")


def fetch_labour_by_sex(*, refresh: bool = False) -> dict:
    """回傳 {"years": [...], "bands": [...], "lfpr": {sex: {band: [...]}}, "unemployment": {...}}。

    比率型，小數（0.83 = 83%）。某年某組官方填 "-" 時放 None。
    """
    raw = {}
    for key, (table, name) in TABLES.items():
        series = _parse(fetch(_DIR + table + ".xml", f"dgbas_{table}.xml", refresh=refresh))
        _verify(name, series)
        raw[key] = series

    years = sorted(set(raw["population"]) & set(raw["labour"]) & set(raw["unemployed"]))
    bands = sorted({b for y in years for b in raw["labour"][y]})
    out = {"source": DATASET, "url": LANDING, "years": years,
           "bands": [f"{a}-{b}" for a, b in bands], "lfpr": {}, "unemployment": {}}
    for sex in ("total", "m", "f"):
        out["lfpr"][sex], out["unemployment"][sex] = {}, {}
        for band in bands:
            label = f"{band[0]}-{band[1]}"
            lf, ur = [], []
            for y in years:
                pop = raw["population"][y].get(band, {}).get(sex)
                lab = raw["labour"][y].get(band, {}).get(sex)
                une = raw["unemployed"][y].get(band, {}).get(sex)
                lf.append(round(lab / pop, 4) if pop and lab is not None else None)
                ur.append(round(une / lab, 4) if lab and une is not None else None)
            out["lfpr"][sex][label] = lf
            out["unemployment"][sex][label] = ur
    return out


if __name__ == "__main__":
    d = fetch_labour_by_sex()
    print(f"{DATASET}\n{len(d['years'])} 個年度 {d['years'][0]}–{d['years'][-1]}　分組 {d['bands']}\n")
    for band in ("20-24", "25-29", "30-34"):
        f, m = d["lfpr"]["f"][band], d["lfpr"]["m"][band]
        print(f"  {band} 歲勞參率　女 {f[0]:.1%} → {f[-1]:.1%}　男 {m[0]:.1%} → {m[-1]:.1%}")
    f = d["lfpr"]["f"]["30-34"]
    print(f"\n  30-34 歲女性勞參率近五年：", "  ".join(f"{y} {v:.1%}" for y, v in zip(d["years"][-5:], f[-5:])))
