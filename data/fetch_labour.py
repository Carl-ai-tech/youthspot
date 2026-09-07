"""抓分齡勞參率與分齡失業率。—— Spec §6.3 死穴 2

來源：主計總處人力資源調查
  mp04020  歷年年齡組別勞動力參與率  → 公式 B 的 r(a) 基底
  mp04031  歷年年齡組別失業率        → 公式 D 借用的形狀曲線

兩份 XML 的標籤長得一模一樣，所以共用一支解析器。

三個必須講清楚的限制（會被評審問）：
  1. 只有全國，沒有分縣市 —— 新北市的 r(a) 是借全國的形狀。
     這正是公式 D 的邏輯：形狀外借，水準用本地合計值錨定。
  2. 最細只到五歲組，18 歲的切點卡在 15-19 組裡面 —— 交給 ungroup.py 拆。
  3. 是「年平均」不是「年底」。跟戶政司的月底人口混用會有時點差，
     這個差在 provenance 裡要標出來。

好消息：年平均資料從民國 67 年（1978）到現在整整 48 年沒斷，
命題要的「趨勢預測」時間序列不用另外找。

單獨執行：python data/fetch_labour.py
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.sources import LFPR_XML, UNEMPLOYMENT_XML, fetch  # noqa: E402

Band = tuple[int, int]

_YEAR_RE = re.compile(r"Ave\.,\s*(\d{4})")
_SUB_BAND_RE = re.compile(r"^年齡[\d\-]+歲_(\d+)-(\d+)歲_小計_.*_Total_百分比$")


def _parse(xml_bytes: bytes) -> dict[int, dict[Band, float]]:
    """回傳 {西元年: {(下限, 上限): 比率}}，只取年平均那些列。"""
    root = ET.fromstring(xml_bytes)
    series: dict[int, dict[Band, float]] = {}

    for record in root:
        label = (record.findtext("年月別_Year_and_month") or "").strip()
        if "年平均" not in label:
            continue                      # 月資料跳過，我們要的是年平均
        m = _YEAR_RE.search(label)
        if not m:
            continue
        year = int(m.group(1))

        bands: dict[Band, float] = {}
        for field in record:
            hit = _SUB_BAND_RE.match(field.tag)
            if not hit or not (field.text or "").strip():
                continue
            try:
                bands[(int(hit.group(1)), int(hit.group(2)))] = float(field.text) / 100.0
            except ValueError:
                continue                  # 官方有時填 "-" 表示樣本不足
        if bands:
            series[year] = bands
    return series


def labour_force_participation(*, refresh: bool = False) -> dict[int, dict[Band, float]]:
    return _parse(fetch(LFPR_XML, "dgbas_mp04020_lfpr.xml", refresh=refresh))


def unemployment(*, refresh: bool = False) -> dict[int, dict[Band, float]]:
    return _parse(fetch(UNEMPLOYMENT_XML, "dgbas_mp04031_unemployment.xml", refresh=refresh))


def latest(series: dict[int, dict[Band, float]]) -> tuple[int, dict[Band, float]]:
    year = max(series)
    return year, series[year]


if __name__ == "__main__":
    lfpr, unemp = labour_force_participation(), unemployment()
    y_l, bands_l = latest(lfpr)
    y_u, bands_u = latest(unemp)

    print(f"年度序列涵蓋 {min(lfpr)}–{max(lfpr)}（{len(lfpr)} 年，可直接餵給趨勢預測）")
    print()
    print(f"  {'年齡組':<10}{'勞參率 ' + str(y_l):>14}{'失業率 ' + str(y_u):>14}")
    for band in sorted(bands_l):
        if band[0] > 44:
            continue
        print(f"  {f'{band[0]}-{band[1]} 歲':<10}"
              f"{bands_l[band]:>13.2%}{bands_u.get(band, float('nan')):>14.2%}")
