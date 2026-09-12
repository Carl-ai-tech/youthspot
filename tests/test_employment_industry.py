"""歷年就業者之行業（mp04025）與供需錯配的測試。"""

from __future__ import annotations

import unittest

from data import fetch_employment_industry as fi


def _xml(rows):
    body = ""
    for label, cells in rows:
        body += "<r><年月別_Year_and_month>" + label + "</年月別_Year_and_month>"
        for tag, v in cells.items():
            body += f"<{tag}>{v}</{tag}>"
        body += "</r>"
    return ("<root>" + body + "</root>").encode("utf-8")


def _cells(total=100, agri=10, ind=40, svc=50, mfg=30, mfg_m=20, mfg_f=10):
    return {
        "總計_Total_千人": total,
        "農林漁牧業_合計_Agri_Total_千人": agri,
        "工業_合計_Industry_Total_千人": ind,
        "服務業_合計_Services_Total_千人": svc,
        "工業_製造業_小計_Industry_Manufacturing_Total_千人": mfg,
        "工業_製造業_男_Industry_Manufacturing_Male_千人": mfg_m,
        "工業_製造業_女_Industry_Manufacturing_Female_千人": mfg_f,
        "工業_營建工程業_小計_Industry_Construction_Total_千人": ind - mfg,
        "服務業_教育業_小計_Services_Education_Total_千人": svc,
    }


class TestParseVerify(unittest.TestCase):

    def test_parse_industries_and_sectors(self):
        out = fi._parse(_xml([("６７年平均 Ave., 1978", _cells()), ("１２ 月 Dec.", _cells(999))]))
        self.assertEqual(list(out), [1978])
        self.assertEqual(out[1978]["製造業"], {"total": 30.0, "m": 20.0, "f": 10.0})
        self.assertEqual(out[1978]["_sector"], {"農林漁牧業": 10.0, "工業": 40.0, "服務業": 50.0})

    def test_sector_identity_is_checked(self):
        series = {y: fi._parse(_xml([("６７年平均 Ave., 1978", _cells(total=120))]))[1978] for y in range(1978, 2020)}
        with self.assertRaises(RuntimeError):
            fi._verify(series)

    def test_industry_sum_identity_is_checked(self):
        bad = _cells(); bad["服務業_教育業_小計_Services_Education_Total_千人"] = 5
        series = {y: fi._parse(_xml([("６７年平均 Ave., 1978", bad)]))[1978] for y in range(1978, 2020)}
        with self.assertRaises(RuntimeError):
            fi._verify(series)

    def test_sex_identity_is_checked(self):
        series = {y: fi._parse(_xml([("６７年平均 Ave., 1978", _cells(mfg_f=5))]))[1978] for y in range(1978, 2020)}
        with self.assertRaises(RuntimeError):
            fi._verify(series)


class TestRealCache(unittest.TestCase):

    def test_names_match_vacancy_table(self):
        from data.sources import CACHE_DIR
        if not (CACHE_DIR / "dgbas_mp04025.xml").exists():
            self.skipTest("快取不在")
        d = fi.fetch_employment_by_industry()
        for name in ("製造業", "教育業", "醫療保健及社會工作服務業", "住宿及餐飲業"):
            self.assertIn(name, d["industries"])
        self.assertGreaterEqual(len(d["years"]), 40)
        last = d["series"]["製造業"][-1]
        self.assertTrue(2000 < last < 4000)          # 千人：製造業約三百萬人


if __name__ == "__main__":
    unittest.main()
