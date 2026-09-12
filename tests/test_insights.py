"""異常與洞察偵測的測試。—— Spec P1-3

重點不是「有沒有跑出卡片」，是**該安靜的時候要安靜**。
一個會把雜訊講成洞察的偵測器，比沒有偵測器更糟：
評審當場質疑一條站不住的「發現」，整個資料層的可信度就一起賠進去。

所以下面一半的測試在檢查「不該報的情況有沒有閉嘴」。
"""

from __future__ import annotations

import unittest

from data import insights


def _series(values, *, name="測試指標", kind="money", start=2010, youth=True, metric=None):
    """metric 預設跟著 name 走。要模擬「同一指標的不同年齡組」時要明確指定，
    因為合併規則就是靠 metric 判斷兩條序列是不是在講同一件事。"""
    years = list(range(start, start + len(values)))
    return insights.Series(name, kind, years, values, "測試來源", "測試區", youth,
                           metric=metric)


class TestDeviation(unittest.TestCase):
    """偵測器 1：最新值掉出先前趨勢的 95% 預測區間。"""

    def test_clean_line_has_no_deviation(self):
        """完美直線延續下去，不該報異常 —— 它完全符合趨勢。"""
        s = _series([10, 11, 12, 13, 14, 15])
        self.assertIsNone(insights._deviation(s))

    def test_last_point_jumping_off_trend_is_flagged(self):
        s = _series([10, 11, 12, 13, 14, 40])
        card = insights._deviation(s)
        self.assertIsNotNone(card)
        self.assertEqual(card["_sign"], 1)
        self.assertEqual(card["_year"], 2015)

    def test_drop_is_flagged_with_negative_sign(self):
        s = _series([10, 11, 12, 13, 14, -20])
        card = insights._deviation(s)
        self.assertIsNotNone(card)
        self.assertEqual(card["_sign"], -1)

    def test_too_few_points_stays_silent(self):
        """點數不足時寧可不報。四點扣掉一點只剩三點，區間寬到沒有意義。"""
        self.assertIsNone(insights._deviation(_series([10, 11, 12, 99])))

    def test_checked_point_is_excluded_from_the_fit(self):
        """被檢驗的那一年不能參與迴歸，否則等於自己驗自己。

        把它放進去會把離群值往迴歸線拉，異常就驗不出來 ——
        這是這個偵測器唯一會**靜默失效**的地方，所以鎖起來。
        """
        s = _series([10, 11, 12, 13, 14, 40])
        card = insights._deviation(s)
        # 用來外推的年度只到倒數第二年
        self.assertIn("2010–2014", card["detail"][2])
        self.assertIn("共 5 點", card["detail"][2])


class TestTrend(unittest.TestCase):
    """偵測器 3：整段斜率顯著不為零。"""

    def test_flat_series_reports_nothing(self):
        s = _series([10, 10.1, 9.9, 10, 10.05, 9.95])
        self.assertIsNone(insights._trend(s))

    def test_rising_series_is_reported(self):
        card = insights._trend(_series([10, 12, 14, 16, 18, 20]))
        self.assertIsNotNone(card)
        self.assertIn("上升", card["title"])

    def test_low_r2_is_downgraded_and_reworded(self):
        """顯著但解釋力差 → 不能說「持續」，也不能給 medium。

        用**真實的**新北市失業率（2006–2024，主計處開放平臺）當樣本，
        因為這條規則就是為它寫的：19 個點讓雜訊也能通過顯著性檢定，
        但 R² 只有 0.27 —— 趨勢只解釋了不到三成的變異。
        說「持續下降」是超譯，這是整個偵測器最容易講錯話的地方。
        """
        real_unemployment = [0.0383, 0.0378, 0.0409, 0.0589, 0.0523, 0.0439,
                             0.0425, 0.0416, 0.0394, 0.0376, 0.0395, 0.0378,
                             0.037, 0.0382, 0.0389, 0.0406, 0.0372, 0.0347, 0.0342]
        self.assertLess(_r2_of(real_unemployment), insights.R2_SOLID)

        card = insights._trend(_series(real_unemployment, kind="rate", start=2006))
        self.assertIsNotNone(card, "這條線整體是下降的，斜率顯著，應該要報")
        self.assertEqual(card["confidence"], "low")
        self.assertIn("波動大", card["title"])
        self.assertNotIn("持續", card["title"])

    def test_high_r2_keeps_the_stronger_wording(self):
        """對照組：解釋力夠就該講「持續」，不能一律降級。"""
        card = insights._trend(_series([10, 12, 14, 16, 18, 20]))
        self.assertIn("持續", card["title"])
        self.assertEqual(card["confidence"], "medium")


