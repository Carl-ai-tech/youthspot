"""AI 統整 Demo —— 命題：「透過 AI 統整…並整合出青年動態」

執行：python demo_ai.py

三幕：
  幕一  AI 看到什麼（撈出來的記錄，以及交給它的規則）
  幕二  模型寫得好的時候 —— 每個數字都比對回得了來源
  幕三  模型編數字的時候 —— **被抓出來**

幕三是重點。RAG 最大的問題不是模型不會寫，
是它會寫出一段很流暢、但其中一兩個數字是編的文字，而讀的人分辨不出來。
"""

from __future__ import annotations

import json
import sys
import unicodedata
from pathlib import Path

from llm.backend import StubBackend, load_backend
from llm.synthesize import build_prompt, retrieve, synthesize, verify

W = 78


def vw(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def wrap(text: str, width: int = 70, indent: str = "    ") -> str:
    out, line = [], ""
    for ch in text:
        line += ch
        if vw(line) >= width and ch in "，。；、）%":
            out.append(line)
            line = ""
    if line:
        out.append(line)
    return "\n".join(indent + ln for ln in out)


def rule(ch: str = "─") -> None:
    print(ch * W)


def scene(n: str, title: str) -> None:
    print()
    rule("━")
    print(f"  幕{n}　{title}")
    rule("━")


# 假裝模型回的兩段話。賽前沒有 Bedrock，但驗證邏輯完全測得到。
GOOD = (
    "新北市 18-35 歲共有 832,214 名青年，是全國青年最集中的城市之一。"
    "值得注意的是 25-29 歲這一群：勞動力參與率在六都排第 1，"
    "平均年薪 59.9 萬元卻只排第 4。"
    "投入勞動市場的意願最高，得到的待遇卻在後段，"
    "這比較像產業結構與通勤外流造成的，而不是青年不夠努力。"
)

HALLUCINATED = (
    "新北市 18-35 歲共有 832,214 名青年，平均月薪為 4.8 萬元，"
    "青年租金負擔率高達 38.2%，居六都之冠。"
    "建議加強社會住宅供給以降低居住壓力。"
)


def main() -> int:
    payload = json.loads(Path("data/unified.json").read_text(encoding="utf-8"))
    backend = load_backend()

    print()
    print("  AI 統整　—　撈 → 生成 → **驗證**")
    if isinstance(backend, StubBackend):
        print("  ⚠️  賽前使用假後端；9/12 設定 YOUTHLENS_LLM_BACKEND=bedrock 即為真實模型")

    # ---------------------------------------------------------------- 幕一
    scene("一", "AI 看到什麼")

    records = retrieve(payload, "新北市", "18-35")
    print(f"    從 {len(payload['records']):,} 筆記錄裡撈出 {len(records)} 筆相關的：")
    print()
    for r in records[:8]:
        edu = f"／{r['education']}" if r.get("education") else ""
        v = (f"{r['value'] * 100:.1f}%" if r["unit"] == "%"
             else f"{r['value']:,.1f} {r['unit']}" if r["unit"].startswith("萬")
             else f"{r['value']:,.0f} {r['unit']}")
        print(f"      {r['age_group']:<7}{r['metric'] + edu:<18}{v:>16}"
              f"   {r['provenance']['confidence']}")
    print(f"      …（共 {len(records)} 筆）")
    print()
    print("    交給模型的規則，一字不改：")
    print()
    for line in build_prompt(payload, records, "新北市", "18-35").splitlines()[:5]:
        print(f"      {line}")
    print("      …")
    print()
    print("    重點：可用的數字全部列在提示詞裡，模型只能挑，不能算也不能編。")

    # ---------------------------------------------------------------- 幕二
    scene("二", "模型寫得好的時候")

    # 幕二用**真正載入的後端**。先前這裡固定塞 StubBackend，所以不管
    # YOUTHLENS_LLM_BACKEND 設成什麼，跑出來的永遠是寫死的罐頭字串 ——
    # 看起來像模型寫的，其實模型一次都沒被呼叫到。
    # （幕三保持罐頭：沒辦法叫模型按需求產生幻覺，那一幕要的是固定的壞例子。）
    g = synthesize(payload, backend if not isinstance(backend, StubBackend)
                   else StubBackend({"__default__": GOOD}))
    print(wrap(g.text))
    print()
    ok, _ = verify(g.text, g.records, payload)
    print(f"    逐一比對：{g.summary()}")
    print(f"    比對到的數字：{'、'.join(ok)}")
    print()
    print("    這段話是模型寫的，但每一個數字都查得到出處。")

    # ---------------------------------------------------------------- 幕三
    scene("三", "模型編數字的時候")

    b = synthesize(payload, StubBackend({"__default__": HALLUCINATED}))
    print(wrap(b.text))
    print()
    print(f"    逐一比對：{b.summary()}")
    print()
    for n in b.unverified:
        print(f"      ❌ 「{n}」在我們的記錄裡找不到")
    print()
    print("    這段話讀起來完全正常，而且結論聽起來很有道理。")
    print("    但「4.8 萬月薪」和「38.2% 租金負擔率」都是編的 ——")
    print("    我們根本沒有租金資料。")
    print()
    print("    一般的 RAG 到「模型寫完」就結束了，這種錯會直接送到使用者面前。")
    print("    我們多做一步：把它寫的每個數字比對回原始記錄，對不上就標出來。")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
