"""Issue #3 的回歸測試：F01 CSV 千分位、F02 ‰、F03 查核漏洞、F06 回測預測年份、F08 比率表、F14 metadata 缺漏。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.backtest_migration import classify  # noqa: E402
from deploy.lambda_handler import _ai  # noqa: E402
from llm.backend import StubBackend  # noqa: E402
from llm.synthesize import _fmt, _numbers_in, verify  # noqa: E402


def _rec(value, unit, metric="x"):
    return {"value": value, "unit": unit, "metric": metric, "region": "r", "age_group": "18-35",
            "provenance": {"confidence": "high"}}


class TestF01CsvQuotedThousands(unittest.TestCase):
    def test_quoted_thousands_parse_to_full_numbers(self):
        csv = '新北市 113年\n年齡,人口數\n18-24,"1,234"\n25-29,"2,345"\n30-35,"3,456"\n'
        r = _ai("scan_text", {"text": csv, "region": "新北市"}, backend=StubBackend())
        self.assertEqual(r["computed_total"], 7035.0)
        self.assertEqual(r["total_check"], "skipped")        # 表上沒印合計 → 沒做比對，不能說通過
        self.assertEqual([x["value"] for x in r["rows"]], [1234.0, 2345.0, 3456.0])

    def test_plain_csv_still_works_and_total_pass_is_explicit(self):
        csv = "新北市 113年\n年齡,人口數\n18-24,1000\n25-29,2000\n30-35,3000\n合計,6000\n"
        r = _ai("scan_text", {"text": csv, "region": "新北市"}, backend=StubBackend())
        self.assertEqual(r["total_check"], "pass")
        self.assertEqual(r["computed_total"], 6000.0)


class TestF02Permille(unittest.TestCase):
    def test_fmt_returns_string_with_unit(self):
        self.assertEqual(_fmt({"value": 24.6, "unit": "‰"}), "24.6‰")

    def test_rounded_permille_matches_but_percent_does_not(self):
        rec = [_rec(24.6, "‰")]
        self.assertEqual(verify("生育率 25‰", rec)[1], [])
        self.assertEqual(verify("生育率 25%", rec)[1], ["25"])


class TestF03VerifyHoles(unittest.TestCase):
    def test_ranges_small_rates_and_signs_are_checked(self):
        rec = [_rec(-0.052, "%")]
        self.assertEqual(verify("預計青年增加 8888–9999 人", rec)[1], ["8888", "9999"])
        self.assertEqual(verify("青年淨移入率 0.9%", rec)[1], ["0.9"])
        self.assertEqual(verify("青年淨移入率 +5.2%", rec)[1], ["+5.2"])     # 帶號要對號
        self.assertEqual(verify("淨遷入率 -5.2%", rec)[1], [])
        self.assertEqual(verify("流出 5.2%", rec)[1], [])                    # 不帶號兩邊都可

    def test_age_ranges_citations_and_list_numbers_still_skipped(self):
        self.assertEqual(_numbers_in("18-35 歲，第 4 名，2024 年，[1-27]，#254"), [])
        self.assertEqual(_numbers_in("1. 第一點\n2. 第二點"), [])


class TestF06ForecastYear(unittest.TestCase):
    def test_forecast_is_for_cutoff_plus_one_after_merging_shock_years(self):
        pts = [(2019, .01), (2020, .02), (2021, .03), (2022, .04), (2023, .05)]
        c = classify(pts)
        self.assertEqual(c["forecast_year"], 2024)
        self.assertAlmostEqual(c["forecast"], 0.06, places=4)
        self.assertEqual(c["points"][-1][0], 2022.5)       # 合併後最後一個 x 不是預測基準


class TestF08F14ScanRobustness(unittest.TestCase):
    def test_rate_table_is_not_summed_or_aligned(self):
        r = _ai("scan_text", {"text": "新北市 113年 失業率（%）\n年齡,失業率\n18-24,8\n25-34,4\n", "region": "新北市"}, backend=StubBackend())
        self.assertEqual(r["kind"], "intensive")
        self.assertIsNone(r["computed_total"])
        self.assertEqual(r["total_check"], "not_applicable")
        self.assertEqual(r["aligned"], [])
        self.assertIn("分子", r["align_note"])

    def test_missing_metadata_returns_rows_instead_of_raising(self):
        r = _ai("scan_text", {"text": "年齡,人口數\n18-24,1000\n25-29,2000\n30-35,3000\n"}, backend=StubBackend())
        self.assertEqual(sorted(r["missing"]), ["region", "year"])
        self.assertEqual(len(r["rows"]), 3)
        self.assertEqual(r["aligned"], [])
        self.assertIn("補填", r["align_note"])


if __name__ == "__main__":
    unittest.main()
