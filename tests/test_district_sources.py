"""行政區層級的兩個來源：財政部綜稅所得（村里統計）與主計總處工商普查（按行政區）。"""

from __future__ import annotations

import unittest

from data import fetch_district_income as fi
from data import fetch_district_jobs as fj


class TestIncome(unittest.TestCase):

    CSV = ('鄉鎮市區,村里,納稅單位(戶),綜合所得總額,平均數,中位數,第一分位數,第三分位數,標準差,變異係數\n'
           '新北市新莊區,海山里,1712,1343741,785,581,340,970,"737.46","93.96"\n'
           '新北市新莊區,合計,116602,92300325,792,572,351,965,"946.03","119.51"\n'
           '新北市其他,合計,100,50000,500,400,300,600,"1","1"\n'
           '台北市大安區,合計,5,5,5,5,5,5,"1","1"\n')

    def test_only_district_totals(self):
        d = fi._parse(self.CSV)
        self.assertEqual(list(d), ["新北市新莊區"])          # 村里列、「其他」、外縣市都不要
        self.assertEqual(d["新北市新莊區"]["median"], 572)
        self.assertEqual(d["新北市新莊區"]["units"], 116602)

    def test_verify_requires_29_districts(self):
        with self.assertRaises(RuntimeError):
            fi._verify(2023, fi._parse(self.CSV))

    def test_verify_range(self):
        d = {f"新北市{i}區": {"units": 10, "total": 1, "mean": 5000, "median": 5000, "q1": 1, "q3": 1} for i in range(29)}
        with self.assertRaises(RuntimeError):
            fi._verify(2023, d)

    def test_real_cache(self):
        from data.sources import CACHE_DIR
        if not (CACHE_DIR / "fia_income_112.csv").exists():
            self.skipTest("快取不在")
        d = fi.fetch_district_income()
        self.assertEqual(len(d["latest"]), 29)
        self.assertEqual(d["years"][0], 2019)                 # 108 年度起才進 trends
        self.assertIn("新北市林口區", d["latest"])
        self.assertGreater(d["latest"]["新北市林口區"]["median"], d["latest"]["新北市三芝區"]["median"])


class TestJobs(unittest.TestCase):

    def _xml(self, rows):
        body = ""
        for name, units, workers in rows:
            body += ("<r><項目別>" + name + "</項目別>"
                     "<年底場所單位數_家_總計_x_家數>" + str(units) + "</年底場所單位數_家_總計_x_家數>"
                     "<年底從業員工人數_人_x_人數>" + str(workers) + "</年底從業員工人數_人_x_人數></r>")
        return ("<root>" + body + "</root>").encode("utf-8")

    def test_parse_and_sum_check(self):
        rows = [("總　計", 29, 290)] + [(f"第{i}區", 1, 10) for i in range(29)]
        areas, total = fj._parse(self._xml(rows))
        self.assertEqual(total["workers"], 290)
        self.assertEqual(len(areas), 29)
        fj._verify(areas, total)                              # 29 區、加總對 → 過
        areas["新北市多出來"] = {"units": 1, "workers": 10}
        with self.assertRaises(RuntimeError):
            fj._verify(areas, total)                          # 30 區 → 炸

    def test_sum_mismatch_raises(self):
        rows = [("總計", 29, 999)] + [(f"第{i}區", 1, 10) for i in range(29)]
        areas, total = fj._parse(self._xml(rows))
        with self.assertRaises(RuntimeError):
            fj._verify(areas, total)

    def test_real_cache(self):
        from data.sources import CACHE_DIR
        if not (CACHE_DIR / "dgbas_census_110.xml").exists():
            self.skipTest("快取不在")
        d = fj.fetch_district_jobs()
        self.assertEqual(len(d["areas"]), 29)
        self.assertGreater(d["areas"]["新北市板橋區"]["workers"], d["areas"]["新北市平溪區"]["workers"])


if __name__ == "__main__":
    unittest.main()
