"""對齊引擎現場 Demo —— 對應 Demo 腳本 1:00–2:10 那一段。

執行：python demo_align.py

畫面分四幕：
  幕一  問題長什麼樣（三個機關、三種年齡分組）
  幕二  T1 vs T2 的差距（72,470 vs 96,700）—— 全場最重要的一張畫面
  幕三  比率型指標為什麼不能直接乘（公式 C 的退化 vs 公式 D）
  幕四  每個數字都有履歷（provenance）
"""

from __future__ import annotations

import sys
import unicodedata

from engine import (
    TARGET_BANDS,
    YOUTH_BAND,
    AgeBand,
    MetricKind,
    ReferenceData,
    SourceRecord,
    align_extensive,
    align_ratio_formula_c,
    align_ratio_formula_d,
    weight_formula_a,
    weight_formula_b,
)

W = 78
LFPR = "labor_force_participation"


def vw(s: str) -> int:
    """終端顯示寬度。中文與全形標點算 2 格，否則欄位會歪掉（投影出去很明顯）。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def pad(s: str, width: int) -> str:
    return s + " " * max(width - vw(s), 0)


def band_rate(ref: ReferenceData, key: str, band: AgeBand) -> float:
    """組別的人口加權平均發生率（例如 15-17 歲的平均勞參率）。"""
    pop = ref.population_sum(band)
    return ref.effective_population(key, band) / pop if pop else 0.0


def band_rate_lf(ref: ReferenceData, key: str, band: AgeBand, base: str) -> float:
    """以勞動力加權的平均比率 —— 失業率的分母是勞動力，不是人口。"""
    ages = list(band.ages())
    w = sum(ref.population(a) * ref.rate(base, a) for a in ages)
    if not w:
        return 0.0
    return sum(ref.population(a) * ref.rate(base, a) * ref.rate(key, a) for a in ages) / w


def rule(ch: str = "─") -> None:
    print(ch * W)


def scene(n: str, title: str) -> None:
    print()
    rule("━")
    print(f"  幕{n}　{title}")
    rule("━")


def main() -> int:
    ref = ReferenceData.load()

    print()
    print("  YouthLens 年齡層對齊引擎")
    print(f"  參考資料：{ref.region} {ref.year} 年，涵蓋 {ref.age_coverage.label} 歲")
    if ref.is_placeholder:
        print("  ⚠️  目前使用示意參考資料（PLACEHOLDER），賽前需換成官方統計")
    else:
        print(f"  人口　{ref.population_source}")
        print(f"  發生率　{ref.rate_meta(LFPR).get('source', '')}")
        print(f"  → {ref.region} 18-35 歲人口 "
              f"{ref.population_sum(YOUTH_BAND):,.0f} 人（《青年基本法》口徑）")

    # ---------------------------------------------------------------- 幕一
    scene("一", "問題：三個機關，三種年齡分組，拼不起來")

    rows = [
        ("主計總處 勞動力統計", "15–24, 25–44, 45–64, 65+"),
        ("勞動部 薪資統計", "未滿 25, 25–29, 30–34, 35–39"),
        ("內政部 人口統計", "15–19, 20–24, 25–29, 30–34, 35–39"),
        ("《青年基本法》", "18–35  ← 我們的目標口徑"),
    ]
    for name, grouping in rows:
        print(f"    {pad(name, 26)}{grouping}")
    print()
    print("    要把「15–24」和「25–44」拼成「18–35」，數學上不可能精確。")
    print("    我們的作法不是假裝精確，而是把不確定性算出來、標出來。")

    # ---------------------------------------------------------------- 幕二
    src = SourceRecord(
        region="新北市",
        year=2025,
        age_band=AgeBand(15, 24),
        metric="就業人數",
        value=100_000,
        unit="人",
        kind=MetricKind.EXTENSIVE,
        source_agency="行政院主計總處",
        source_dataset="人力資源調查",
        rate_key=LFPR,
    )
    target = AgeBand(18, 24)

    wa = weight_formula_a(ref, src.age_band, target)
    wb = weight_formula_b(ref, src.age_band, target, LFPR)

    scene("二", f"加總型指標：T1 與 T2 差了 "
                f"{src.value * (wb.value - wa.value):,.0f} 人")

    print(f"    來源：{src.source_agency}「{src.source_dataset}」")
    print(f"          {src.age_band.label} 歲 {src.metric} = {src.value:,.0f} {src.unit}")
    print(f"    目標：{target.label} 歲")
    print()
    print(f"    公式 A（T1）人口權重        w = {wa.value:.4f}"
          f"   →  {src.value * wa.value:>9,.0f} 人   信心度：{wa.confidence.value}")
    print(f"    公式 B（T2）有效母體加權    w = {wb.value:.4f}"
          f"   →  {src.value * wb.value:>9,.0f} 人   信心度：{wb.confidence.value}")
    print()
    v_a, v_b = src.value * wa.value, src.value * wb.value
    print(f"    差距：{v_b - v_a:,.0f} 人 —— T1 相對 T2 低估了 {(v_b - v_a) / v_b:.1%}")
    print()
    r_teen = band_rate(ref, LFPR, AgeBand(15, 17))
    r_early = band_rate(ref, LFPR, AgeBand(22, 24))
    print(f"    為什麼差這麼多？因為 15–17 歲絕大多數在念高中，勞參率只有 {r_teen:.1%}，")
    print(f"    而 22–24 歲高達 {r_early:.1%}。用總人口當權重，等於假設高中生和大學畢業生")
    print("    一樣可能在就業 —— 這會系統性低估 18–24 的佔比。")
    print("    T2 的成本只是多接一張分齡勞參率表。")

    # 目標分組往往要由多筆來源拼起來：18–35 要同時吃 15–24 和 25–44。
    src_25_44 = SourceRecord(
        region="新北市",
        year=2025,
        age_band=AgeBand(25, 44),
        metric="就業人數",
        value=420_000,
        unit="人",
        kind=MetricKind.EXTENSIVE,
        source_agency="行政院主計總處",
        source_dataset="人力資源調查",
        rate_key=LFPR,
    )
    pool = [src, src_25_44]

    print()
    print(f"    加入第二筆來源：{src_25_44.age_band.label} 歲"
          f" {src_25_44.metric} = {src_25_44.value:,.0f} 人")
    print("    三個標準分組全部對齊（目標分組自動由多筆來源拼接）：")
    rule()
    for band in TARGET_BANDS:
        rec = align_extensive(ref, pool, band)
        if rec is None:
            print(f"    {pad(band.label, 9)}—（無來源涵蓋此區間）")
            continue
        p = rec.provenance
        print(f"    {pad(rec.age_group, 9)}{rec.value:>9,.0f} {rec.unit}"
              f"   來源 {pad(p.source_age_group, 14)}{_badge(p.confidence.value)}")

    whole = align_extensive(ref, pool, YOUTH_BAND)
    assert whole is not None
    print(f"    {pad('18-35', 9)}{whole.value:>9,.0f} {whole.unit}"
          f"   來源 {pad(whole.provenance.source_age_group, 14)}"
          f"{_badge(whole.provenance.confidence.value)}   ← 《青年基本法》口徑")
    rule()

    # 同一個指標常常有好幾個機關發布，分組粗細不同。挑對來源是白賺的精度。
    fine = [
        SourceRecord(
            region="新北市", year=2025, age_band=AgeBand(lo, hi), metric="就業人數",
            value=v, unit="人", kind=MetricKind.EXTENSIVE,
            source_agency="內政部", source_dataset="人口統計",
            rate_key=LFPR,
        )
        for lo, hi, v in [(15, 19, 30_000), (20, 24, 70_000),
                          (25, 29, 102_000), (30, 34, 125_000), (35, 39, 130_000)]
    ]

    print()
    print("    ── 如果同一個指標另有「五歲一組」的來源 ──")
    print("    引擎會自動挑分組較細的那個，能不切就不切：")
    rule()
    for band in TARGET_BANDS:
        rec = align_extensive(ref, fine, band)
        if rec is None:
            continue
        p = rec.provenance
        cut = "完全對上，未插補" if p.confidence.value == "high" else f"來源 {p.source_age_group}"
        print(f"    {pad(rec.age_group, 9)}{rec.value:>9,.0f} {rec.unit}"
              f"   {pad(cut, 22)}{_badge(p.confidence.value)}")
    rule()
    print("    25-29 直接對上內政部的分組 → 沒有插補，信心度 high。")
    print("    另外兩格也只需要切一刀（15-19 和 35-39），而不是整包重算。")

    # ---------------------------------------------------------------- 幕三
    scene("三", "比率型指標：權重會自己約掉")

    unemp = SourceRecord(
        region="新北市",
        year=2025,
        age_band=AgeBand(15, 24),
        metric="失業率",
        value=0.08,
        unit="%",
        kind=MetricKind.INTENSIVE,
        source_agency="行政院主計總處",
        source_dataset="人力資源調查",
        numerator=16_000,
        denominator=200_000,
        rate_key=LFPR,
    )

    print(f"    來源：{unemp.age_band.label} 歲失業率 = {unemp.value:.1%}"
          f"（失業 {unemp.numerator:,.0f} / 勞動力 {unemp.denominator:,.0f}）")
    print()
    print("    ❌ 錯誤示範：直接把比率乘權重")
    print(f"       8% × {wb.value:.4f} = {0.08 * wb.value:.2%}"
          "   ← 統計上毫無意義。失業率不會因為只看一部分人就等比例縮小。")
    print()

    rec_c = align_ratio_formula_c(
        ref, unemp, target,
        numerator_rate_key=LFPR,
        denominator_rate_key=LFPR,
    )
    print(f"    公式 C（分子分母同權重）    {rec_c.value:.2%}"
          f"   信心度：{_badge(rec_c.provenance.confidence.value)}")
    print(f"       → {rec_c.provenance.note}")
    print()

    rec_d = align_ratio_formula_d(
        ref, unemp, target,
        shape_key="unemployment_national",
        denominator_rate_key=LFPR,
    )
    print(f"    公式 D（T3 benchmark 校準）  {rec_d.value:.2%}"
          f"   信心度：{_badge(rec_d.provenance.confidence.value)}")
    print(f"       校準係數 k = {rec_d.extras['_calibration_k']:.4f}")
    bands = ref.rate_meta("unemployment_national").get("official_bands", {})
    shape = "、".join(f"{k} 歲 {v:.1%}" for k, v in list(bands.items())[:3])
    print("       → 借用全國分齡失業率的「形狀」，水準用新北市自己的合計值錨定。")
    print(f"          全國形狀：{shape}　高峰在 20 出頭。")
    print(f"       → 結果只比 15–24 的 {unemp.value:.1%} 高一點點，"
          f"因為 15–17 歲幾乎沒有勞動力，")
    print("          本來就拉不動合計值。這個誠實的小差距，比硬掰一個大差距有說服力。")

    # ---------------------------------------------------------------- 幕四
    scene("四", "每個數字都有履歷（P0-5 資料來源可追溯）")

    rec = align_extensive(ref, [src], target)
    assert rec is not None
    p = rec.provenance
    print(f"    {rec.region} {rec.year} {rec.age_group} 歲 {rec.metric}"
          f" = {rec.value:,.0f} {rec.unit}")
    print()
    for label, val in [
        ("來源機關", p.source_agency),
        ("來源資料集", p.source_dataset),
        ("原始分組", p.source_age_group),
        ("使用公式", p.method.value),
        ("權重", f"{p.weight:.4f}"),
        ("信心度", _badge(p.confidence.value)),
        ("計算說明", p.note),
    ]:
        print(f"      {pad(label, 12)}{val}")
    print()
    print("    評審問「你的數字準嗎」，我們有公式、假設、誤差來源可以回答，")
    print("    而不是「AI 算的」。")
    print()

    return 0


def _badge(conf: str) -> str:
    return {
        "high": "high   ✅ 實線",
        "medium": "medium ⓘ 虛線",
        "low": "low    ⚠️ 虛線＋推估區間",
        "inferred": "inferred 🤖 AI 推定",
    }.get(conf, conf)


if __name__ == "__main__":
    sys.exit(main())
