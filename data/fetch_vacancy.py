"""抓歷年各業廠商職缺數。—— 主計總處 事業人力僱用狀況調查

「職缺」是缺工的官方定義：廠商已經開缺、正在找人、但還沒找到人的職位數。
這是命題「預測哪些領域缺工」唯一有官方統計背書的指標。

檔案格式與 mp04020／mp04031 相同（DataCollection → 一筆一期）。
1997 年 2 月起，一年四次，共十八個行業。

⚠️ 2019 年以前不含研究發展服務業、學前教育及社會工作服務業（官方備註），
   所以做趨勢時只取近十年，避免被統計範圍改變污染。

單獨執行：python data/fetch_vacancy.py
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.sources import fetch  # noqa: E402

VACANCY_XML = "https://ws.dgbas.gov.tw/001/Upload/461/relfile/11525/230513/mp05005.xml"
DATASET = "行政院主計總處 事業人力僱用狀況調查－歷年各業廠商職缺數"
LANDING = "https://data.gov.tw/dataset/9857"

AGGREGATES = {"工業及服務業", "工業", "服務業"}
_TAG = re.compile(r"^(.+?)_職缺個數")


def fetch_vacancies(*, refresh: bool = False) -> dict[str, dict[str, float]]:
    """回傳 {行業: {年月別: 職缺數}}。合計欄位（工業／服務業／總計）也一併回傳。"""
    root = ET.fromstring(fetch(VACANCY_XML, "dgbas_mp05005_vacancy.xml", refresh=refresh))
    out: dict[str, dict[str, float]] = {}
    for record in root:
        period = (record.findtext("年月別_Year_and_month") or "").strip()
        if not period:
            first = list(record)[0]
            period = (first.text or "").strip()
        for field in record:
            hit = _TAG.match(field.tag)
            if not hit or not (field.text or "").strip():
                continue
            try:
                out.setdefault(hit.group(1), {})[period] = float(field.text)
            except ValueError:
                continue
    if not out:
        raise RuntimeError("職缺資料解析後為空，來源格式可能改了")
    return out


if __name__ == "__main__":
    from data.forecast import annual_means

    data = fetch_vacancies()
    latest = max(next(iter(data.values())))
    print(f"{DATASET}\n最新一期 {latest}　共 {len(data)} 個行業欄位\n")
    rows = sorted(
        ((k, v[latest]) for k, v in data.items() if latest in v and k not in AGGREGATES),
        key=lambda t: -t[1],
    )
    print(f"  {'行業':<22}{'職缺數':>9}")
    for name, v in rows[:8]:
        print(f"  {name:<22}{v:>9,.0f}")
    print(f"\n  年平均序列涵蓋 {min(annual_means(data['製造業']))}–{max(annual_means(data['製造業']))}")
