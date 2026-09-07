"""真實資料與拆組演算法的測試。

跟 test_align.py 分家的理由：那一支測「公式算得對不對」（用示意資料，
對得上 Spec 手算過程）；這一支測「資料本身可不可信」。

最重要的一條是 test_official_band_totals_are_exact ——
它證明我們把五歲組拆成單一年齡的時候，**沒有偷改官方公布的總量**。
評審問「你們自己插的值憑什麼相信」，答案就是這條：組內分布是推估，
組間加總分毫不差等於主計總處公布的數字。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.backtest import backtest, load_curves  # noqa: E402
from data.ungroup import ungroup  # noqa: E402
from engine import (  # noqa: E402
    AgeBand,
    Confidence,
    MetricKind,
    ReferenceData,
    SourceRecord,
    align_ratio_formula_d,
    weight_formula_a,
    weight_formula_b,
)
from engine.reference import OFFICIAL_FILE  # noqa: E402

LFPR = "labor_force_participation"
HAS_OFFICIAL = OFFICIAL_FILE.exists()
SKIP_MSG = "尚未產生官方資料，先跑 python data/build_reference.py"


class TestUngroup(unittest.TestCase):
    """拆組演算法本身，不碰網路。"""

    BANDS = {(15, 19): 0.0957, (20, 24): 0.6323, (25, 29): 0.9291, (30, 34): 0.9221}
    WEIGHT = {a: 1000.0 + a for a in range(15, 35)}

    def test_band_means_are_reproduced_exactly(self) -> None:
        r = ungroup(self.BANDS, self.WEIGHT)
        for (lo, hi), official in self.BANDS.items():
            ages = range(lo, hi + 1)
            got = (sum(self.WEIGHT[a] * r[a] for a in ages)
                   / sum(self.WEIGHT[a] for a in ages))
            self.assertAlmostEqual(got, official, places=12, msg=f"{lo}-{hi} 對不回官方值")

    def test_never_negative_or_over_cap(self) -> None:
        r = ungroup(self.BANDS, self.WEIGHT)
        for a, v in r.items():
            self.assertGreaterEqual(v, 0.0, f"{a} 歲出現負值")
            self.assertLessEqual(v, 1.0, f"{a} 歲超過 100%")

    def test_cap_is_respected_and_band_still_matches(self) -> None:
        """撞到上限時，剩下的量要分配給其他年齡，組平均仍須命中。"""
        bands = {(20, 24): 0.95}
        weight = {a: 1.0 for a in range(20, 25)}
        r = ungroup(bands, weight, cap=0.97)
        self.assertLessEqual(max(r.values()), 0.97 + 1e-12)
        self.assertAlmostEqual(sum(r.values()) / 5, 0.95, places=12)

    def test_captures_the_18_year_old_break(self) -> None:
        """拆組的全部意義：讓 18 歲那道坎浮出來。

        平掉的話公式 B 會被降成 low，整個儀表板都是 ⚠️。"""
        r = ungroup(self.BANDS, self.WEIGHT)
        self.assertGreater(r[18] / max(r[17], 1e-9), 1.5)

    def test_monotone_where_official_is_monotone(self) -> None:
        """官方組值遞增的區段，插出來不該出現回頭（PCHIP 的重點）。

        只驗到 25 歲：官方 25-29 是 92.9%、30-34 是 92.2%，曲線本來就該在
        27 歲附近見頂後回落，那不是 overshoot。"""
        r = ungroup(self.BANDS, self.WEIGHT)
        vals = [r[a] for a in range(15, 26)]
        for prev, nxt in zip(vals, vals[1:]):
            self.assertGreaterEqual(nxt + 1e-12, prev)


@unittest.skipUnless(HAS_OFFICIAL, SKIP_MSG)
class TestOfficialReference(unittest.TestCase):
    """實際產出的 reference_ntpc.json。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = json.loads(OFFICIAL_FILE.read_text(encoding="utf-8"))
        cls.ref = ReferenceData.load(OFFICIAL_FILE)

    def test_is_not_placeholder(self) -> None:
        self.assertFalse(self.ref.is_placeholder)
        self.assertEqual(self.payload["_status"], "OFFICIAL")

    def test_covers_15_to_64(self) -> None:
        self.assertEqual(self.ref.age_coverage, AgeBand(15, 64))

    def test_every_number_cites_a_source_url(self) -> None:
        """P0-5 資料來源可追溯：前端要顯示來源，這裡就得有。"""
        self.assertTrue(self.payload["population"]["source_url"])
        for block in self.payload["rates"].values():
            self.assertTrue(block["source_url"])
            self.assertTrue(block["note"])

    def test_official_band_totals_are_exact(self) -> None:
        """拆組後回推，每一組的加權平均必須等於官方公布值。"""
        pop = {a: self.ref.population(a) for a in range(15, 65)}
        lfpr = {a: self.ref.rate(LFPR, a) for a in range(15, 65)}

        checks = [
            ("labor_force_participation", lfpr, pop),
            ("unemployment_national",
             {a: self.ref.rate("unemployment_national", a) for a in range(15, 65)},
             {a: pop[a] * lfpr[a] for a in range(15, 65)}),
        ]
        for key, curve, weight in checks:
            for label, official in self.payload["rates"][key]["official_bands"].items():
                lo, hi = (int(x) for x in label.split("-"))
                ages = [a for a in range(lo, hi + 1) if weight[a] > 0]
                got = (sum(weight[a] * curve[a] for a in ages)
                       / sum(weight[a] for a in ages))
                with self.subTest(rate=key, band=label):
                    self.assertAlmostEqual(got, official, places=9)

    def test_population_is_plausible(self) -> None:
        youth = self.ref.population_sum(AgeBand(18, 35))
        self.assertGreater(youth, 500_000)      # 新北市是全國人口最多的直轄市
        self.assertLess(youth, 1_500_000)

    def test_t2_still_beats_t1_on_real_data(self) -> None:
        """換成真實資料後，§5.4 的核心論證必須依然成立。"""
        a = weight_formula_a(self.ref, AgeBand(15, 24), AgeBand(18, 24)).value
        b = weight_formula_b(self.ref, AgeBand(15, 24), AgeBand(18, 24), LFPR).value
        self.assertGreater(b, a)

    def test_shape_reversal_ignores_tiny_wiggles(self) -> None:
        """勞參率在 30-34 歲有個 0.5pp 的小凹陷，那不是結構轉折，不該降級。

        沒有這條保護，門檻只要訂太鬆，整條曲線都會被判成不可靠。
        """
        rev = self.ref.shape_reversal(LFPR, 32)
        self.assertIsNotNone(rev)
        self.assertLess(rev, 0.10)

    def test_shape_reversal_catches_the_unemployment_peak(self) -> None:
        """失業率在 20-24 歲是明顯高峰，組內結構看不見，必須被抓出來。"""
        rev = self.ref.shape_reversal("unemployment_national", 22)
        self.assertIsNotNone(rev)
        self.assertGreaterEqual(rev, 0.10)

    def test_formula_d_downgraded_where_shape_has_a_peak(self) -> None:
        """回測顯示失業率拆不準，所以公式 D 在那個區間必須標 low 而非 medium。

        這條是回測結果直接寫進程式的地方 —— 拿掉它，我們就是在
        宣稱一個自己測過、知道不成立的精度。
        """
        src = SourceRecord(
            region="新北市", year=2025, age_band=AgeBand(15, 24), metric="失業率",
            value=0.08, unit="%", kind=MetricKind.INTENSIVE,
            source_agency="行政院主計總處", source_dataset="人力資源調查",
            numerator=16_000, denominator=200_000, rate_key=LFPR,
        )
        rec = align_ratio_formula_d(
            self.ref, src, AgeBand(18, 24),
            shape_key="unemployment_national", denominator_rate_key=LFPR,
        )
        self.assertIs(rec.provenance.confidence, Confidence.LOW)
        self.assertIn("局部高峰", rec.provenance.note)

    def test_break_detection_survives_real_data(self) -> None:
        """真實曲線也要能被判定為捕捉到 18 歲轉折，否則公式 B 全變 low。"""
        from engine.align import Confidence
        w = weight_formula_b(self.ref, AgeBand(15, 24), AgeBand(18, 24), LFPR)
        self.assertIs(w.confidence, Confidence.MEDIUM)


@unittest.skipUnless(HAS_OFFICIAL, SKIP_MSG)
class TestBacktest(unittest.TestCase):
    """把回測結論鎖成測試，換資料之後如果結論變了會立刻知道。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.curves = load_curves()

    def test_ungrouping_beats_doing_nothing_on_lfpr(self) -> None:
        """勞參率：拆組必須明顯優於「整組平鋪」，否則這個方法沒有存在價值。"""
        official, weight = self.curves["勞動力參與率"]
        _, ours, naive = backtest(official, weight)
        self.assertLess(ours, naive)
        self.assertLess(ours / naive, 0.5)   # 至少要砍掉一半誤差

    def test_backtest_still_flags_unemployment(self) -> None:
        """失業率：方法沒有比較好。這條測試是在**保存一個誠實的壞消息**。

        如果哪天它開始通過（方法變好了），那是好事 —— 但要有人來改這條測試，
        順便重新檢視 align.py 裡的降級規則還需不需要。
        """
        official, weight = self.curves["失業率"]
        _, ours, naive = backtest(official, weight)
        self.assertGreaterEqual(ours, naive)


if __name__ == "__main__":
    unittest.main(verbosity=2)
