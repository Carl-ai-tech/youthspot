"""對齊引擎的測試。

執行：python -m unittest discover -s tests -v

重點不是覆蓋率，是把「上台會被追問的性質」鎖住：
  - 公式 A / B 的權重要跟 Spec §5.3 / §5.4 的手算結果一致
  - 比率型指標誤用加總型公式必須直接爆掉，不能靜靜算出錯的數字
  - 公式 D 的校準必須自洽（回推來源區間要還原原始比率）
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import (  # noqa: E402
    YOUTH_BAND,
    AgeBand,
    Confidence,
    Method,
    MetricKind,
    ReferenceData,
    SourceRecord,
    align_extensive,
    align_ratio_formula_c,
    align_ratio_formula_d,
    weight_formula_a,
    select_sources,
    weight_formula_b,
)

# 這一支測的是「公式算得對不對」，所以固定用示意資料 —— 它的數字直接對得上
# Spec §5.3 / §5.4 的手算過程。真實資料的性質由 tests/test_real_reference.py 負責。
REF = ReferenceData.load_placeholder()
LFPR = "labor_force_participation"


def employment(band: AgeBand, value: float) -> SourceRecord:
    return SourceRecord(
        region="新北市", year=2025, age_band=band, metric="就業人數",
        value=value, unit="人", kind=MetricKind.EXTENSIVE,
        source_agency="行政院主計總處", source_dataset="人力資源調查",
        rate_key=LFPR,
    )


class TestAgeBandParsing(unittest.TestCase):
    def test_common_formats(self) -> None:
        cases = {
            "18-24": (18, 24),
            "18–24": (18, 24),        # en dash，政府統計表很常見
            "25-29 歲": (25, 29),
            "未滿 25": (0, 24),
            "15 歲以上": (15, 120),
            "65+": (65, 120),
        }
        for label, (lo, hi) in cases.items():
            with self.subTest(label=label):
                band = AgeBand.parse(label)
                self.assertEqual((band.start, band.end), (lo, hi))

    def test_unparseable_raises(self) -> None:
        # 解析不了要明確失敗，才會被路由到 §5.6 的 LLM 語意判讀
        for label in ["青年", "社會新鮮人", "應屆畢業生"]:
            with self.subTest(label=label), self.assertRaises(ValueError):
                AgeBand.parse(label)

    def test_overlap(self) -> None:
        self.assertEqual(AgeBand(15, 24).overlap(AgeBand(18, 24)), AgeBand(18, 24))
        self.assertEqual(AgeBand(25, 44).overlap(AgeBand(18, 35)), AgeBand(25, 35))
        self.assertIsNone(AgeBand(15, 24).overlap(AgeBand(25, 29)))


class TestFormulaAB(unittest.TestCase):
    """對照 Spec §5.3 / §5.4 的手算結果。"""

    def test_formula_a_matches_spec(self) -> None:
        w = weight_formula_a(REF, AgeBand(15, 24), AgeBand(18, 24))
        self.assertAlmostEqual(w.value, 308 / 425, places=6)
        self.assertAlmostEqual(w.value, 0.7247, places=4)
        # T1 一律低信心度
        self.assertIs(w.confidence, Confidence.LOW)

    def test_formula_b_matches_spec(self) -> None:
        w = weight_formula_b(REF, AgeBand(15, 24), AgeBand(18, 24), LFPR)
        self.assertAlmostEqual(w.value, 171.50 / 177.35, places=4)
        self.assertAlmostEqual(w.value, 0.9670, places=4)

    def test_t2_beats_t1_on_employment(self) -> None:
        """T2 必須顯著高於 T1 —— 這是 §5.4 整段論證的前提。"""
        a = weight_formula_a(REF, AgeBand(15, 24), AgeBand(18, 24)).value
        b = weight_formula_b(REF, AgeBand(15, 24), AgeBand(18, 24), LFPR).value
        self.assertGreater(b - a, 0.2)

    def test_exact_match_is_high_confidence(self) -> None:
        rec = align_extensive(REF, [employment(AgeBand(25, 29), 100_000)], AgeBand(25, 29))
        assert rec is not None
        self.assertIs(rec.provenance.method, Method.EXACT_MATCH)
        self.assertIs(rec.provenance.confidence, Confidence.HIGH)
        self.assertEqual(rec.value, 100_000)

    def test_structural_break_not_double_penalised(self) -> None:
        """切割點在 18 歲，但勞參率曲線本身就在 18 歲跳升（5% → 40%），
        代表 T2 已經吃進這個轉折，不該再降一次信心度。"""
        w = weight_formula_b(REF, AgeBand(15, 24), AgeBand(18, 24), LFPR)
        self.assertIs(w.confidence, Confidence.MEDIUM)

    def test_missing_rate_curve_falls_back_to_t1(self) -> None:
        src = employment(AgeBand(15, 24), 100_000)
        src.rate_key = "does_not_exist"
        rec = align_extensive(REF, [src], AgeBand(18, 24))
        assert rec is not None
        self.assertIs(rec.provenance.method, Method.FORMULA_A_T1)
        self.assertIs(rec.provenance.confidence, Confidence.LOW)
        self.assertIn("退回 T1", rec.provenance.note)


class TestExtensiveAggregation(unittest.TestCase):
    def test_assembles_across_multiple_sources(self) -> None:
        """18–35 要由 15–24 和 25–44 兩筆拼起來。"""
        pool = [employment(AgeBand(15, 24), 100_000), employment(AgeBand(25, 44), 420_000)]
        whole = align_extensive(REF, pool, YOUTH_BAND)
        assert whole is not None
        self.assertIn("15-24", whole.provenance.source_age_group)
        self.assertIn("25-44", whole.provenance.source_age_group)

    def test_parts_sum_to_whole(self) -> None:
        """三個標準分組加起來要等於 18–35 整段 —— 加總型的基本自洽性。"""
        pool = [employment(AgeBand(15, 24), 100_000), employment(AgeBand(25, 44), 420_000)]
        parts = sum(
            r.value
            for b in (AgeBand(18, 24), AgeBand(25, 29), AgeBand(30, 35))
            if (r := align_extensive(REF, pool, b)) is not None
        )
        whole = align_extensive(REF, pool, YOUTH_BAND)
        assert whole is not None
        self.assertAlmostEqual(parts, whole.value, places=1)

    def test_no_overlap_returns_none(self) -> None:
        pool = [employment(AgeBand(45, 64), 500_000)]
        self.assertIsNone(align_extensive(REF, pool, AgeBand(18, 24)))

    def test_confidence_takes_the_worst(self) -> None:
        """一筆不用插補 + 一筆要插補，合成後不能宣稱 high。

        來源互不重疊：25-29 整段落在 25-35 內（不用切），
        30-44 跨出目標邊界（要切）。取最差的那一級。
        """
        clean = employment(AgeBand(25, 29), 100_000)
        rough = employment(AgeBand(30, 44), 420_000)
        rec = align_extensive(REF, [clean, rough], AgeBand(25, 35))
        assert rec is not None
        self.assertIsNot(rec.provenance.confidence, Confidence.HIGH)

    def test_prefers_the_source_that_needs_no_cutting(self) -> None:
        """同一個指標有粗細兩種來源時，要挑細的那個。

        要 25-29 歲：內政部的「25-29」直接對得上，
        主計總處的「25-44」要切開。挑對來源等於白賺一級信心度。
        """
        fine = employment(AgeBand(25, 29), 100_000)
        coarse = employment(AgeBand(25, 44), 420_000)
        picked = select_sources([coarse, fine], AgeBand(25, 29))
        self.assertEqual([s.age_band.label for s in picked], ["25-29"])

        rec = align_extensive(REF, [coarse, fine], AgeBand(25, 29))
        assert rec is not None
        self.assertIs(rec.provenance.confidence, Confidence.HIGH)

    def test_overlapping_sources_are_not_double_counted(self) -> None:
        """同一群人不能被加兩次。

        25-29 和 25-44 重疊，兩筆都算會得到 20 萬人 —— 但正確答案就是
        25-29 那筆的 10 萬人。這是 select_sources 修掉的實際 bug。
        """
        pool = [employment(AgeBand(25, 29), 100_000), employment(AgeBand(25, 44), 420_000)]
        rec = align_extensive(REF, pool, AgeBand(25, 29))
        assert rec is not None
        self.assertEqual(rec.value, 100_000)

    def test_fully_contained_source_needs_no_interpolation(self) -> None:
        """來源整段落在目標內時，權重恰好 1.0，不該被當成插補。"""
        rec = align_extensive(REF, [employment(AgeBand(25, 29), 100_000)], AgeBand(25, 35))
        assert rec is not None
        self.assertEqual(rec.value, 100_000)
        self.assertIs(rec.provenance.confidence, Confidence.HIGH)

    def test_intensive_metric_rejected(self) -> None:
        """§5.2 最重要的一條：比率型絕對不能乘權重。必須直接爆掉。"""
        rate = SourceRecord(
            region="新北市", year=2025, age_band=AgeBand(15, 24), metric="失業率",
            value=0.08, unit="%", kind=MetricKind.INTENSIVE,
            source_agency="主計總處", source_dataset="人力資源調查",
        )
        with self.assertRaises(ValueError) as ctx:
            align_extensive(REF, [rate], AgeBand(18, 24))
        self.assertIn("比率型", str(ctx.exception))


class TestRatioMetrics(unittest.TestCase):
    def unemployment(self) -> SourceRecord:
        return SourceRecord(
            region="新北市", year=2025, age_band=AgeBand(15, 24), metric="失業率",
            value=0.08, unit="%", kind=MetricKind.INTENSIVE,
            source_agency="行政院主計總處", source_dataset="人力資源調查",
            numerator=16_000, denominator=200_000, rate_key=LFPR,
        )

    def test_formula_c_same_weight_degenerates(self) -> None:
        """§5.5 的陷阱：同權重會約掉，結果等同原始比率。
        這不是 bug，但必須被標成 low 並在 note 裡講清楚。"""
        rec = align_ratio_formula_c(
            REF, self.unemployment(), AgeBand(18, 24),
            numerator_rate_key=LFPR, denominator_rate_key=LFPR,
        )
        self.assertAlmostEqual(rec.value, 0.08, places=9)
        self.assertIs(rec.provenance.confidence, Confidence.LOW)
        self.assertIn("組內比率相同", rec.provenance.note)

    def test_formula_c_requires_numerator_denominator(self) -> None:
        src = self.unemployment()
        src.numerator = None
        with self.assertRaises(ValueError) as ctx:
            align_ratio_formula_c(REF, src, AgeBand(18, 24))
        self.assertIn("分子", str(ctx.exception))

    def test_formula_d_is_self_consistent(self) -> None:
        """最重要的一條：把 D 聚合回**來源區間本身**，必須還原原始比率。

        這證明校準係數 k 解對了 —— 借來的只有形狀，水準仍錨定在本地合計值上。
        """
        rec = align_ratio_formula_d(
            REF, self.unemployment(), AgeBand(15, 24),
            shape_key="unemployment_national", denominator_rate_key=LFPR,
        )
        self.assertAlmostEqual(rec.value, 0.08, places=9)

    def test_formula_d_produces_new_information(self) -> None:
        """D 用在子區間時必須給出跟原始比率不同的值，否則就白做了。"""
        rec = align_ratio_formula_d(
            REF, self.unemployment(), AgeBand(18, 24),
            shape_key="unemployment_national", denominator_rate_key=LFPR,
        )
        self.assertNotAlmostEqual(rec.value, 0.08, places=4)
        self.assertIs(rec.provenance.method, Method.FORMULA_D_T3)
        self.assertIs(rec.provenance.confidence, Confidence.MEDIUM)

    def test_formula_d_reflects_national_shape(self) -> None:
        """全國 18–21 失業率高於 22–24，所以 18–24 應高於 22–24。"""
        src = self.unemployment()
        younger = align_ratio_formula_d(
            REF, src, AgeBand(18, 21),
            shape_key="unemployment_national", denominator_rate_key=LFPR,
        )
        older = align_ratio_formula_d(
            REF, src, AgeBand(22, 24),
            shape_key="unemployment_national", denominator_rate_key=LFPR,
        )
        self.assertGreater(younger.value, older.value)


class TestProvenance(unittest.TestCase):
    def test_every_record_carries_full_provenance(self) -> None:
        """P0-5：每個數字都要能追溯。schema 的欄位一個都不能少。"""
        rec = align_extensive(REF, [employment(AgeBand(15, 24), 100_000)], AgeBand(18, 24))
        assert rec is not None
        d = rec.to_dict()
        for key in ("region", "year", "age_group", "metric", "value", "unit", "provenance"):
            self.assertIn(key, d)
        for key in ("source_agency", "source_dataset", "source_age_group",
                    "method", "weight", "confidence", "note"):
            self.assertIn(key, d["provenance"])
        self.assertTrue(d["provenance"]["note"], "note 不能是空的 —— 評審會點開來看")

    def test_method_and_confidence_are_serialisable(self) -> None:
        """前端拿到的必須是字串，不是 Python enum 的 repr。"""
        rec = align_extensive(REF, [employment(AgeBand(15, 24), 100_000)], AgeBand(18, 24))
        assert rec is not None
        p = rec.to_dict()["provenance"]
        self.assertIsInstance(p["method"], str)
        self.assertIsInstance(p["confidence"], str)
        self.assertIn(p["confidence"], {"high", "medium", "low", "inferred"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
