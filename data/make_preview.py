"""把 unified.json 包成一個可以雙擊打開的網頁。

為什麼要有這支：unified.json 是給前端吃的，人眼看不出所以然。
這支把資料直接嵌進 HTML，產出一個**不用伺服器、不用網路、雙擊就能開**的頁面，
用來檢查資料對不對、給隊友看格式、賽前自己測。

它不是最終儀表板 —— 那是前端的工作。這是資料層的自我檢查工具。

執行：python data/make_preview.py
產出：preview.html（在專案根目錄，雙擊即可打開）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.sources import DATA_DIR  # noqa: E402

UNIFIED = DATA_DIR / "unified.json"
TEMPLATE = DATA_DIR / "preview_template.html"
OUTPUT = DATA_DIR.parent / "preview.html"
PLACEHOLDER = "/*__DATA__*/"


def _six_cards(current: str) -> list[dict]:
    """首頁的六都卡片：每個城市一張小地圖（淨遷入率上色）＋三個數字。讀六個 unified，沒建的城市略過。"""
    from data import sources as _src
    cards = []
    for city in _src.SIX_CITIES:
        f = _src.unified_path(city)
        if not f.exists():
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        recs = d.get("records", [])
        rate = {r["region"].replace(city, ""): r["value"] for r in recs
                if r.get("metric") == "青年淨遷入率" and r.get("age_group") == "18-35" and r["region"] != city}
        city_rec = {r["metric"]: r["value"] for r in recs if r["region"] == city and r.get("age_group") == "18-35"}
        mig = (d.get("trends") or {}).get("migration") or {}
        signals = [v.get("signal") for k, v in (mig.get("areas") or {}).items() if k != city]
        top = max(rate.items(), key=lambda kv: kv[1]) if rate else None
        geo = d.get("geo") or {}
        cards.append({
            "region": city,
            "href": _src.preview_path(city).name,
            "current": city == current,
            "geo": {"viewBox": geo.get("viewBox"), "districts": {k: {"d": v["d"]} for k, v in (geo.get("districts") or {}).items()}},
            "rate": rate,
            "youth": city_rec.get("人口數"),
            "net": city_rec.get("青年淨遷入人數"),
            "city_rate": city_rec.get("青年淨遷入率"),
            "top": {"name": top[0], "rate": top[1]} if top else None,
            "inflow": sum(1 for s in signals if s and "移入" in s),
            "outflow": sum(1 for s in signals if s and "流出" in s),
            "districts": len(rate),
        })
    return cards


def main() -> int:
    import sys as _sys
    from data import sources as _src
    global UNIFIED, OUTPUT
    region = _src.region_arg(_sys.argv[1:])
    UNIFIED, OUTPUT = _src.unified_path(region), _src.preview_path(region)
    if not UNIFIED.exists():
        print(f"找不到 {UNIFIED.name}，先跑 python data/build_unified.py")
        return 1

    payload = json.loads(UNIFIED.read_text(encoding="utf-8"))
    payload["six"] = _six_cards(region)
    html = TEMPLATE.read_text(encoding="utf-8")
    if PLACEHOLDER not in html:
        print(f"樣板裡找不到 {PLACEHOLDER} 佔位符")
        return 1

    # 嵌在 <script type="application/json"> 裡，只需要防 </script> 提前結束標籤
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    blob = blob.replace("</", r"<\/")
    OUTPUT.write_text(html.replace(PLACEHOLDER, blob), encoding="utf-8")

    print(f"寫出 {OUTPUT.name}　{OUTPUT.stat().st_size / 1024:.0f} KB")
    print(f"  {len(payload['records']):,} 筆記錄已嵌入，不需要伺服器也不需要網路")
    print()
    print("  打開方式：直接雙擊 preview.html，或在檔案總管按右鍵 → 開啟檔案")
    print(f"  完整路徑：{OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
