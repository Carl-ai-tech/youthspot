"""六都一次建完：reference → unified → preview，每個城市各一份。

    python tools/build_six.py            # 用快取
    python tools/build_six.py --refresh  # 重新下載政府資料

新北市維持原本檔名（reference_ntpc.json / unified.json / preview.html），
其他城市帶縣市名。任何一個城市失敗會印出原因並繼續做下一個，最後總結。
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from data.sources import SIX_CITIES  # noqa: E402

PY = sys.executable


def run(args: list[str]) -> tuple[bool, str]:
    r = subprocess.run([PY, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
    return r.returncode == 0, " / ".join(tail)


def main(argv: list[str]) -> int:
    refresh = ["--refresh"] if "--refresh" in argv else []
    cities = [a for a in argv if a in SIX_CITIES] or SIX_CITIES
    results = {}
    # 驅動模型要六都的資料一起算，所以先跑一次（它會順便把六都的快取抓齊）
    ok, tail = run(["data/drivers.py", *refresh])
    print(f"  drivers   {'✓' if ok else '✗'}  {tail[-160:]}\n")
    for city in cities:
        t0 = time.time()
        steps = [
            ("reference", ["data/build_reference.py", "--region", city, *refresh]),
            ("unified", ["data/build_unified.py", "--region", city, *refresh]),
            ("preview", ["data/make_preview.py", "--region", city]),
        ]
        ok_all = True
        for name, args in steps:
            ok, tail = run(args)
            print(f"  {city} {name:<9} {'✓' if ok else '✗'}  {tail[-160:]}")
            if not ok:
                ok_all = False
                break
        results[city] = ok_all
        print(f"{city}：{'完成' if ok_all else '失敗'}（{time.time() - t0:.0f} 秒）\n")
    bad = [c for c, ok in results.items() if not ok]
    print("六都完成" if not bad else f"失敗：{'、'.join(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
