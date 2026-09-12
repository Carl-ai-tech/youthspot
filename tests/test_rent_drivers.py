"""租金 fetcher 的篩選規則，與驅動模型的 OLS。—— 兩個都不碰網路。"""

from __future__ import annotations

import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data import drivers, fetch_rent  # noqa: E402


def _row(**kw):
    base = {"交易標的": "租賃房屋", "建物型態": "公寓(5樓含以下無電梯)", "主要用途": "住家用",
            "租賃住宅服務": "一般代管", "單價元平方公尺": "376", "總額元": "14000", "鄉鎮市區": "三重區"}
    base.update(kw)
    return base


class TestRentFilters(unittest.TestCase):
    def test_residential_rows_kept(self):
        self.assertTrue(fetch_rent._keep(_row()))
        self.assertTrue(fetch_rent._keep(_row(建物型態="住宅大樓(11層含以上有電梯)", 主要用途="集合住宅")))
        self.assertTrue(fetch_rent._keep(_row(交易標的="租賃房屋+車位")))

    def test_non_residential_dropped(self):
        self.assertFalse(fetch_rent._keep(_row(交易標的="車位")))
        self.assertFalse(fetch_rent._keep(_row(建物型態="店面(店鋪)")))
        self.assertFalse(fetch_rent._keep(_row(建物型態="辦公商業大樓", 主要用途="辦公室")))
        self.assertFalse(fetch_rent._keep(_row(主要用途="工業用")))

    def test_social_housing_sublet_excluded_but_managed_kept(self):
        """包租轉租是市價八折以下，不是市場租金；代管是房東房客直接議定，保留。"""
        self.assertFalse(fetch_rent._keep(_row(租賃住宅服務="社會住宅包租轉租")))
        self.assertTrue(fetch_rent._keep(_row(租賃住宅服務="社會住宅代管")))

    def test_zero_or_bad_price_dropped(self):
        self.assertFalse(fetch_rent._keep(_row(單價元平方公尺="0")))
        self.assertFalse(fetch_rent._keep(_row(單價元平方公尺="")))
        self.assertFalse(fetch_rent._keep(_row(單價元平方公尺="abc")))

    def test_seasons_roll_over_years(self):
        self.assertEqual(fetch_rent._seasons("113S3", "114S2"), ["113S3", "113S4", "114S1", "114S2"])

    def test_english_header_row_skipped(self):
        text = "鄉鎮市區,交易標的\nThe villages,transaction sign\n三重區,租賃房屋\n"
        rows = fetch_rent._rows(text)
        self.assertEqual([r["鄉鎮市區"] for r in rows], ["三重區"])

    def test_ping_conversion(self):
        # 376 元/m² × 3.30579 ≈ 1,243 元/坪
        self.assertAlmostEqual(376 * fetch_rent.PING_PER_SQM, 1243, delta=1)


class TestOLS(unittest.TestCase):
    def test_recovers_known_coefficients(self):
        """y = 1 + 2x₁ − 3x₂ + 小雜訊 → 係數要回到 (1, 2, −3)，而且顯著。"""
        rng = random.Random(7)
        X, y = [], []
        for _ in range(120):
            x1, x2 = rng.uniform(0, 5), rng.uniform(-2, 2)
            X.append([x1, x2]); y.append(1 + 2 * x1 - 3 * x2 + rng.gauss(0, 0.1))
        res = drivers.ols(X, y, ["a", "b"])
        self.assertAlmostEqual(res["coef"]["截距"]["b"], 1, delta=0.1)
        self.assertAlmostEqual(res["coef"]["a"]["b"], 2, delta=0.05)
        self.assertAlmostEqual(res["coef"]["b"]["b"], -3, delta=0.05)
        self.assertTrue(res["coef"]["a"]["significant"] and res["coef"]["b"]["significant"])
        self.assertGreater(res["r2"], 0.99)
        self.assertEqual(len(res["resid"]), 120)

    def test_pure_noise_not_significant(self):
        rng = random.Random(3)
        X = [[rng.uniform(0, 1)] for _ in range(80)]
        y = [rng.gauss(0, 1) for _ in range(80)]
        res = drivers.ols(X, y, ["x"])
        self.assertFalse(res["coef"]["x"]["significant"])
        self.assertLess(res["r2"], 0.1)

    def test_singular_matrix_raises(self):
        X = [[1.0, 2.0], [2.0, 4.0], [3.0, 6.0]]
        with self.assertRaises(ValueError):
            drivers.ols(X, [1.0, 2.0, 3.0], ["a", "b"])

    def test_similar_to_orders_by_z_distance(self):
        rows = [
            {"area": "A", "city": "新北市", "z": {"rel_rent": 0, "ln_jobs_density": 0, "ln_income": 0, "ln_youth": 0}, "y": 1, "rent": 1, "jobs_density": 1, "income": 1, "youth": 1},
            {"area": "B", "city": "新北市", "z": {"rel_rent": 0.1, "ln_jobs_density": 0, "ln_income": 0, "ln_youth": 0}, "y": 1, "rent": 1, "jobs_density": 1, "income": 1, "youth": 1},
            {"area": "C", "city": "臺北市", "z": {"rel_rent": 2, "ln_jobs_density": 2, "ln_income": 0, "ln_youth": 0}, "y": 1, "rent": 1, "jobs_density": 1, "income": 1, "youth": 1},
            {"area": "D", "city": "臺北市", "z": None, "y": 1, "rent": 1, "jobs_density": 1, "income": 1, "youth": 1},
        ]
        sim = drivers.similar_to(rows, "A")
        self.assertEqual([s["area"] for s in sim], ["B", "C"])       # D 沒有 z，不進
        self.assertAlmostEqual(sim[1]["distance"], math.sqrt(8), places=2)


if __name__ == "__main__":
    unittest.main()
