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
from data.fetch_employment import fetch_employment  # noqa: E402
from data.fetch_population import fetch_population_by_district  # noqa: E402
from data.fetch_salary import fetch_salary  # noqa: E402
from data.fetch_salary_education import (  # noqa: E402
    fetch_salary_by_education,
    fetch_salary_by_industry,
)
from data.fetch_vacancy import AGGREGATES, DATASET as VACANCY_DATASET  # noqa: E402
from data.fetch_vacancy import LANDING as VACANCY_LANDING, fetch_vacancies  # noqa: E402
from data.forecast import annual_means, fit  # noqa: E402
from data.ungroup import ungroup  # noqa: E402
from engine import (  # noqa: E402
    MetricKind,
    SourceRecord,
    TARGET_BANDS,
    align_extensive,
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


def _employment_records(ref, region, refresh) -> list[AlignedRecord]:
    """就業者人數：來源是五歲組，交給引擎對齊。25-29 會完全對上 → high。"""
    emp = fetch_employment(region, refresh=refresh)
    pool = [
        SourceRecord(
            region=region, year=emp["year"], age_band=AgeBand(lo, hi),
            metric="就業者人數", value=v * 1000, unit="人",
            kind=MetricKind.EXTENSIVE,
            source_agency="行政院主計總處", source_dataset=emp["dataset"],
            rate_key=LFPR,
        )
        for (lo, hi), v in emp["by_age"].items() if v
    ]
    out = []
    for band in BANDS:
        rec = align_extensive(ref, pool, band)
        if rec is not None:
            out.append(rec)
    return out


def _salary_records(ref, region, refresh) -> list[AlignedRecord]:
    """年薪：比率型，不能乘權重。

    來源分組 未滿25 / 25-29 / 30-39 / 40-49 / 50-64。
      25-29  完全對上 → 直接抄，high
      其他   用受僱人數當權重把組值拆成單一年齡再重新聚合

    可以這樣拆是因為**薪資隨年齡單調上升**（48.3 → 59.9 → 69.0 → 77.2），
    沒有失業率那種局部高峰。這正是 backtest.py 驗證過「拆得準」的情況。
    """
    sal = fetch_salary(region, refresh=refresh)
    ages = range(15, 65)
    employed = {a: ref.population(a) * ref.rate(LFPR, a) for a in ages}

    out = []
    for kind, curve in (("平均年薪", sal["mean"]), ("中位數年薪", sal["median"])):
        single = ungroup(curve, employed, cap=10_000.0)
        for band in BANDS:
            src = next((b for b in curve if b[0] <= band.start and band.end <= b[1]), None)
            exact = src is not None and src == (band.start, band.end)

            if exact:
                value, conf, method = curve[src], Confidence.HIGH, Method.EXACT_MATCH
                note = f"來源分組 {src[0]}-{src[1]} 與目標完全吻合，原值照抄，未經插補"
            else:
                w = sum(employed[a] for a in band.ages() if a in employed)
                if w <= 0:
                    continue
                value = sum(employed[a] * single[a] for a in band.ages() if a in single) / w
                conf, method = Confidence.MEDIUM, Method.FORMULA_C
                note = (
                    f"薪資為比率型，不能乘權重。改以受僱人數（人口×勞參率）為權重，"
                    f"把官方組值拆成單一年齡後重新聚合。"
                    f"薪資隨年齡單調上升（無局部極值），屬 backtest.py 驗證為可靠的情況"
                )
                if src:
                    note = f"來源分組 {src[0]}-{src[1]} 涵蓋目標但較寬。" + note
                else:
                    note = "目標區間橫跨多個官方分組。" + note

            out.append(AlignedRecord(
                region=region, year=sal["year"], age_group=band.label, gender="total",
                metric=kind, value=round(value, 2), unit=sal["unit"],
                provenance=Provenance(
                    source_agency="行政院主計總處",
                    source_dataset=sal["dataset"],
                    source_age_group=f"{src[0]}-{src[1]}" if src else "多組",
                    method=method,
                    weight=1.0 if exact or src is None else round(value / curve[src], 4),
                    confidence=conf, note=note,
                ),
            ))
    return out


def _regional_factor(sal_local: dict, sal_national: dict):
    """從表6 量出「新北市薪資相對全國的折扣」，而且它隨薪資水準變動。

    表6 同時有全國與新北市、同一個統計口徑，所以逐年齡組相除就得到地區係數：
        未滿25  1.000　　25-29  0.939　　30-39  0.914　　40-49  0.906　　50-64  0.878
    低薪族群幾乎沒差，高薪族群差到 12% —— 用單一係數會把兩端都算錯。

    回傳一個函式：給定「全國薪資水準」，回傳該水準的地區係數（線性內插，兩端夾住）。
    這是把公式 D 的「借形狀、錨水準」換到地區維度上用。
    """
    pts = sorted(
        (sal_national["mean"][b], sal_local["mean"][b] / sal_national["mean"][b])
        for b in sal_local["mean"] if sal_national["mean"].get(b)
    )
    if not pts:
        return (lambda x: 1.0), pts

    def factor(level: float) -> float:
        if level <= pts[0][0]:
            return pts[0][1]
        if level >= pts[-1][0]:
            return pts[-1][1]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 <= level <= x1:
                return y0 + (y1 - y0) * (level - x0) / (x1 - x0)
        return pts[-1][1]

    return factor, pts


def _education_records(region, refresh) -> list[AlignedRecord]:
    """命題點名的「教育程度 × 薪資水準」交叉。

    官方沒有縣市 × 教育程度的薪資統計，只有全國值。但直接把全國數字掛上
    「新北市」再標 low，等於放一個自己都不信的數字 —— 沒有參考價值。

    改成校準：用表6 量出的地區係數（隨薪資水準變動）把全國值調整到新北市水準。
    形狀（學歷之間的差距）借全國，水準錨定在新北市自己的資料上 —— 公式 D 的邏輯。

    ⚠️ 仍然無法做到的事：官方**沒有任何**教育程度 × 年齡的交叉表
    （表28／表32／data.gov.tw 都只有邊際統計），所以這一組是全年齡，
    不能只看 18-35 歲。用兩個邊際去推交叉需要假設兩者獨立，
    但年輕人學歷明顯較高，那個假設是錯的，會產生一個很有自信的錯數字。
    """
    emp = fetch_employment(region, refresh=refresh)
    sal_edu = fetch_salary_by_education(refresh=refresh)
    sal_local = fetch_salary(region, refresh=refresh)
    sal_nat = fetch_salary("總計", refresh=refresh)
    factor, pts = _regional_factor(sal_local, sal_nat)

    # 表32 的「大專及以上」在薪資表分成專科及大學、研究所兩級。
    # 用新北市自己的專科／大學／研究所人數加權合併，而不是隨便挑一個。
    detail = emp.get("by_education_detail", {})
    tertiary = (detail.get("專科", 0) or 0) + (detail.get("大學", 0) or 0)
    graduate = detail.get("研究所", 0) or 0
    if tertiary + graduate > 0:
        national_tertiary = (
            sal_edu["mean"]["專科及大學"] * tertiary + sal_edu["mean"]["研究所"] * graduate
        ) / (tertiary + graduate)
    else:
        national_tertiary = sal_edu["mean"]["專科及大學"]

    NATIONAL = {
        "國中及以下": sal_edu["mean"]["國中及以下"],
        "高級中等": sal_edu["mean"]["高級中等"],
        "大專及以上": national_tertiary,
    }
    span = f"{pts[0][1]:.3f}–{pts[-1][1]:.3f}" if pts else "n/a"
    out = []

    for level, headcount in emp["by_education"].items():
        out.append(AlignedRecord(
            region=region, year=emp["year"], age_group="15-64", gender="total",
            metric="就業者人數", value=headcount * 1000, unit="人", education=level,
            provenance=Provenance(
                source_agency="行政院主計總處", source_dataset=emp["dataset"],
                source_age_group="15-64", method=Method.EXACT_MATCH, weight=1.0,
                confidence=Confidence.HIGH,
                note=f"{region}就業者按最高學歷分，原始統計值，未經插補。"
                     f"⚠️ 這是全年齡就業者，不限 18-35 歲 —— 官方未提供教育程度 × 年齡的交叉表",
            ),
        ))

    for level, national in NATIONAL.items():
        k = factor(national)
        extra = ""
        if level == "大專及以上":
            extra = (f"「大專及以上」由專科及大學（{sal_edu['mean']['專科及大學']} 萬）與"
                     f"研究所（{sal_edu['mean']['研究所']} 萬）依{region}實際人數"
                     f"{tertiary:.0f}：{graduate:.0f} 千人加權合併為 {national:.1f} 萬。")
        out.append(AlignedRecord(
            region=region, year=sal_edu["year"], age_group="15-64", gender="total",
            metric="平均年薪", value=round(national * k, 1), unit=sal_edu["unit"],
            education=level,
            provenance=Provenance(
                source_agency="行政院主計總處",
                source_dataset=f"{sal_edu['dataset']} ＋ 表6（地區校準）",
                source_age_group="15-64",
                method=Method.FORMULA_D_T3, weight=round(k, 4),
                confidence=Confidence.MEDIUM,
                note=(
                    f"{extra}"
                    f"官方無縣市 × 教育程度薪資，故借全國的學歷差距形狀，"
                    f"再用表6 量出的地區係數校準到{region}水準："
                    f"全國 {national:.1f} 萬 × {k:.3f} = {national * k:.1f} 萬。"
                    f"係數不是固定值，而是隨薪資水準變動（觀測範圍 {span}）—— "
                    f"表6 顯示{region}在低薪族群幾乎與全國持平、高薪族群落後約 12%。"
                    f"兩個假設：①地區差距只取決於薪資水準，與學歷本身無關；"
                    f"②表1（各業受僱員工，含部分工時）與表6（本國籍全時受僱員工）"
                    f"統計口徑不同，這裡只借用表6 的「比值」而非「水準」，"
                    f"跨口徑套用比值比套用金額安全，但仍是假設。"
                    f"⚠️ 此列為全年齡（15-64），官方無教育程度 × 年齡交叉表，無法只取 18-35 歲"
                ),
            ),
        ))
    return out


FORECAST_FROM = 2015     # 2019 年以前的行業涵蓋範圍不同，只取近十年
FORECAST_TO = 2027


def _vacancy_records(refresh) -> list[AlignedRecord]:
    """命題的預期成果：預測哪些領域缺工。

    「職缺數」是缺工的官方定義 —— 廠商已開缺、正在找人、還沒找到人的職位數。

    刻意不用機器學習：年度資料點只有十個，訓練出來的模型不可信也無法解釋。
    改用線性外推，並且每一筆都附 95% 預測區間。

    方向判定用**斜率顯著性**（小樣本 t 檢定），不是「預測區間有沒有跨過現值」——
    後者包含了每年的隨機波動，會把明顯在成長的行業也判成看不出來。

    再跟表1 的行業別薪資交叉，讓「哪些領域缺工」後面能接一句「而且薪水如何」。
    """
    vac = fetch_vacancies(refresh=refresh)
    industry_pay = fetch_salary_by_industry(refresh=refresh)
    out: list[AlignedRecord] = []

    for industry, series in vac.items():
        if industry in AGGREGATES:
            continue
        points = sorted((y, v) for y, v in annual_means(series).items() if y >= FORECAST_FROM)
        trend = fit(points)
        if trend is None or trend.last_y <= 0:
            continue
        latest_year = int(trend.last_x)
        yhat, margin = trend.predict(FORECAST_TO)
        arrow = {"up": "成長", "down": "萎縮", "flat": "無明顯趨勢"}[trend.direction()]

        out.append(AlignedRecord(
            region="全國", year=latest_year, age_group="全體", gender="total",
            metric="職缺數", value=round(trend.last_y), unit="個", education=industry,
            provenance=Provenance(
                source_agency="行政院主計總處", source_dataset=VACANCY_DATASET,
                source_age_group="全體", method=Method.EXACT_MATCH, weight=1.0,
                confidence=Confidence.HIGH,
                note=f"{latest_year} 年四次調查的平均值，官方實測，未經推估。"
                     f"職缺＝廠商已開缺、正在找人、尚未找到人的職位數",
            ),
        ))

        out.append(AlignedRecord(
            region="全國", year=FORECAST_TO, age_group="全體", gender="total",
            metric="職缺數預估", value=round(max(yhat, 0)), unit="個", education=industry,
            provenance=Provenance(
                source_agency="行政院主計總處（本專案外推）",
                source_dataset=f"{VACANCY_DATASET}（{FORECAST_FROM}–{latest_year} 年平均）",
                source_age_group="全體",
                method=Method.FORMULA_D_T3,
                weight=round(trend.annual_change_pct, 4),
                confidence=Confidence.MEDIUM if trend.confidence == "medium" else Confidence.LOW,
                note=(
                    f"線性外推，非機器學習模型（資料點僅 {trend.n} 個，訓練模型不可信）。"
                    f"{FORECAST_TO} 年預估 {max(yhat, 0):,.0f} 個，"
                    f"95% 區間 {max(yhat - margin, 0):,.0f}–{yhat + margin:,.0f} 個。"
                    f"年變化 {trend.annual_change_pct:+.1%}，判定為「{arrow}」"
                    f"（斜率 t={trend.t_stat:.2f}，R²={trend.r2:.2f}）。"
                    + ("趨勢在統計上顯著。" if trend.significant
                       else "⚠️ 斜率未達統計顯著，看不出明確方向，這個預估值只能當參考。")
                ),
            ),
            extras={
                "_low": round(max(yhat - margin, 0)),
                "_high": round(yhat + margin),
                "_r2": round(trend.r2, 4),
                "_t": round(trend.t_stat, 3),
                "_direction": trend.direction(),
                "_annual_pct": round(trend.annual_change_pct, 5),
            },
        ))

        pay = industry_pay.get(industry)
        if pay:
            out.append(AlignedRecord(
                region="全國", year=2024, age_group="全體", gender="total",
                metric="行業平均年薪", value=pay, unit="萬元/年", education=industry,
                provenance=Provenance(
                    source_agency="行政院主計總處",
                    source_dataset="工業及服務業全年總薪資統計－表1 各業受僱員工",
                    source_age_group="全體", method=Method.EXACT_MATCH, weight=1.0,
                    confidence=Confidence.HIGH,
                    note="全國各業受僱員工平均年薪，官方統計值。"
                         "行業分類與職缺調查同為標準行業分類，可直接對照",
                ),
            ))
    return out


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

    # 就業與薪資只有縣市層級，沒有行政區細分 —— 只掛在全市那一層
    records.extend(_employment_records(ref, region, refresh))
    records.extend(_salary_records(ref, region, refresh))
    records.extend(_education_records(region, refresh))
    records.extend(_vacancy_records(refresh))

    educations = sorted({r.education for r in records
                         if r.education and r.region != "全國"})
    industries = sorted({r.education for r in records if r.region == "全國" and r.education})
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
            "educations": educations,
            "industries": industries,
            "forecast_year": FORECAST_TO,
            "reference_data": str(ref.path.name) if ref.path else "",
            "sources": [
                {"agency": "內政部戶政司", "dataset": sources.POPULATION_DATASET,
                 "url": sources.POPULATION_LANDING},
                {"agency": "行政院主計總處", "dataset": sources.LFPR_DATASET,
                 "url": sources.LFPR_XML},
                {"agency": "行政院主計總處", "dataset": "人力資源調查 表32 就業者之教育程度與年齡",
                 "url": "https://www.stat.gov.tw/News_Content.aspx?n=4001&s=236078"},
                {"agency": "行政院主計總處", "dataset": "工業及服務業全年總薪資統計 表6／表1",
                 "url": "https://www.stat.gov.tw/News_Content.aspx?n=4580&s=232642"},
                {"agency": "行政院主計總處", "dataset": VACANCY_DATASET,
                 "url": VACANCY_LANDING},
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
