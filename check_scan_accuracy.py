"""考 AI 一張有標準答案的表，算出準確率。

問題：怎麼知道 AI 把掃描檔讀對了？
答案：**拿一張我們已經知道正確答案的表去考它。**

標準答案哪來的：`tests/fixtures/table32_ntpc_truth.json`
那是 `data/fetch_employment.py` 從主計總處的 ODS 檔解析出來的，
而且通過了三道加總檢查，所以我們確定它是對的。

測驗卷：`tests/fixtures/table32_sheet.html`
同一份數值排版成政府統計表的樣子。截成圖片，就是一張 AI 沒看過答案的考卷。

這支程式做的事：把圖片餵給 AI → 拿它讀出來的和標準答案逐格比對 → 算分。

執行（賽前，用假後端自我檢查評分邏輯）：
    python check_scan_accuracy.py --self-test

執行（9/12 接上 Bedrock 之後，真的考它）：
    $env:YOUTHLENS_LLM_BACKEND = "bedrock"
    python check_scan_accuracy.py tests/fixtures/table32_sheet.png
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from engine.schema import AgeBand
from llm.backend import StubBackend, load_backend
from llm.scan_table import ScannedTable, parse_response, read_table

TRUTH = Path("tests/fixtures/table32_ntpc_truth.json")
VALUE_TOLERANCE = 0.001          # 數值要完全相同（容許浮點誤差）


@dataclass
class Score:
    matched: int = 0
    wrong: list[str] = None
    missing: list[str] = None
    extra: list[str] = None
    total_ok: bool | None = None
    meta_ok: dict = None

    def __post_init__(self) -> None:
        self.wrong = self.wrong or []
        self.missing = self.missing or []
        self.extra = self.extra or []
        self.meta_ok = self.meta_ok or {}

    @property
    def expected(self) -> int:
        return self.matched + len(self.wrong) + len(self.missing)

    @property
    def rate(self) -> float:
        return self.matched / self.expected if self.expected else 0.0

    @property
    def perfect(self) -> bool:
        return (not self.wrong and not self.missing and not self.extra
                and self.total_ok is not False and all(self.meta_ok.values()))


def _key(label: str) -> str:
    """把「15-24」「15～24 歲」「65歲以上」都正規化成同一個鍵，
    才不會因為寫法不同就被當成讀錯。"""
    try:
        band = AgeBand.parse(label)
    except ValueError:
        return label.strip()
    return f"{band.start}-{band.end}"


def grade(table: ScannedTable, truth: dict) -> Score:
    """逐格比對。AI 讀出來的 vs 我們已知的正確值。"""
    want = {_key(r["age_label"]): float(r["value"]) for r in truth["rows"]}
    got = {_key(r.age_label): r.value for r in table.rows}
    score = Score()

    for key, expected in want.items():
        if key not in got:
            score.missing.append(f"{key}（應為 {expected:,.0f}）")
        elif abs(got[key] - expected) > VALUE_TOLERANCE:
            score.wrong.append(f"{key}：讀成 {got[key]:,.0f}，實際 {expected:,.0f}")
        else:
            score.matched += 1

    score.extra = [f"{k}（{v:,.0f}）" for k, v in got.items() if k not in want]

    if truth.get("printed_total") is not None:
        score.total_ok = (table.printed_total is not None
                          and abs(table.printed_total - truth["printed_total"]) <= VALUE_TOLERANCE)

    score.meta_ok = {
        "地區": (table.region or "").strip() == truth["region"],
        "年份": table.year == truth["year"],
        "單位": (table.unit or "").strip() == truth["unit"],
        "指標類型": table.kind.value == ("extensive" if truth["kind"] == "count" else "intensive"),
    }
    return score


def report(score: Score, table: ScannedTable, backend_name: str) -> None:
    print()
    print(f"  後端：{backend_name}")
    print(f"  讀到：{table.summary()}")
    print()
    print(f"  逐格比對　　{score.matched} / {score.expected} 格正確"
          f"　（{score.rate:.1%}）")

    for label, ok in score.meta_ok.items():
        print(f"  {label}　　　　{'✅' if ok else '❌'}")
    if score.total_ok is not None:
        print(f"  合計　　　　{'✅' if score.total_ok else '❌'}")

    for bucket, name in ((score.wrong, "讀錯"), (score.missing, "漏讀"), (score.extra, "多讀")):
        for item in bucket:
            print(f"    ❌ {name}　{item}")

    if table.issues:
        print()
        print("  程式自己的查核（不看標準答案也抓得到的）：")
        for issue in table.issues:
            print(f"    {issue}")

    print()
    if score.perfect:
        print("  ✅ 全部正確。")
    else:
        print("  ⚠️ 有落差。掃描檔判讀的結果必須人工確認後才能進儀表板。")
    print()


def main(argv: list[str]) -> int:
    if not TRUTH.exists():
        print(f"找不到標準答案 {TRUTH}")
        return 1
    truth = json.loads(TRUTH.read_text(encoding="utf-8"))

    print()
    print("  掃描檔判讀準確率測驗")
    print(f"  標準答案：{truth['source']}")

    if "--self-test" in argv:
        # 賽前自我檢查：確認評分程式本身是對的。
        # 餵完全正確的答案應該滿分；故意改錯一格應該被抓到。
        payload = {**truth, "unreadable": [], "notes": ""}
        backend = StubBackend({"__default__": json.dumps(payload, ensure_ascii=False)})
        table = read_table("（自我檢查，未使用圖片）", backend)
        score = grade(table, truth)
        report(score, table, "stub（自我檢查）")
        if not score.perfect:
            print("  ❌ 評分程式本身有問題：餵標準答案卻不滿分")
            return 1

        broken = json.loads(json.dumps(payload))
        broken["rows"][1]["value"] = 815.0        # 215 → 815
        s2 = grade(parse_response(json.dumps(broken, ensure_ascii=False)), truth)
        ok = len(s2.wrong) == 1 and s2.total_ok is True
        print(f"  故意改錯一格　→　{'✅ 抓到了' if ok else '❌ 沒抓到'}"
              f"（{s2.wrong[0] if s2.wrong else '—'}）")
        print()
        print("  評分程式驗證完畢。9/12 接上 Bedrock 後，用真圖片再跑一次就是真實準確率。")
        print()
        return 0 if ok else 1

    images = [a for a in argv if not a.startswith("--")]
    if not images:
        print()
        print("  用法：python check_scan_accuracy.py <圖片路徑>")
        print("        python check_scan_accuracy.py --self-test")
        print()
        print("  還沒有圖片？把 tests/fixtures/table32_sheet.html 用瀏覽器打開，")
        print("  截圖存成 tests/fixtures/table32_sheet.png 就可以了。")
        print()
        return 1

    backend = load_backend()
    if isinstance(backend, StubBackend):
        print()
        print("  ⚠️ 目前是假後端，這樣測沒有意義。")
        print("     設定 YOUTHLENS_LLM_BACKEND=bedrock 才是真的考它。")
        return 1

    table = read_table(images[0], backend)
    score = grade(table, truth)
    report(score, table, backend.name)
    return 0 if score.perfect else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