class TestDivergence(unittest.TestCase):
    """偵測器 4：組間成長率落差。"""

    def test_parallel_growth_is_not_divergence(self):
        """兩組**成長率**一樣，水準差很多 —— 那是年資，不是結構問題。"""
        a = _series([10, 10.5, 11, 11.5, 12, 12.5], name="青年")      # 每年 +4.0%
        b = _series([50, 52.5, 55, 57.5, 60, 62.5], name="中壯年")     # 每年 +4.0%
        self.assertIsNone(insights._divergence([a, b], "年薪"))

    def test_equal_absolute_raise_still_counts_as_divergence(self):
        """釘住設計選擇：比的是相對成長率，不是絕對金額。

        兩組都每年加 1 萬，但一組基期 15 萬、一組 55 萬 —— 對低薪那組
        是 +6.7%，對高薪那組只有 +1.8%。薪資成長本來就該用比例談，
        所以這**應該**報成落差。寫成測試是因為換個人來看很容易
        「順手改成比絕對值」，那會讓青年組的相對劣勢消失。
        """
        a = _series([10, 11, 12, 13, 14, 15], name="青年")
        b = _series([50, 51, 52, 53, 54, 55], name="中壯年")
        self.assertIsNotNone(insights._divergence([a, b], "年薪"))

    def test_gap_in_growth_rate_is_reported(self):
        fast = _series([10, 12, 14, 16, 18, 20], name="青年")
        slow = _series([50, 50.5, 51, 51.5, 52, 52.5], name="中壯年")
        card = insights._divergence([fast, slow], "年薪")
        self.assertIsNotNone(card)
        self.assertIn("青年", card["body"])

    def test_single_group_cannot_diverge(self):
        self.assertIsNone(insights._divergence([_series([10, 12, 14, 16])], "年薪"))


