"""居住負擔資料（內政部不動產資訊平台）的測試。

這個來源跟其他的不一樣：不能自動抓、只讀人工下載的 CSV、而且來源本身有錯
（貸款負擔率三季整列是 100 倍）。所以測的重點是：
    1. 修正要修對、要留下記錄，而且**只修整列都錯的情況**
    2. 版面驗證要在欄位改動時炸掉，不能靜靜讀錯欄
    3. 「台」要統一成「臺」，否則六都比較對不上
    4. 洞察層看到的是年平均，不是季；而且不能把它講成青年
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from data import fetch_housing as fh
from data import insights


def _csv(rows: list[list], header=None) -> str:
    header = header or ["年度季別", "全國", "新北市", "台北市", "桃園市", "台中市", "台南市", "高雄市"]
    lines = [",".join(header)]
    for r in rows:
        lines.append(",".join(str(v) for v in r))
    return "\n".join(lines) + "\n"


def _quarters(n: int, start_year=91):
    """從 start_year Q1 開始連續 n 季，**新到舊**（跟內政部匯出一樣）。"""
    out = []
    y, q = start_year, 1
    for _ in range(n):
        out.append(f"{y:03d}Q{q}")
        q += 1
        if q == 5:
            y, q = y + 1, 1
    return list(reversed(out))


def _rows(n: int, base: float, step: float = 0.01):
    qs = _quarters(n)
    rows = []
    for i, q in enumerate(reversed(qs)):
        v = base + i * step
        rows.append([q] + [round(v + j * 0.1, 2) for j in range(7)])
    return list(reversed(rows))


class TestRepair(unittest.TestCase):

    def test_whole_row_x100_is_divided_and_logged(self):
        rows = [["111Q1", "5241.11", "5300.00", "6400.5", "4000", "4100", "3900", "4200"],
                ["110Q4", "52.41", "53.00", "64.00", "40.0", "41.0", "39.0", "42.0"]]
        fixes = fh._repair("貸款負擔率", rows)
        self.assertEqual([f["quarter"] for f in fixes], ["111Q1"])
        self.assertEqual(rows[0][1], "52.41")
        self.assertEqual(fixes[0]["action"], "÷100")
        self.assertAlmostEqual(fixes[0]["after_sample"], 53.0)

    def test_single_cell_outlier_is_left_alone(self):
        """單一格異常可能是真的離群值，不是單位錯誤 —— 不動。"""
        rows = [["111Q1", "52.41", "5300.00", "64.00", "40.0", "41.0", "39.0", "42.0"]]
        fixes = fh._repair("貸款負擔率", rows)
        self.assertEqual(fixes, [])
        self.assertEqual(rows[0][2], "5300.00")

    def test_price_income_ratio_is_never_repaired(self):
        rows = [["111Q1", "1200", "1300", "1400", "900", "1000", "800", "1100"]]
        self.assertEqual(fh._repair("房價所得比", rows), [])
        self.assertEqual(rows[0][1], "1200")


class TestVerify(unittest.TestCase):

    def test_column_order_change_raises(self):
        header = ["年度季別", "新北市", "全國", "臺北市", "桃園市", "臺中市", "臺南市", "高雄市"]
        with self.assertRaises(RuntimeError) as cm:
            fh._verify("房價所得比", header, _rows(50, 6.0))
        self.assertIn("欄位", str(cm.exception))

    def test_too_few_rows_raises(self):
        with self.assertRaises(RuntimeError):
            fh._verify("房價所得比", fh.EXPECTED_COLS, _rows(10, 6.0))

    def test_out_of_range_raises(self):
        rows = _rows(50, 6.0)
        rows[0][2] = "999"
        with self.assertRaises(RuntimeError):
            fh._verify("房價所得比", fh.EXPECTED_COLS, rows)

    def test_unrepaired_x100_burden_fails_verification(self):
        """修正是在驗證之前跑的；如果哪天修正被拿掉，驗證要接住它。"""
        rows = _rows(50, 50.0)
        rows[3] = [rows[3][0]] + ["5000"] * 7
        with self.assertRaises(RuntimeError):
            fh._verify("貸款負擔率", fh.EXPECTED_COLS, rows)


class TestFetch(unittest.TestCase):
    """整支跑一次：用暫存資料夾裝假 CSV，不碰真的 _cache。"""

    def _run(self, price_rows, burden_rows, encoding="cp950"):
        tmp = Path(tempfile.mkdtemp())
        files = {"房價所得比": tmp / "p.csv", "貸款負擔率": tmp / "b.csv"}
        files["房價所得比"].write_bytes(_csv(price_rows).encode(encoding))
        files["貸款負擔率"].write_bytes(_csv(burden_rows).encode(encoding))
        with mock.patch.object(fh, "FILES", files):
            return fh.fetch_housing()

    def test_missing_file_explains_manual_download(self):
        with mock.patch.object(fh, "FILES", {"房價所得比": Path("nope.csv"),
                                             "貸款負擔率": Path("nope2.csv")}):
            with self.assertRaises(FileNotFoundError) as cm:
                fh.fetch_housing()
        self.assertIn(fh.LANDING, str(cm.exception))

    def test_cities_normalised_and_sorted_old_to_new(self):
        h = self._run(_rows(48, 6.0), _rows(48, 40.0))
        self.assertIn("臺北市", h["latest"]["房價所得比"])
        self.assertNotIn("台北市", h["latest"]["房價所得比"])
        self.assertEqual(h["quarters"][0], "091Q1")
        self.assertEqual(h["quarters"][-1], "102Q4")
        self.assertEqual(h["latest_quarter"], "102Q4")
        self.assertEqual(h["years"][0], 2002.0)
        self.assertEqual(h["years"][1], 2002.25)
        # 舊到新：第一季的值要比最後一季小（測試資料是遞增的）
        s = h["series"]["房價所得比"]["新北市"]
        self.assertLess(s[0], s[-1])
        self.assertEqual(h["corrections"], {})

    def test_corrections_flow_through(self):
        burden = _rows(48, 40.0)
        burden[5] = [burden[5][0]] + [f"{float(v) * 100:.2f}" for v in burden[5][1:]]
        h = self._run(_rows(48, 6.0), burden)
        self.assertEqual([f["quarter"] for f in h["corrections"]["貸款負擔率"]],
                         [burden[5][0]])
        # 修完之後序列要平滑，不能留著 100 倍的尖峰
        s = h["series"]["貸款負擔率"]["新北市"]
        self.assertLess(max(s), 100)

    def test_misaligned_quarters_raise(self):
        with self.assertRaises(RuntimeError):
            self._run(_rows(48, 6.0), _rows(44, 40.0))

    def test_utf8_bom_is_accepted(self):
        h = self._run(_rows(48, 6.0), _rows(48, 40.0), encoding="utf-8-sig")
        self.assertEqual(len(h["quarters"]), 48)


class TestInsightsSeries(unittest.TestCase):
    """洞察層看到的居住序列：年平均、只含整年、標成家戶不是青年。"""

    def _trends(self, n_quarters=97):
        qs = list(reversed(_quarters(n_quarters)))
        ntpc = [6.0 + i * 0.07 for i in range(n_quarters)]
        burden = [40.0 + i * 0.16 for i in range(n_quarters)]
        return {"housing": {
            "source": "內政部", "quarters": qs,
            "series": {"房價所得比": {"新北市": ntpc}, "貸款負擔率": {"新北市": burden}},
        }}

    def test_annual_mean_drops_partial_years(self):
        years, vals = insights._annual_mean(["104Q1", "104Q2", "104Q3", "104Q4", "105Q1"],
                                            [1, 2, 3, 4, 100])
        self.assertEqual(years, [2015])
        self.assertEqual(vals, [2.5])

    def test_series_is_annual_and_labelled_household(self):
        allser, _ = insights._series_from_trends(self._trends(), "新北市")
        hou = [s for s in allser if s.chart == "housing"]
        self.assertEqual({s.metric for s in hou}, {"房價所得比", "貸款負擔率"})
        for s in hou:
            self.assertIn("全體家戶", s.scope)
            self.assertIn("非青年", s.scope)
            # 97 季 = 24 整年 + 1 季 → 24 點
            self.assertEqual(len(s.points), 24)
            self.assertEqual(int(s.points[0][0]), 2002)
        burden = next(s for s in hou if s.metric == "貸款負擔率")
        self.assertEqual(burden.kind, "rate")
        self.assertLess(burden.points[-1][1], 1.0)         # 已轉成小數

    def test_trend_card_says_household(self):
        cards = insights.detect(self._trends(), "新北市")
        hou = [c for c in cards if c.get("chart") == "housing"]
        self.assertEqual(len(hou), 2, "兩條單調上升 24 年的序列都該產生趨勢卡")
        for c in hou:
            self.assertIn("全體家戶", c["body"])
        ratio = next(c for c in hou if "房價所得比" in c["title"])
        self.assertIn("倍", ratio["body"])

    def test_housing_cards_survive_the_national_series(self):
        """真實資料：全國 48 年序列會產出六張以上的卡，居住的趨勢卡不能被它們擠掉。"""
        import json
        from pathlib import Path
        path = Path(__file__).resolve().parent.parent / "data" / "unified.json"
        trends = json.loads(path.read_text(encoding="utf-8"))["trends"]
        if "housing" not in trends:
            self.skipTest("unified.json 尚未接入居住資料")
        cards = insights.detect(trends, "新北市")
        charts = [c.get("chart") for c in cards]
        self.assertIn("housing", charts)
        self.assertLessEqual(max(charts.count(k) for k in set(charts)), insights.MAX_PER_CHART)
        self.assertLessEqual(len(cards), insights.MAX_CARDS)

    def test_no_housing_is_silent(self):
        allser, _ = insights._series_from_trends({}, "新北市")
        self.assertEqual([s for s in allser if s.chart == "housing"], [])


if __name__ == "__main__":
    unittest.main()
