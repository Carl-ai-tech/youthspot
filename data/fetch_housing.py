"""居住負擔：房價所得比、貸款負擔率。—— 內政部不動產資訊平台，六都 + 全國，每季

⚠️ **這個來源沒辦法自動抓。** pip.moi.gov.tw 前面有防火牆（F5），
非瀏覽器的請求一律回「Request Rejected」；資料本身又藏在 ASP.NET 表單後面，
沒有固定的檔案網址。所以這支 fetcher 跟其他的不一樣：它**不下載**，
只讀人工下載好的 CSV，並在檔案不在時把下載步驟印出來。

上台可以講這件事：政府資料開放的程度差很多 —— 主計總處給固定網址的 XML，
內政部要人工查詢下載。我們的 pipeline 兩種都接了。

**兩個指標都是家戶層級，不是青年。** 房價所得比 = 中位數房價 ÷ 家戶年所得中位數。
它回答的是「一般家庭要幾年不吃不喝才買得起」，不是「18–35 歲的人」。
接進來的理由是命題有「居住」面向，而且青年的所得低於家戶中位數，
所以真實負擔只會比這個數字更重 —— 這點要在畫面上講，不能讓人誤以為是青年數字。

更新方式（每季一次）：
    1. 開 https://pip.moi.gov.tw/V3/E/SCRE0201.aspx
    2. 查詢項目勾「房價所得比」與「貸款負擔率」，各查一次
    3. 期間 89年第1季 → 最新；區域勾 全國 + 六都
    4. 查詢結果下載 → 兩個 CSV 分別存成
           data/_cache/moi_price_income.csv
           data/_cache/moi_loan_burden.csv
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

CACHE = Path(__file__).resolve().parent / "_cache"
FILES = {
    "房價所得比": CACHE / "moi_price_income.csv",
    "貸款負擔率": CACHE / "moi_loan_burden.csv",
}
DATASET = "內政部不動產資訊平台 房價負擔能力統計（家戶層級）"
LANDING = "https://pip.moi.gov.tw/V3/E/SCRE0201.aspx"
UNITS = {"房價所得比": "倍", "貸款負擔率": "%"}

# 內政部寫「台」，主計總處與我們其他資料寫「臺」。統一成後者，六都比較才對得上。
_CITY = {"台北市": "臺北市", "台中市": "臺中市", "台南市": "臺南市"}
EXPECTED_COLS = ["年度季別", "全國", "新北市", "臺北市", "桃園市", "臺中市", "臺南市", "高雄市"]


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp950", "big5"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise RuntimeError(f"{path.name} 不是 UTF-8 也不是 Big5，無法解碼")
    rows = list(csv.reader(io.StringIO(text)))
    header = [_CITY.get(h.strip(), h.strip()) for h in rows[0]]
    return header, rows[1:]


def _quarter_key(label: str) -> tuple[int, int]:
    """'115Q1' → (2026, 1)。民國轉西元。"""
    y, q = label.upper().split("Q")
    return int(y) + 1911, int(q)


def _repair(metric: str, rows: list[list[str]]) -> list[dict]:
    """修掉來源本身的單位錯誤，並把每一筆修正記錄下來。

    實際遇到：貸款負擔率的 110Q4、111Q1、111Q2 三季**整列**是正常值的 100 倍
    （新北市 5241.11，前後季都是 52–55）。÷100 之後跟鄰季平滑銜接：
    48.46 → 50.02 → 51.45 → 52.41 → 53.66。這是匯出時小數點位移，不是真實變化。

    處理原則：**明顯的單位錯誤可以修，但一定要留下記錄。** 靜靜改掉等於
    我們也在造資料。修正清單會一路帶到 unified.json 的 provenance 裡。

    只修「整列都超過 100」的情況 —— 單一格異常不動，那可能是真的離群值。
    """
    if metric != "貸款負擔率":
        return []
    fixes = []
    for r in rows:
        vals = [float(v) for v in r[1:]]
        if all(v > 100 for v in vals):
            r[1:] = [f"{v / 100:.2f}" for v in vals]
            fixes.append({"quarter": r[0], "issue": "整列數值為正常值的 100 倍（小數點位移）",
                          "action": "÷100", "before_sample": vals[1], "after_sample": vals[1] / 100})
    return fixes


def _verify(metric: str, header: list[str], rows: list[list[str]]) -> None:
    """版面驗證。內政部改欄位順序時要在這裡炸，不要靜靜讀錯欄。"""
    if header != EXPECTED_COLS:
        raise RuntimeError(
            f"{metric} 的欄位跟預期不同：\n  讀到 {header}\n  預期 {EXPECTED_COLS}\n"
            "內政部可能改了版面，先確認再往下跑"
        )
    if len(rows) < 40:
        raise RuntimeError(f"{metric} 只有 {len(rows)} 筆，應該有二十多年的季資料，檔案可能不完整")
    for r in rows[:3]:
        _quarter_key(r[0])                                  # 格式不對會丟例外
        for v in r[1:]:
            float(v)
    # 房價所得比不可能小於 1、貸款負擔率不可能超過 100
    vals = [float(v) for r in rows for v in r[1:]]
    if metric == "房價所得比" and (min(vals) < 1 or max(vals) > 40):
        raise RuntimeError(f"房價所得比的範圍異常：{min(vals)}–{max(vals)}")
    if metric == "貸款負擔率" and (min(vals) < 0 or max(vals) > 100):
        raise RuntimeError(f"貸款負擔率的範圍異常：{min(vals)}–{max(vals)}")


def fetch_housing() -> dict:
    """回傳兩個指標的季序列與最新值。檔案不在時把下載步驟講清楚。"""
    missing = [m for m, p in FILES.items() if not p.exists()]
    if missing:
        raise FileNotFoundError(
            f"找不到 {'、'.join(missing)} 的 CSV。這個來源無法自動下載（防火牆擋非瀏覽器請求），"
            f"請人工從 {LANDING} 查詢後下載，存成：\n"
            + "\n".join(f"    {FILES[m]}" for m in missing)
            + "\n步驟寫在 data/fetch_housing.py 開頭。"
        )

    out: dict = {"source": DATASET, "url": LANDING, "unit": UNITS, "series": {}, "latest": {},
                 "note": "家戶層級，不是青年。青年所得低於家戶中位數，實際負擔只會更重。"}
    quarters_ref: list[str] | None = None
    out["corrections"] = {}
    for metric, path in FILES.items():
        header, rows = _read_csv(path)
        fixes = _repair(metric, rows)        # 先修再驗：驗證器對「錯的來源」也要能過
        if fixes:
            out["corrections"][metric] = fixes
        _verify(metric, header, rows)
        rows.sort(key=lambda r: _quarter_key(r[0]))          # 檔案是新到舊，轉成舊到新
        quarters = [r[0] for r in rows]
        if quarters_ref is None:
            quarters_ref = quarters
        elif quarters != quarters_ref:
            raise RuntimeError("兩個指標的季別對不齊，請確認兩份 CSV 是同一個期間查的")
        cities = header[1:]
        out["series"][metric] = {c: [float(r[i + 1]) for r in rows] for i, c in enumerate(cities)}
        out["latest"][metric] = {c: float(rows[-1][i + 1]) for i, c in enumerate(cities)}

    out["quarters"] = quarters_ref or []
    out["latest_quarter"] = (quarters_ref or [""])[-1]
    out["years"] = [_quarter_key(q)[0] + (_quarter_key(q)[1] - 1) / 4 for q in out["quarters"]]
    return out


if __name__ == "__main__":
    h = fetch_housing()
    print(f"{len(h['quarters'])} 季　{h['quarters'][0]} → {h['quarters'][-1]}")
    for m, fixes in h["corrections"].items():
        print(f"⚠ {m} 修正了 {len(fixes)} 季的來源錯誤：" + "、".join(f["quarter"] for f in fixes))
    for m in FILES:
        lat = h["latest"][m]
        first = {c: h["series"][m][c][0] for c in lat}
        print(f"\n{m}（{UNITS[m]}）最新 {h['latest_quarter']}：")
        for c in ["新北市", "臺北市", "桃園市", "臺中市", "臺南市", "高雄市", "全國"]:
            print(f"  {c:4s} {first[c]:6.2f} → {lat[c]:6.2f}")
