"""CSV／文字表格的自動辨識（llm/table_text.py）。—— 命題 2b「自動辨識定義衝突」＋ Spec §5.6

規則解析要認得政府表格常見的各種年齡寫法；認不得的文字標籤才交模型，
而且模型只能給區間與依據。查核（重疊、合計）對兩種來源一視同仁。
"""

from __future__ import annotations

import unittest

from llm import table_text as tt
from llm.backend import StubBackend

CSV = """新北市 113年 就業者人數（千人）
年齡,就業者人數
15～24歲,120
25～29歲,215
30～34歲,"302"
35歲以上,1439
合計,2076
"""


class TestRuleParsing(unittest.TestCase):

    def test_age_label_variants(self):
        for s in ("15-19", "15～24歲", "未滿25歲", "65歲以上", "25至29歲", "３０～３４歲", "24歲以下", "20 - 24"):
            self.assertTrue(tt.looks_like_age(s), s)
        for s in ("社會新鮮人", "合計", "就業者人數", "2024"):
            self.assertFalse(tt.looks_like_age(s), s)

    def test_csv_with_title_and_total(self):
        t, unparsed = tt.parse_table_text(CSV)
        self.assertEqual(unparsed, [])
        self.assertEqual(t.region, "新北市")
        self.assertEqual(t.year, 2024)                      # 113 年 → 2024
        self.assertEqual(t.metric, "就業者人數")
        self.assertEqual(t.unit, "千人")
        self.assertEqual(t.printed_total, 2076)
        self.assertEqual([r.age_label for r in t.rows], ["15-24", "25-29", "30-34", "35以上"])
        self.assertEqual([r.value for r in t.rows], [120, 215, 302, 1439])

    def test_tab_and_fullwidth(self):
        text = "年齡\t人數\n１５～１９歲\t1,234\n２０～２４歲\t2,345\n"
        t, _ = tt.parse_table_text(text)
        self.assertEqual([r.age_label for r in t.rows], ["15-19", "20-24"])
        self.assertEqual(t.rows[0].value, 1234)

    def test_rate_column_is_intensive(self):
        text = "年齡,勞動力參與率(%)\n15-24,35.2\n25-29,92.1\n"
        t, _ = tt.parse_table_text(text)
        self.assertEqual(t.unit, "%")
        self.assertIsNone(t.computed_total)                 # 比率型不可相加

    def test_picks_metric_column_by_header(self):
        text = "年齡,序號,失業人數,備註\n15-24,1,12,x\n25-29,2,9,y\n"
        t, _ = tt.parse_table_text(text)
        self.assertEqual(t.metric, "失業人數")
        self.assertEqual([r.value for r in t.rows], [12, 9])

    def test_no_numeric_column_raises(self):
        with self.assertRaises(ValueError):
            tt.parse_table_text("年齡,備註\n15-24,x\n")

    def test_total_mismatch_is_caught_after_validate(self):
        bad = CSV.replace("合計,2076", "合計,2500")
        t, _ = tt.read_table_text(bad)
        self.assertTrue(any("合計對不上" in i for i in t.issues))


class TestSemantic(unittest.TestCase):
    """§5.6：文字標籤 → 模型判讀區間，標推定；模型不碰數值。"""

    TEXT = "青年就業調查 2024 新北市\n分組\t人數\n應屆畢業生\t8000\n25-29歲\t9000\n總計\t17000\n"
    REPLY = ('{"labels": {"應屆畢業生": {"start": 22, "end": 24, '
             '"basis": "大學畢業 22 歲、碩士 24 歲"}}}')

    def test_only_unparsed_labels_go_to_model(self):
        stub = StubBackend({"年齡分組標籤": self.REPLY})
        t, inferred = tt.read_table_text(self.TEXT, stub)
        self.assertEqual(len(stub.calls), 1)
        self.assertIn("應屆畢業生", stub.calls[0][0])
        self.assertNotIn("25-29", stub.calls[0][0].split("標籤：")[1])
        self.assertEqual(inferred["應屆畢業生"]["applied_label"], "22-24")
        self.assertEqual(t.rows[0].band.label, "22-24")
        self.assertIn("推定", t.rows[0].age_label)           # 畫面上看得到原文＋推定
        self.assertEqual(t.rows[0].value, 8000)             # 數值一個字都沒動
        self.assertIn("推定", t.notes)

    def test_without_backend_unparsed_rows_become_issues(self):
        t, inferred = tt.read_table_text(self.TEXT)
        self.assertEqual(inferred, {})
        self.assertTrue(any("應屆畢業生" in i for i in t.issues))
        self.assertFalse(t.trustworthy)

    def test_model_garbage_does_not_crash(self):
        stub = StubBackend({"__default__": "我不知道"})
        t, inferred = tt.read_table_text(self.TEXT, stub)
        self.assertIsNone(inferred["應屆畢業生"]["start"])
        self.assertFalse(t.trustworthy)

    def test_overlap_from_inference_is_still_caught(self):
        text = "分組\t人數\n社會新鮮人\t100\n青年\t500\n"
        stub = StubBackend({"年齡分組標籤": '{"labels": {"社會新鮮人": {"start": 22, "end": 26, "basis": "b"}, '
                                          '"青年": {"start": 18, "end": 35, "basis": "青年基本法"}}}'})
        t, _ = tt.read_table_text(text, stub)
        self.assertTrue(any("重疊" in i for i in t.issues))


if __name__ == "__main__":
    unittest.main()
