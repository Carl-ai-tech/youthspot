"""掃描檔 Demo —— 命題痛點第一條「格式不一（API／CSV／掃描檔）」

執行：python demo_scan.py

兩幕：
  幕一  一張掃描的統計表 → AI 讀出來 → 引擎對齊成 18-35 歲
  幕二  同一張表，AI 看錯一位數字 → **我們的查核把它抓出來**

幕二才是重點。任何隊伍都能接一個 AI 去讀圖片；
但 AI 讀錯的時候會不會有人發現，那是另一回事。
"""

from __future__ import annotations

import json
import sys
import unicodedata

from engine import TARGET_BANDS, YOUTH_BAND, ReferenceData, align_extensive
from llm.backend import StubBackend, load_backend
from llm.scan_table import read_table, to_source_records

W = 78


def vw(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def pad(s: str, width: int) -> str:
    return s + " " * max(width - vw(s), 0)


def rule(ch: str = "─") -> None:
    print(ch * W)


def scene(n: str, title: str) -> None:
    print()
    rule("━")
    print(f"  幕{n}　{title}")
    rule("━")


# 賽前沒有 AWS 環境，所以用假後端把整條路徑跑通。
# 9/12 把 YOUTHLENS_LLM_BACKEND 設成 bedrock，這支程式一個字都不用改。
CLEAN = {
    "metric": "就業者人數", "unit": "千人", "region": "新北市", "year": 2025,
    "kind": "count",
    "rows": [
        {"age_label": "15-24", "value": 123},
        {"age_label": "25-44", "value": 1022},
        {"age_label": "45-64", "value": 868},
        {"age_label": "65歲以上", "value": 63},
    ],
    "printed_total": 2076, "unreadable": [], "notes": "",
}

MISREAD = json.loads(json.dumps(CLEAN))
MISREAD["rows"][1]["value"] = 1922        # 1022 的 0 被看成 9


def show_table(table) -> None:
    print(f"    {table.summary()}")
    print()
    print(f"    {pad('年齡分組', 14)}{'數值':>10}   解析結果")
    rule()
    for row in table.rows:
        state = f"→ {row.band.label} 歲" if row.band else "✗ 無法解析"
        print(f"    {pad(row.age_label, 14)}{row.value:>10,.0f}   {state}")
    rule()
    if table.printed_total is not None:
        computed = table.computed_total
        mark = "✅" if not any("合計" in i for i in table.issues) else "❌"
        print(f"    {pad('表上印的合計', 14)}{table.printed_total:>10,.0f}")
        print(f"    {pad('我們自己加的', 14)}{computed:>10,.0f}   {mark}")
    print()


def main() -> int:
    ref = ReferenceData.load()
    backend = load_backend()
    if isinstance(backend, StubBackend):
        backend.responses = {"__default__": json.dumps(CLEAN, ensure_ascii=False)}
        print()
        print("  ⚠️  目前使用假後端（賽前 AWS 環境未開通）")
        print("      比賽當天設定 YOUTHLENS_LLM_BACKEND=bedrock 即可切換為真實模型")
    print()
    print("  掃描檔判讀　—　AI 只負責看懂圖片，數字一律交給引擎")

    # ------------------------------------------------------------ 幕一
    scene("一", "一張掃描的統計表，AI 讀出來")

    table = read_table("樣本_主計總處就業者統計表.png", backend)
    show_table(table)
    print("    AI 做的事到這裡就結束了 —— 它只是把圖片上的字變成結構化資料。")
    print("    它沒有做任何計算，也沒有碰年齡對齊。")

    print()
    print("    接下來交給引擎，把「15-24／25-44」對齊成《青年基本法》的口徑：")
    print()
    pool = to_source_records(table)
    rule()
    for band in list(TARGET_BANDS) + [YOUTH_BAND]:
        rec = align_extensive(ref, pool, band)
        if rec is None:
            continue
        p = rec.provenance
        tail = "　← 青年基本法口徑" if band == YOUTH_BAND else ""
        print(f"    {pad(rec.age_group + ' 歲', 12)}{rec.value * 1000:>12,.0f} 人"
              f"   {pad(p.confidence.value, 8)}來源 {p.source_age_group}{tail}")
    rule()
    print("    一張沒有結構的圖片，變成可以跟其他資料源比較的數字。")

    # ------------------------------------------------------------ 幕二
    scene("二", "如果 AI 看錯一位數字呢？")

    print("    掃描檔最常見的錯誤：把 1,022 的 0 看成 9，變成 1,922。")
    print("    模型自己不會發現 —— 它對兩個數字一樣有把握。")
    print()

    backend.responses = {"__default__": json.dumps(MISREAD, ensure_ascii=False)}
    bad = read_table("樣本_主計總處就業者統計表.png", backend)
    show_table(bad)

    for issue in bad.issues:
        print(f"    {issue}")
    print()
    print("    抓到了，而且不是靠另一個 AI 去檢查 —— 是靠加法。")
    print("    我們把每一列自己加一次，跟表上印的合計比對。")
    print("    看錯一位數字一定會讓合計對不上，這種錯誤逃不掉。")
    print()
    print("    這一筆不會靜靜流進儀表板，它會被標示出來等人工核對。")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