class TestMergeAndAssembly(unittest.TestCase):

    def test_same_year_same_direction_deviations_collapse(self):
        """三組同時同向偏離 = 一個現象，不是三個。"""
        cards = [insights._deviation(
            _series([10, 11, 12, 13, 14, 40], name=f"{i}0-{i}4 歲年薪", metric="年薪"))
            for i in range(3)]
        merged = insights._merge_deviations(cards)
        self.assertEqual(len(merged), 1)
        self.assertIn("3 個年齡組", merged[0]["title"])
        self.assertIn("年薪", merged[0]["title"], "標題要講出實際指標，不能寫死")

    def test_merging_three_series_raises_confidence(self):
        """獨立序列同向佐證才升級 —— 這是合併後才成立的證據。"""
        cards = [insights._deviation(
            _series([10, 11, 12, 13, 14, 40], name=f"{i}0-{i}4 歲年薪", metric="年薪"))
            for i in range(3)]
        self.assertTrue(all(c["confidence"] == "low" for c in cards))
        self.assertEqual(insights._merge_deviations(cards)[0]["confidence"], "medium")

    def test_two_series_do_not_raise_confidence(self):
        """兩條可能只是巧合，不夠格升級。"""
        cards = [insights._deviation(
            _series([10, 11, 12, 13, 14, 40], name=f"{i}0-{i}4 歲年薪", metric="年薪"))
            for i in range(2)]
        self.assertEqual(insights._merge_deviations(cards)[0]["confidence"], "low")

    def test_different_metrics_never_merge(self):
        """不同指標不准併成一個現象。

        實測踩過：失業率偏低與勞參率偏低被併成一張卡，還宣稱兩者有
        「共同外因」—— 那是兩件事，而且方向意義相反（失業率低是好事，
        勞參率低是壞事）。合併的前提是「在講同一件事」。
        """
        a = insights._deviation(_series([10, 11, 12, 13, 14, 40],
                                        name="25-29 歲失業率", metric="失業率"))
        b = insights._deviation(_series([10, 11, 12, 13, 14, 40],
                                        name="25-29 歲勞參率", metric="勞參率"))
        self.assertEqual(len(insights._merge_deviations([a, b])), 2,
                         "不同指標被錯誤合併了")

    def test_opposite_directions_do_not_collapse(self):
        up = insights._deviation(_series([10, 11, 12, 13, 14, 40], name="漲"))
        down = insights._deviation(_series([10, 11, 12, 13, 14, -20], name="跌"))
        self.assertEqual(len(insights._merge_deviations([up, down])), 2)

    def test_non_youth_series_never_becomes_a_trend_card(self):
        """40-49 歲薪資在成長不是青年洞察，是雜訊。

        但它仍然要能參與偏離合併與組間比較，所以只擋 trend／shift。
        """
        trends = {
            "salary": {
                "source": "測試",
                "years": [2019, 2020, 2021, 2022, 2023, 2024],
                "median": {"40-49": [50, 52, 54, 56, 58, 60]},
            }
        }
        titles = [c["title"] for c in insights.detect(trends)]
        self.assertFalse([t for t in titles if "40-49" in t and "持續" in t],
                         f"非青年組不該單獨產生趨勢卡：{titles}")

    def test_internal_fields_never_leak_to_output(self):
        """底線開頭的欄位是內部用的，洩到 unified.json 會被前端直接印出來。"""
        trends = {
            "salary": {
                "source": "測試",
                "years": [2019, 2020, 2021, 2022, 2023, 2024],
                "median": {"25-29": [10, 11, 12, 13, 14, 40]},
            }
        }
        for card in insights.detect(trends):
            leaked = [k for k in card if k.startswith("_") and k != "_kind"]
            self.assertEqual(leaked, [], f"內部欄位外洩：{leaked}")

    def test_empty_trends_produces_nothing_and_does_not_raise(self):
        self.assertEqual(insights.detect({}), [])
        self.assertEqual(insights.detect({"salary": {}, "labour": {}}), [])

    def test_output_is_capped(self):
        many = {"salary": {
            "source": "測試",
            "years": [2019, 2020, 2021, 2022, 2023, 2024],
            "median": {f"25-29": [10, 12, 14, 16, 18, 20]},
        }}
        self.assertLessEqual(len(insights.detect(many)), insights.MAX_CARDS)

    def test_every_card_carries_a_basis_and_confidence(self):
        """沒有依據的卡片不准出現 —— 整個專案的立場就是每個數字都查得到。"""
        trends = {
            "salary": {
                "source": "主計總處表6",
                "years": [2019, 2020, 2021, 2022, 2023, 2024],
                "median": {"25-29": [10, 12, 14, 16, 18, 20],
                           "未滿25": [8, 8.2, 8.4, 8.6, 8.8, 9.0]},
            }
        }
        cards = insights.detect(trends)
        self.assertTrue(cards)
        for c in cards:
            self.assertTrue(c["basis"], c)
            self.assertIn(c["confidence"], ("high", "medium", "low"))
            self.assertTrue(c["title"] and c["body"])


def _r2_of(values):
    from data import forecast
    tr = forecast.fit([(float(i), float(v)) for i, v in enumerate(values)])
    return tr.r2


if __name__ == "__main__":
    unittest.main()
