"""趨勢外推的測試。純函式，不碰網路。

重點是「該說不知道的時候，有沒有說不知道」——
一個會把雜訊當成趨勢的預測模型，比沒有預測還危險。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.forecast import annual_means, fit  # noqa: E402


class TestFit(unittest.TestCase):
    def test_perfect_line_is_recovered(self) -> None:
        t = fit([(x, 3.0 * x + 10) for x in range(2015, 2025)])
        assert t is not None
        self.assertAlmostEqual(t.slope, 3.0, places=6)
        self.assertAlmostEqual(t.r2, 1.0, places=9)
        self.assertTrue(t.significant)
        self.assertEqual(t.direction(), "up")

    def test_flat_noise_is_not_called_a_trend(self) -> None:
        """鋸齒但沒有方向的資料必須判成 flat，不能硬找一條趨勢出來。"""
        vals = [100, 104, 98, 103, 99, 102, 97, 101, 100, 103]
        t = fit(list(zip(range(2015, 2025), vals)))
        assert t is not None
        self.assertFalse(t.significant)
        self.assertEqual(t.direction(), "flat")
        self.assertEqual(t.confidence, "low")

    def test_declining_series(self) -> None:
        t = fit([(x, 1000.0 - 20 * (x - 2015)) for x in range(2015, 2025)])
        assert t is not None
        self.assertEqual(t.direction(), "down")
        self.assertTrue(t.significant)

    def test_interval_widens_further_out(self) -> None:
        """外推越遠，區間必須越寬。這是誠實度的最低要求。"""
        vals = [100, 106, 109, 118, 121, 130, 133, 141, 147, 152]
        t = fit(list(zip(range(2015, 2025), vals)))
        assert t is not None
        _, near = t.predict(2026)
        _, far = t.predict(2035)
        self.assertGreater(far, near)

    def test_confidence_never_high(self) -> None:
        """外推不管線多漂亮都不能宣稱 high —— 我們預測的是未來，不是資料。"""
        t = fit([(x, 5.0 * x) for x in range(2000, 2025)])
        assert t is not None
        self.assertIn(t.confidence, {"low", "medium"})

    def test_too_few_points_returns_none(self) -> None:
        self.assertIsNone(fit([(2023, 10.0), (2024, 12.0)]))

    def test_small_sample_is_not_over_confident(self) -> None:
        """只有三個點時，即使斜率不小也不該宣稱可信。

        小樣本用常態的 1.96 會過度自信，必須用 t 臨界值，
        而且點數不足 MIN_POINTS 一律降為 low。
        """
        t = fit([(2022, 100.0), (2023, 112.0), (2024, 118.0)])
        assert t is not None
        self.assertEqual(t.confidence, "low")

    def test_annual_change_is_relative_to_latest(self) -> None:
        t = fit([(x, 100.0 + 10 * (x - 2015)) for x in range(2015, 2025)])
        assert t is not None
        self.assertAlmostEqual(t.last_y, 190.0, places=6)
        self.assertAlmostEqual(t.annual_change_pct, 10.0 / 190.0, places=6)


class TestAnnualMeans(unittest.TestCase):
    def test_collapses_quarters_into_years(self) -> None:
        """職缺調查一年四次，而且調查月份改過。先取年平均把季節性洗掉。"""
        series = {"202402": 10.0, "202405": 20.0, "202408": 30.0, "202411": 40.0,
                  "202302": 5.0, "202308": 15.0}
        self.assertEqual(annual_means(series), {2023: 10.0, 2024: 25.0})

    def test_ignores_unparseable_periods(self) -> None:
        self.assertEqual(annual_means({"合計": 1.0, "202401": 8.0}), {2024: 8.0})


if __name__ == "__main__":
    unittest.main(verbosity=2)
