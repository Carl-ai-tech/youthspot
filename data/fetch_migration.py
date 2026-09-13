"""青年世代淨遷徙。—— 內政部戶政司 ODRP014 單一年齡人口，兩期相減的世代追蹤

「青年人口變少」混了三件事：進入 18 歲的人比離開 35 歲的少（世代縮小）、死亡、遷徙。
青年局要的只有第三個。世代追蹤把前兩個扣掉：

    淨遷入(區, t) = Σ_{a=18..35} [ P(a, t) − P(a−1, t−1年) ]

今年 a 歲的人，一年前是 a−1 歲的同一群人，兩期相減就是這群人一年內淨搬進來多少。
18–35 歲年死亡率約 0.05%，一年 1,000 人不到 1 人 —— **沒有扣**，所以嚴格說這是「世代餘額」，
含死亡與戶籍登記異動（初設、除籍）的近似淨遷徙。文案一律寫「未扣死亡的近似」，不寫「扣掉死亡」。

實測（新北市 2025/7 → 2026/7）：淡水區「人口差」−275，世代淨遷入 **+1,069**；
全市人口差 −21,755，世代淨遷入 +546 —— 人口差幾乎全是少子化，跟青年搬不搬走無關。
用人口差配資源會把錢投錯區。

資料：每年 7 月一期，戶政司 API 從 106 年 7 月（2017，ODRP005）起有，所以序列是 2018–2026 共 9 點。
六都同一個 API、同一份全國檔，抓一次六都都能算。

檢查：Σ 各區 = 全市（同一份資料自然成立，但要驗，欄位位移會在這裡炸）。

限制（要寫在卡片上）：戶籍 ≠ 實際居住 —— 就學就業沒遷戶籍的人看不到；量到的是「願意把戶籍遷來」
的青年，反而更接近定居。社宅一次入住幾百人會造成單年跳動。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.fetch_population import fetch_population_by_district, latest_period  # noqa: E402

DATASET = "內政部戶政司 ODRP014 村里單一年齡人口（世代追蹤：兩期相減）"
LANDING = "https://data.gov.tw/dataset/32973"
FIRST_PERIOD = "10607"          # 107 年 7 月起是 ODRP014；106 年 7 月那一期用同欄位的 ODRP005（再往前 API 沒有）
AGE_LO, AGE_HI = 18, 35


def _periods(latest: str) -> list[str]:
    """107年7月 … 最新期（同月，每年一期）。"""
    y = int(latest[:3]); m = latest[3:]
    return [f"{yy:03d}{m}" for yy in range(int(FIRST_PERIOD[:3]), y + 1)]


def _merge_split_districts(by_district: dict, region: str) -> dict:
    """戶政早期把大區拆成戶政所（「高雄市三民一」「三民二」、「鳳山一」「鳳山二」），
    109 年 7 月起合併回「三民區」「鳳山區」。先併回去，序列才接得起來。"""
    out: dict[str, dict[int, int]] = {}
    for site, counts in by_district.items():
        name = site
        short = site[len(region):] if site.startswith(region) else site
        if not short.endswith("區") and short[:-1]:
            name = region + short[:-1] + "區"          # 三民一 → 三民區
        tgt = out.setdefault(name, {})
        for a, v in counts.items():
            tgt[a] = tgt.get(a, 0) + v
    return out


def fetch_migration(region: str = "新北市", *, refresh: bool = False, period: str | None = None) -> dict:
    """回傳 {"years": [...], "areas": {區: {"net": [...], "rate": [...], "base": [...]}}, "city": {...}}。

    years[i] 是「t 期」的西元年；net[i] 是 t−1 → t 這一年的淨遷入人數；
    rate[i] = net / 一年前的同世代人口（17–34 歲）；pop[i] = t 期的 18–35 歲人口（同一份 ODRP014 快照，
    給地圖的逐年人口與年變化率用 —— 這份資料每年都有，不是只有最新一期）。
    """
    latest = period or latest_period(refresh=refresh)
    periods = _periods(latest)
    snaps: dict[str, dict[str, dict[int, int]]] = {}
    for p in periods:
        by_district, _ = fetch_population_by_district(region, period=p, age_range=(AGE_LO - 1, AGE_HI), refresh=refresh)
        by_district = _merge_split_districts(by_district, region)
        city: dict[int, int] = {}
        for counts in by_district.values():
            for a, v in counts.items():
                city[a] = city.get(a, 0) + v
        by_district[region] = city
        snaps[p] = by_district

    years = [int(p[:3]) + 1911 for p in periods[1:]]
    areas: dict[str, dict] = {}
    names = sorted(set().union(*[set(s) for s in snaps.values()]))
    for name in names:
        net, rate, base, naive, pop = [], [], [], [], []
        for prev_p, cur_p in zip(periods, periods[1:]):
            cur, prev = snaps[cur_p].get(name), snaps[prev_p].get(name)
            pop.append(sum(cur[a] for a in range(AGE_LO, AGE_HI + 1)) if cur else None)
            if not cur or not prev:
                net.append(None); rate.append(None); base.append(None); naive.append(None)
                continue
            n = sum(cur[a] - prev[a - 1] for a in range(AGE_LO, AGE_HI + 1))
            b = sum(prev[a - 1] for a in range(AGE_LO, AGE_HI + 1))
            net.append(n); base.append(b); rate.append(round(n / b, 4) if b else None)
            naive.append(sum(cur[a] for a in range(AGE_LO, AGE_HI + 1)) - sum(prev[a] for a in range(AGE_LO, AGE_HI + 1)))
        areas[name] = {"net": net, "rate": rate, "base": base, "naive": naive, "pop": pop}

    # 檢查：各區淨遷入加總 = 全市
    for i, y in enumerate(years):
        parts = sum(areas[n]["net"][i] or 0 for n in names if n != region)
        if abs(parts - (areas[region]["net"][i] or 0)) > 0:
            raise RuntimeError(f"{y}：各區世代淨遷入加總 {parts} ≠ 全市 {areas[region]['net'][i]}")

    return {"source": DATASET, "url": LANDING, "region": region, "years": years,
            "periods": periods, "areas": areas,
            "note": ("世代追蹤：今年 a 歲 − 去年 a−1 歲，Σ 18–35 歲。扣掉了世代縮小；死亡與戶籍登記異動（初設、除籍）"
                     "未另外校正 —— 18–35 歲年死亡率約 0.05%，所以這是「世代餘額」的近似淨遷徙。"
                     "戶籍資料，量的是把戶籍遷來的青年，不含未遷籍的就學就業者。")}


if __name__ == "__main__":
    region = sys.argv[1] if len(sys.argv) > 1 else "新北市"
    d = fetch_migration(region)
    ys = d["years"]
    print(f"{region} 18–35 歲世代淨遷入　{ys[0]}–{ys[-1]}\n")
    print(f"{'區':<8}" + "".join(f"{y:>7}" for y in ys) + "   最新率")
    order = sorted(d["areas"], key=lambda n: -(d["areas"][n]["rate"][-1] or 0))
    for n in order:
        a = d["areas"][n]
        print(f"{n.replace(region, '') or n:<8}" + "".join(f"{(v if v is not None else 0):>7,}" for v in a["net"]) + f"   {a['rate'][-1]:+.1%}")
