"""一鍵跑完整條資料流程。—— 命題：「建立能自動抓取並清理公開資料的 pipeline」

    抓 → 讀 → 對齊 → 存 → 產出畫面

執行：
    python run_pipeline.py              用快取（現場 demo 用，不賭網路）
    python run_pipeline.py --refresh    重新下載所有政府資料

每一步都會印出它做了什麼、花了多久、產出什麼。
現場按一次給評審看，比口頭說「我們有 pipeline」有說服力得多。

比賽當天這支會被包成 AWS Lambda，畫面上的按鈕直接觸發它 ——
那時候「按一個按鈕」就是真的按鈕。
"""

from __future__ import annotations

import sys
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

W = 74


def vw(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def pad(s: str, width: int) -> str:
    return s + " " * max(width - vw(s), 0)


def rule(ch: str = "─") -> None:
    print(ch * W)


class Step:
    """一個步驟。失敗時要講清楚是哪一步、為什麼，不要只丟 traceback。"""

    def __init__(self, n: int, total: int, name: str, why: str) -> None:
        self.n, self.total, self.name, self.why = n, total, name, why

    def __enter__(self):
        print()
        print(f"  [{self.n}/{self.total}] {self.name}")
        print(f"        {self.why}")
        self.t0 = time.time()
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed = time.time() - self.t0
        if exc_type is None:
            print(f"        ✅ 完成（{elapsed:.1f} 秒）")
            return False
        print(f"        ❌ 失敗：{exc}")
        print(f"        這一步是「{self.why}」，先確認網路與來源網址是否還在")
        return False


def main(argv: list[str]) -> int:
    refresh = "--refresh" in argv
    started = time.time()

    print()
    rule("━")
    print("  YouthLens 資料流程")
    print(f"  模式：{'重新下載政府資料' if refresh else '使用本機快取（不連網）'}")
    rule("━")

    from data import build_reference, build_unified, make_preview
    from data.sources import CACHE_DIR

    total = 4

    with Step(1, total, "抓 ＋ 讀", "從六個政府資料源下載並解析成表格"):
        # build_reference 內部會呼叫戶政司 API 與主計總處的 XML／ODS
        ref_payload = build_reference.build(refresh=refresh)
        pop = ref_payload["population"]
        print(f"        {pop['source']}")
        print(f"        涵蓋 {len(pop['by_age'])} 個單一年齡"
              f"、{pop.get('villages_aggregated', '?')} 個村里")

    with Step(2, total, "對齊", "把各機關不同的年齡分組換算成 18–35 歲口徑"):
        import json
        build_reference.OUTPUT.write_text(
            json.dumps(ref_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        payload = build_unified.build(refresh=refresh)
        recs = payload["records"]
        conf: dict[str, int] = {}
        for r in recs:
            c = r["provenance"]["confidence"]
            conf[c] = conf.get(c, 0) + 1
        print(f"        {len(recs):,} 筆記錄　"
              + "　".join(f"{k} {v}" for k, v in sorted(conf.items())))

    with Step(3, total, "存", "寫成 unified.json，每個數字都附上來源與算法"):
        build_unified.OUTPUT.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        size = build_unified.OUTPUT.stat().st_size / 1024
        print(f"        {build_unified.OUTPUT.name}　{size:.0f} KB")

    with Step(4, total, "產出畫面", "把資料嵌進網頁，不需要伺服器也能開"):
        make_preview.main()

    cache_mb = sum(f.stat().st_size for f in CACHE_DIR.glob("*")) / 1024 / 1024 \
        if CACHE_DIR.exists() else 0

    print()
    rule("━")
    print(f"  全部完成，共 {time.time() - started:.1f} 秒")
    print(f"  原始檔快取 {cache_mb:.0f} MB　產出 preview.html")
    print()
    print("  下次不加 --refresh 就會直接用快取，現場 demo 不必賭網路。")
    rule("━")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
