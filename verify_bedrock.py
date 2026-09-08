"""9/12 早上第一件事就跑這支。

比賽的 AWS 環境只在 9/12 08:00 – 9/13 13:00 開放，所以**這會是我們第一次
真的呼叫模型**。賽前所有測試用的都是假後端 —— 邏輯測得很完整，
但「真的連得上」這件事完全沒驗證過。

這支程式把可能出錯的地方逐一檢查，一次講完，
而不是讓你在倒數計時的時候一個一個踩。

執行：
    $env:YOUTHLENS_LLM_BACKEND = "bedrock"
    python verify_bedrock.py

每一項失敗都會告訴你「該去做什麼」，不是只丟一個 traceback。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

W = 76
results: list[tuple[bool, str, str]] = []


def check(name: str):
    """裝飾器：跑一個檢查項，成功印 ✅、失敗印 ❌ 與該怎麼辦。"""
    def deco(fn):
        def run():
            print(f"  ▸ {name}")
            t0 = time.time()
            try:
                detail = fn()
            except Exception as exc:                      # noqa: BLE001
                results.append((False, name, str(exc)))
                print(f"    ❌ {exc}")
                print()
                return False
            results.append((True, name, detail or ""))
            print(f"    ✅ {detail}（{time.time() - t0:.1f} 秒）")
            print()
            return True
        return run
    return deco


@check("anthropic 套件")
def step_package() -> str:
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("沒安裝。執行： pip install anthropic") from None
    return f"已安裝 {getattr(anthropic, '__version__', '')}".strip()


@check("AWS 憑證")
def step_credentials() -> str:
    keys = ("AWS_ACCESS_KEY_ID", "AWS_SESSION_TOKEN", "AWS_PROFILE")
    found = [k for k in keys if os.environ.get(k)]
    if found:
        return f"環境變數 {'、'.join(found)}"
    if (Path.home() / ".aws" / "credentials").exists():
        return "~/.aws/credentials"
    raise RuntimeError(
        "找不到 AWS 憑證。Workshop 環境通常會自動帶入；"
        "若沒有，到 AWS 主控台右上角取得臨時憑證並設成環境變數"
    )


@check("這個帳號有哪些 Bedrock 模型")
def step_models() -> str:
    from llm.backend import DEFAULT_MODEL, DEFAULT_REGION, list_available_models
    models = list_available_models(DEFAULT_REGION)
    if not models:
        raise RuntimeError(
            f"{DEFAULT_REGION} 這一區沒有任何 Anthropic 模型。"
            "到 Bedrock 主控台 → Model access 申請啟用，或換一個 region"
        )
    print(f"      可用：{len(models)} 個")
    for m in models[:8]:
        mark = "  ← 目前設定" if m == DEFAULT_MODEL else ""
        print(f"        {m}{mark}")
    if DEFAULT_MODEL not in models:
        raise RuntimeError(
            f"目前設定的 {DEFAULT_MODEL} 不在清單裡。"
            f"從上面挑一個，設定： $env:YOUTHLENS_BEDROCK_MODEL = \"{models[0]}\""
        )
    return f"{DEFAULT_MODEL} 可用"


@check("真的呼叫一次模型")
def step_call() -> str:
    from llm.backend import load_backend
    backend = load_backend()
    if backend.name != "bedrock":
        raise RuntimeError(
            '目前是假後端。設定： $env:YOUTHLENS_LLM_BACKEND = "bedrock"')
    reply = backend.complete("只回答兩個字：可以")
    if not reply.strip():
        raise RuntimeError("模型回了空字串")
    return f"回應「{reply.strip()[:20]}」"


@check("掃描檔判讀（需要 tests/fixtures/table32_sheet.png）")
def step_scan() -> str:
    img = Path("tests/fixtures/table32_sheet.png")
    if not img.exists():
        raise RuntimeError(
            "還沒有測驗卷圖片。用瀏覽器打開 tests/fixtures/table32_sheet.html，"
            "截圖存成同名的 .png 即可（10 秒）"
        )
    from llm.backend import load_backend
    from llm.scan_table import read_table
    table = read_table(img, load_backend())
    return f"讀出 {len(table.rows)} 列，{'查核全過' if table.trustworthy else '有待確認項'}"


@check("掃描檔準確率（跟標準答案比對）")
def step_accuracy() -> str:
    import check_scan_accuracy as acc
    img = Path("tests/fixtures/table32_sheet.png")
    if not img.exists():
        raise RuntimeError("同上，需要先截圖")
    truth = json.loads(acc.TRUTH.read_text(encoding="utf-8"))
    from llm.backend import load_backend
    from llm.scan_table import read_table
    score = acc.grade(read_table(img, load_backend()), truth)
    if not score.perfect:
        raise RuntimeError(
            f"{score.matched}/{score.expected} 格正確（{score.rate:.0%}）。"
            "不是致命問題，但 demo 前要知道實際準確率"
        )
    return f"{score.matched}/{score.expected} 格全對"


@check("中文問答")
def step_ask() -> str:
    from llm.ask import ask
    from llm.backend import load_backend
    payload = json.loads(Path("data/unified.json").read_text(encoding="utf-8"))
    a = ask("新北市 25-29 歲的平均年薪是多少？", payload, load_backend())
    if not a.answered:
        raise RuntimeError(f"答不出來：{a.text}")
    return a.text[:40]


@check("AI 統整 ＋ 數字驗證")
def step_synth() -> str:
    from llm.backend import load_backend
    from llm.synthesize import synthesize
    payload = json.loads(Path("data/unified.json").read_text(encoding="utf-8"))
    g = synthesize(payload, load_backend())
    if not g.text.strip():
        raise RuntimeError("模型沒有回傳任何文字")
    print(f"      模型寫的：{g.text[:60]}…")
    if not g.trustworthy:
        raise RuntimeError(
            f"{g.summary()}　—— 模型編了數字。"
            "驗證機制正常運作（這正是它存在的目的），但 demo 時要挑通過驗證的那次"
        )
    return g.summary()


def main() -> int:
    print()
    print("━" * W)
    print("  Bedrock 連線檢查　—— 9/12 早上第一件事")
    print("━" * W)
    print()

    steps = [step_package, step_credentials, step_models, step_call,
             step_scan, step_accuracy, step_ask, step_synth]
    for step in steps:
        step()

    ok = sum(1 for p, _, _ in results if p)
    print("━" * W)
    print(f"  {ok} / {len(results)} 項通過")
    if ok < len(results):
        print()
        print("  待處理：")
        for passed, name, detail in results:
            if not passed:
                print(f"    ❌ {name}")
                print(f"       {detail}")
    else:
        print("  全部就緒，可以開始 demo。")
    print("━" * W)
    print()
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
