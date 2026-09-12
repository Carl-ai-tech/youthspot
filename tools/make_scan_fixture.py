"""把測驗卷 HTML 轉成 PNG，給掃描檔判讀當輸入。

為什麼要有這支：CLAUDE.md 原本寫「把它截圖存成 PNG」—— 手動步驟，
結果那張圖從來沒被產生過，demo_scan.py 指著一個不存在的檔名跑了很久，
因為 StubBackend 根本不看 image_path。手動步驟遲早會漏，寫成腳本才不會。

用系統上的 Chrome 無頭模式，不需要額外套件（Pillow、playwright 都不用）。

執行：python tools/make_scan_fixture.py
產出：tests/fixtures/table32_sheet.png
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "tests" / "fixtures" / "table32_sheet.html"
OUT = ROOT / "tests" / "fixtures" / "table32_sheet.png"

CANDIDATES = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe",
    Path("/usr/bin/google-chrome"),
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
]


def find_chrome() -> Path | None:
    return next((p for p in CANDIDATES if p.exists()), None)


def main() -> int:
    if not SRC.exists():
        print(f"找不到來源 {SRC}")
        return 1
    chrome = find_chrome()
    if chrome is None:
        print("找不到 Chrome。試過這些位置：")
        for c in CANDIDATES:
            print(f"  {c}")
        print("\nChrome 裝在別的地方的話，直接開那份 HTML 自己截圖存成：")
        print(f"  {OUT}")
        return 1

    # 路徑有中文，要 percent-encode 才進得了 file:// URL
    url = "file:///" + quote(str(SRC).replace("\\", "/"), safe="/:")
    subprocess.run(
        [str(chrome), "--headless=new", "--disable-gpu", "--hide-scrollbars",
         "--default-background-color=FFFFFF", f"--screenshot={OUT}",
         "--window-size=880,760", url],
        check=True, capture_output=True,
    )
    if not OUT.exists():
        print("Chrome 沒有產出檔案")
        return 1

    head = OUT.read_bytes()[:8]
    if head != b"\x89PNG\r\n\x1a\n":
        print("產出的不是合法 PNG")
        return 1
    print(f"OK  {OUT.relative_to(ROOT)}  {OUT.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
