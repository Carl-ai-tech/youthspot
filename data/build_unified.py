"""產出 unified.json —— 交給前端的那個檔。

這是 A（資料）給 B（介面）的唯一交付物，格式照 Spec §7.3 寫死。
B 只要讀這個檔就能畫圖，不用知道引擎裡面發生什麼事。

目前產出的三個指標，剛好示範了「資料品質決定信心度」：

  人口數      戶政司給到單一年齡 → 想切幾歲就切幾歲 → high
  勞動力人數  只有全國五歲組勞參率 → 要拆組、要借全國形狀 → medium
  勞動力參與率  同上 → medium

同一個儀表板上同時有 high 和 medium，而且差別是**資料本身造成的**，
不是我們挑的。這正是信心度標記要傳達的訊息。

執行：python data/build_unified.py
產出：data/unified.json
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data import sources  # noqa: E402
from data.fetch_population import fetch_population_by_district  # noqa: E402
from engine import (  # noqa: E402
    TARGET_BANDS,
    YOUTH_BAND,
    AgeBand,
    Confidence,
    Method,
    Provenance,
    ReferenceData,
)
from engine.schema import AlignedRecord  # noqa: E402

OUTPUT = sources.DATA_DIR / "unified.json"
LFPR = "labor_force_participation"
BANDS = list(TARGET_BANDS) + [YOUTH_BAND]


def _population_record(region, year, band, count, period_label) -> AlignedRecord:
    return AlignedRecord(
        region=region, year=year, age_group=band.label, gender="total",
        metric="人口數", value=float(count), unit="人",
        provenance=Provenance(
            source_agency="內政部戶政司",
            source_dataset=f"村里戶數、單一年齡人口（{period_label}）",
            source_age_group=band.label,
            method=Method.EXACT_MATCH,
            weight=1.0,
            confidence=Confidence.HIGH,
            note="來源資料本身就是單一年齡，直接加總，未經任何插補",
        ),
    )


def _labour_records(ref, region, year, band, counts, period_label) -> list[AlignedRecord]:
    """勞動力人數與勞參率：人口是真的，勞參率借全國曲線 → medium。"""
    ages = [a for a in band.ages() if a in counts]
    pop = sum(counts[a] for a in ages)
    labour = sum(counts[a] * ref.rate(LFPR, a) for a in ages)
    if pop <= 0:
        return []

    meta = ref.rate_meta(LFPR)
    note = (
        f"人口為戶政司單一年齡實數；勞參率借用{meta.get('source', '全國曲線')}，"
        f"並已拆成單一年齡（組間加總等於官方公布值）。"
        f"全國曲線套用於{region}會有偏差，故標記為 medium"
    )
    prov = lambda: Provenance(
        source_agency="內政部戶政司 / 行政院主計總處",
        source_dataset=f"單一年齡人口（{period_label}） × 分齡勞動力參與率",
        source_age_group=band.label,
        method=Method.FORMULA_B_T2,
        weight=round(labour / pop, 6),
        confidence=Confidence.MEDIUM,
        note=note,
    )
    return [
        AlignedRecord(region=region, year=year, age_group=band.label, gender="total",
                      metric="勞動力人數", value=round(labour, 0), unit="人",
                      provenance=prov()),
        AlignedRecord(region=region, year=year, age_group=band.label, gender="total",
                      metric="勞動力參與率", value=round(labour / pop, 4), unit="%",
                      provenance=prov()),
    ]


def build(*, refresh: bool = False, region: str = "新北市") -> dict:
    ref = ReferenceData.load()
    by_district, meta = fetch_population_by_district(region, refresh=refresh)
    year = 2000 + int(meta["period"][:3]) - 89
    label = meta["roc_period_label"]

    # 全市合計 + 各行政區
    areas: dict[str, dict[int, int]] = {region: {}}
    for site, counts in by_district.items():
        areas[site] = counts
        for a, v in counts.items():
            areas[region][a] = areas[region].get(a, 0) + v

    records: list[AlignedRecord] = []
    for area in [region] + sorted(by_district):
        counts = areas[area]
        for band in BANDS:
            pop = sum(counts[a] for a in band.ages() if a in counts)
            records.append(_population_record(area, year, band, pop, label))
            records.extend(_labour_records(ref, area, year, band, counts, label))

    return {
        "_generated": date.today().isoformat(),
        "_schema": "YouthLens unified v1（Spec §7.3）",
        "meta": {
            "region": region,
            "year": year,
            "period_label": label,
            "areas": len(areas),
            "age_groups": [b.label for b in BANDS],
            "metrics": sorted({r.metric for r in records}),
            "reference_data": str(ref.path.name) if ref.path else "",
            "sources": [
                {"agency": "內政部戶政司", "dataset": sources.POPULATION_DATASET,
                 "url": sources.POPULATION_LANDING},
                {"agency": "行政院主計總處", "dataset": sources.LFPR_DATASET,
                 "url": sources.LFPR_XML},
            ],
        },
        "records": [r.to_dict() for r in records],
    }


def main(argv: list[str]) -> int:
    payload = build(refresh="--refresh" in argv)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    recs = payload["records"]
    conf: dict[str, int] = {}
    for r in recs:
        c = r["provenance"]["confidence"]
        conf[c] = conf.get(c, 0) + 1

    print(f"寫出 data/unified.json　{len(recs):,} 筆記錄　{OUTPUT.stat().st_size / 1024:.0f} KB")
    print(f"  地區 {payload['meta']['areas']} 個（全市 + 各行政區）")
    print(f"  年齡層 {'、'.join(payload['meta']['age_groups'])}")
    print(f"  指標 {'、'.join(payload['meta']['metrics'])}")
    print(f"  信心度分布 " + "　".join(f"{k} {v}" for k, v in sorted(conf.items())))
    print()
    print("  抽樣看一筆：")
    sample = next(r for r in recs if r["region"] == "新北市" and r["age_group"] == "18-35"
                  and r["metric"] == "人口數")
    print("   ", json.dumps(sample, ensure_ascii=False)[:150] + " …")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
