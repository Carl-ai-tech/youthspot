"""用 headless Chrome 把儀表板印成 PDF。—— Spec P2-3 的命令列版

儀表板上的「匯出報告」鈕走的是瀏覽器的列印對話框（使用者按「另存為 PDF」）。
這支做同一件事，但不用人按：開 preview.html?report=layout（頁面會自己展開所有
區塊、填上封面），再叫 Chrome --print-to-pdf。拿來檢查列印版面、或批次產報告。

    python tools/make_report_pdf.py                    # → report.pdf
    python tools/make_report_pdf.py out.pdf
    python tools/make_report_pdf.py out.pdf http://localhost:8787/preview.html
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_scan_fixture import find_chrome  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "report.pdf").resolve()
    src = sys.argv[2] if len(sys.argv) > 2 else (ROOT / "preview.html").resolve().as_uri()
    url = src + ("&" if "?" in src else "?") + "report=layout"
    chrome = find_chrome()
    if chrome is None:
        print("找不到 Chrome。裝了 Chrome 之後再跑，或直接在儀表板按「匯出報告」")
        return 1
    subprocess.run(
        [str(chrome), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
         "--virtual-time-budget=4000", f"--print-to-pdf={out}", url],
        check=True, capture_output=True, timeout=120,
    )
    print(f"寫出 {out}　{out.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
