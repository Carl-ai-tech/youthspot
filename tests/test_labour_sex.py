"""分齡 × 性別 序列（主計總處 mp04016 / mp04018 / mp04030）的測試。

重點：欄位對錯要炸、男＋女＝小計要守、官方填 "-" 的格子要變 None 而不是 0。
"""

from __future__ import annotations

import unittest

from data import fetch_labour_sex as fs

Band = tuple[int, int]


def _xml(rows: list[tuple[str, dict[str, str]]]) -> bytes:
    body = ""
    for label, cells in rows:
        body += "<r><年月別_Year_and_month>" + label + "</年月別_Year_and_month>"
        for tag, v in cells.items():
            body += f"<{tag}>{v}</{tag}>"
        body += "</r>"
    return ("<root>" + body + "</root>").encode("utf-8")


def _cells(total, m, f, band="25-29", group="25-44"):
    base = f"年齡{group}歲_{band}歲_"
    tail = f"_{group}_years_{band}_years_"
    return {
        base + "小計" + tail + "Total_千人": str(total),
        base + "男" + tail + "Male_千人": str(m),
        base + "女" + tail + "Female_千人": str(f),
    }


class TestParse(unittest.TestCase):

    def test_only_annual_average_rows(self):
        xml = _xml([("６７年平均 Ave., 1978", _cells(100, 60, 40)),
                    ("１２ 月 Dec.", _cells(999, 999, 999))])
        out = fs._parse(xml)
        self.assertEqual(list(out), [1978])
        self.assertEqual(out[1978][(25, 29)], {"total": 100.0, "m": 60.0, "f": 40.0})

    def test_dash_becomes_missing(self):
        cells = _cells(100, 60, 40)
        k = next(k for k in cells if "女" in k)
        cells[k] = "-"
        out = fs._parse(_xml([("６７年平均 Ave., 1978", cells)]))
        self.assertNotIn("f", out[1978][(25, 29)])

    def test_sum_check_catches_wrong_columns(self):
        series = {y: {(25, 29): {"total": 100.0, "m": 60.0, "f": 30.0}} for y in range(1978, 2020)}
        with self.assertRaises(RuntimeError):
            fs._verify("x", series)

    def test_sum_check_tolerates_rounding(self):
        series = {y: {(25, 29): {"total": 100.0, "m": 60.4, "f": 40.4}} for y in range(1978, 2020)}
        fs._verify("x", series)                       # 差 0.8 千人：四捨五入，放行

    def test_short_series_is_rejected(self):
        with self.assertRaises(RuntimeError):
            fs._verify("x", {1978: {(25, 29): {"total": 1, "m": 1, "f": 0}}})


class TestRealCache(unittest.TestCase):
    """真的快取在的話，整支跑一次；不在就跳過（不賭網路）。"""

    def test_rates_are_sane(self):
        from data.sources import CACHE_DIR
        if not all((CACHE_DIR / f"dgbas_{t}.xml").exists() for t, _ in fs.TABLES.values()):
            self.skipTest("快取不在")
        d = fs.fetch_labour_by_sex()
        self.assertGreaterEqual(len(d["years"]), 40)
        self.assertIn("30-34", d["bands"])
        f = [v for v in d["lfpr"]["f"]["30-34"] if v is not None]
        m = [v for v in d["lfpr"]["m"]["30-34"] if v is not None]
        self.assertTrue(all(0 < v < 1 for v in f + m))
        # 30-34 歲男性勞參率一直高於女性；女性長期上升
        self.assertGreater(m[-1], f[-1])
        self.assertGreater(f[-1], f[0])
        u = [v for v in d["unemployment"]["f"]["20-24"] if v is not None]
        self.assertTrue(all(0 <= v < 0.3 for v in u))


if __name__ == "__main__":
    unittest.main()
