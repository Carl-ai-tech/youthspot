"""把三個資料源組合成引擎吃的參考資料檔。

    戶政司 ODRP014  ─┐
                     ├─→ reference_ntpc.json ─→ engine/reference.py
    主計總處 mp04020 ─┤
    主計總處 mp04031 ─┘

執行：python data/build_reference.py          （用快取，離線也能跑）
      python data/build_reference.py --refresh （重新下載）

拆組時的權重不能亂用（見 ungroup.py 開頭）：
    勞參率 → 用 P(a) 加權
    失業率 → 用 P(a)·勞參率(a) 加權，因為失業率的分母是勞動力不是人口

產出的檔案自帶來源網址、期別、抓取日期，前端要顯示「資料來源」直接讀這裡。
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data import sources  # noqa: E402
from data.fetch_labour import (  # noqa: E402
    labour_force_participation,
    latest,
    unemployment,
)
from data.fetch_lfpr_local import fetch_local_lfpr  # noqa: E402
from data.fetch_population import fetch_population  # noqa: E402
from data.ungroup import ungroup  # noqa: E402

OUTPUT = sources.DATA_DIR / "reference_ntpc.json"
AGE_RANGE = (15, 64)

_UNGROUP_NOTE = (
    "官方僅公布五歲組，已用 PCHIP 保單調插值拆成單一年齡，"
    "並在組內等比例校準使各組加權平均精確等於官方公布值。"
    "組內分布為推估，組間加總為官方值。"
)


def build(*, refresh: bool = False, region: str = "新北市") -> dict:
    pop, pop_meta = fetch_population(region, age_range=AGE_RANGE, refresh=refresh)
    ages = range(AGE_RANGE[0], AGE_RANGE[1] + 1)

    lfpr_year, lfpr_bands = latest(labour_force_participation(refresh=refresh))
    unemp_year, unemp_bands = latest(unemployment(refresh=refresh))

    # 只留完全落在我們年齡範圍內的組別，免得 45-64 那幾組把外推拖歪
    def usable(bands: dict) -> dict:
        return {b: v for b, v in bands.items()
                if b[0] >= AGE_RANGE[0] and b[1] <= AGE_RANGE[1]}

    pop_w = {a: float(pop[a]) for a in ages}

    # 縣市自己的分齡勞參率優先。官方縣市表沒有細分 15-19 / 20-24，
    # 那兩組借全國的形狀但校準到本地的 15-24 合計 ——
    # 這樣既保住 18 歲那道坎，又不會整條曲線都是別人的水準。
    local = fetch_local_lfpr(region, refresh=refresh)
    bands = dict(usable(lfpr_bands))
    local_note = f"全國曲線（{lfpr_year} 年平均）"
    target = local["by_band"].get((15, 24))
    borrowed = [b for b in bands if b not in local["by_band"]]
    if target and borrowed:
        w = sum(pop_w[a] for b in borrowed for a in range(b[0], b[1] + 1) if a in pop_w)
        got = sum(pop_w[a] * bands[b] for b in borrowed
                  for a in range(b[0], b[1] + 1) if a in pop_w)
        if got > 0:
            k = target * w / got
            for b in borrowed:
                bands[b] = bands[b] * k
    for b, v in local["by_band"].items():
        if b in bands:
            bands[b] = v
    if target:
        local_note = (
            f"{region}自有分齡值（主計總處表29，{local['year']}）；"
            f"15-19／20-24 官方未細分，借全國形狀並校準至本地 15-24 合計 {target:.1%}"
        )

    lfpr = ungroup(bands, pop_w)

    # 失業率的權重是勞動力 = 人口 × 勞參率
    labour_w = {a: pop_w[a] * lfpr[a] for a in ages}
    unemp = ungroup(usable(unemp_bands), labour_w)

    retrieved = date.today().isoformat()
    return {
        "_status": "OFFICIAL",
        "_note": (
            f"由 data/build_reference.py 於 {retrieved} 產生。"
            "人口為月底數、勞動統計為年平均，兩者時點不同。"
        ),
        "region": region,
        "year": 2000 + int(pop_meta["period"][:3]) - 89,
        "population": {
            "source": f"{sources.POPULATION_DATASET}（{pop_meta['roc_period_label']}）",
            "source_url": sources.POPULATION_LANDING,
            "api_url": sources.POPULATION_API.format(period=pop_meta["period"]),
            "unit": "人",
            "villages_aggregated": pop_meta["villages"],
            "retrieved": retrieved,
            "by_age": {str(a): pop[a] for a in ages},
        },
        "rates": {
            "labor_force_participation": {
                "source": f"主計總處 表29 {region}分齡勞參率 ＋ {sources.LFPR_DATASET}",
                "source_url": sources.LFPR_XML,
                "note": f"{local_note}。{_UNGROUP_NOTE}",
                "official_bands": {f"{lo}-{hi}": round(v, 12) for (lo, hi), v in sorted(bands.items())},
                "retrieved": retrieved,
                "by_age": {str(a): round(lfpr[a], 12) for a in ages},
            },
            "unemployment_national": {
                "source": f"{sources.UNEMPLOYMENT_DATASET}（{unemp_year} 年平均，全國）",
                "source_url": sources.UNEMPLOYMENT_XML,
                "note": f"公式 D 的形狀來源。以勞動力（人口×勞參率）加權校準。{_UNGROUP_NOTE}",
                "official_bands": {f"{lo}-{hi}": v for (lo, hi), v in sorted(usable(unemp_bands).items())},
                "retrieved": retrieved,
                "by_age": {str(a): round(unemp[a], 12) for a in ages},
            },
        },
    }


def main(argv: list[str]) -> int:
    region = sources.region_arg(argv)
    global OUTPUT
    OUTPUT = sources.reference_path(region)
    payload = build(refresh="--refresh" in argv, region=region)
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    pop = {int(a): v for a, v in payload["population"]["by_age"].items()}
    lfpr = {int(a): v for a, v in payload["rates"]["labor_force_participation"]["by_age"].items()}
    unemp = {int(a): v for a, v in payload["rates"]["unemployment_national"]["by_age"].items()}

    print(f"寫出 {OUTPUT.relative_to(Path.cwd()) if OUTPUT.is_relative_to(Path.cwd()) else OUTPUT}")
    print(f"  {payload['region']}　{payload['population']['source']}")
    print(f"  18-35 歲人口　{sum(pop[a] for a in range(18, 36)):,} 人")
    print()
    print(f"  {'年齡':<6}{'人口':>10}{'勞參率':>10}{'失業率':>10}")
    for a in (15, 16, 17, 18, 19, 20, 22, 24, 25, 29, 30, 35):
        print(f"  {a:<6}{pop[a]:>10,}{lfpr[a]:>10.2%}{unemp[a]:>10.2%}")
    print()
    print(f"  17→18 歲勞參率跳幅 {lfpr[18] / lfpr[17]:.2f} 倍"
          f"（>1.5 才會被判定為捕捉到結構轉折，公式 B 才維持 medium）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
