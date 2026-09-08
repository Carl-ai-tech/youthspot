"""掃描檔判讀的測試。全部離線，用假後端。

測的重點不是「AI 讀得對不對」—— 那我們控制不了。
測的是「**AI 讀錯的時候，我們抓不抓得到**」。

掃描檔最常見的錯誤是看錯一位數字（3 看成 8）。模型自己不會發現，
但把每一列加起來跟表上印的合計比對就會發現。這幾條測試守的就是那道查核。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.schema import MetricKind  # noqa: E402
from llm.backend import StubBackend  # noqa: E402
from llm.scan_table import (  # noqa: E402
    EXTRACT_PROMPT,
    parse_response,
    read_table,
    to_source_records,
)


def table_json(**overrides) -> str:
    payload = {
        "metric": "就業者人數",
        "unit": "千人",
        "region": "新北市",
        "year": 2025,
        "kind": "count",
        "rows": [
            {"age_label": "15-24", "value": 123},
            {"age_label": "25-44", "value": 1022},
            {"age_label": "45-64", "value": 868},
            {"age_label": "65歲以上", "value": 63},
        ],
        "printed_total": 2076,
        "unreadable": [],
        "notes": "",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


class TestParsing(unittest.TestCase):
    def test_reads_a_clean_table(self) -> None:
        t = parse_response(table_json())
        self.assertEqual(t.region, "新北市")
        self.assertEqual(t.year, 2025)
        self.assertIs(t.kind, MetricKind.EXTENSIVE)
        self.assertEqual(len(t.rows), 4)
        self.assertEqual(len(t.usable_rows), 4)
        self.assertTrue(t.trustworthy)
        self.assertEqual(t.issues, [])

    def test_handles_markdown_fence(self) -> None:
        """模型常把 JSON 包在 ```json 圍欄裡，不能因此就整個失敗。"""
        t = parse_response("```json\n" + table_json() + "\n```")
        self.assertEqual(len(t.rows), 4)

    def test_rejects_non_json(self) -> None:
        with self.assertRaises(ValueError):
            parse_response("我看不太清楚這張圖，你要不要換一張？")

    def test_rejects_empty_table(self) -> None:
        with self.assertRaises(ValueError):
            parse_response(table_json(rows=[]))


class TestChecks(unittest.TestCase):
    """三道查核，全部由我們的程式做，不問模型。"""

    def test_catches_a_misread_digit(self) -> None:
        """把 123 看成 823 —— 合計就對不上了。這是最重要的一條。"""
        rows = [
            {"age_label": "15-24", "value": 823},     # 應該是 123
            {"age_label": "25-44", "value": 1022},
            {"age_label": "45-64", "value": 868},
            {"age_label": "65歲以上", "value": 63},
        ]
        t = parse_response(table_json(rows=rows))
        self.assertFalse(t.trustworthy)
        self.assertTrue(any("合計對不上" in i for i in t.issues))

    def test_tolerates_rounding(self) -> None:
        """官方統計表本來就有四捨五入的個位數落差，不該被當成錯誤。"""
        rows = [
            {"age_label": "15-24", "value": 123},
            {"age_label": "25-44", "value": 1023},    # 四捨五入差 1
            {"age_label": "45-64", "value": 868},
            {"age_label": "65歲以上", "value": 63},
        ]
        t = parse_response(table_json(rows=rows))
        self.assertTrue(t.trustworthy, f"不該報錯：{t.issues}")

    def test_catches_overlapping_bands(self) -> None:
        """分組重疊代表同一群人被算兩次，通常是轉錄錯誤。"""
        rows = [
            {"age_label": "15-24", "value": 123},
            {"age_label": "20-44", "value": 1022},    # 與上一列重疊
        ]
        t = parse_response(table_json(rows=rows, printed_total=None))
        self.assertTrue(any("重疊" in i for i in t.issues))

    def test_flags_unparseable_age_label(self) -> None:
        rows = [
            {"age_label": "青壯年", "value": 500},
            {"age_label": "25-44", "value": 1022},
        ]
        t = parse_response(table_json(rows=rows, printed_total=None))
        self.assertTrue(any("無法解析" in i for i in t.issues))
        self.assertEqual(len(t.usable_rows), 1)      # 壞的那列不會混進去

    def test_rate_tables_are_not_summed(self) -> None:
        """比率型不能相加，所以不做合計查核 —— 加總失業率沒有意義。"""
        rows = [
            {"age_label": "15-24", "value": 11.6},
            {"age_label": "25-44", "value": 3.4},
        ]
        t = parse_response(table_json(rows=rows, kind="rate", unit="%", printed_total=None))
        self.assertIs(t.kind, MetricKind.INTENSIVE)
        self.assertIsNone(t.computed_total)
        self.assertTrue(t.trustworthy)

    def test_model_reported_blur_makes_it_untrustworthy(self) -> None:
        """模型說它看不清楚，就不能算全部通過 —— 誠實回報要有後果。"""
        t = parse_response(table_json(unreadable=["第二列的數字被摺痕蓋住"]))
        self.assertFalse(t.trustworthy)


class TestHandoffToEngine(unittest.TestCase):
    """AI 的工作到這裡結束，後面交給確定性的引擎。"""

    def test_builds_source_records(self) -> None:
        recs = to_source_records(parse_response(table_json()))
        self.assertEqual(len(recs), 4)
        self.assertEqual(recs[0].region, "新北市")
        self.assertIs(recs[0].kind, MetricKind.EXTENSIVE)
        self.assertIn("掃描檔", recs[0].source_agency)

    def test_drops_unparseable_rows(self) -> None:
        rows = [
            {"age_label": "社會新鮮人", "value": 500},
            {"age_label": "25-44", "value": 1022},
        ]
        recs = to_source_records(parse_response(table_json(rows=rows, printed_total=None)))
        self.assertEqual(len(recs), 1)

    def test_requires_region_and_year(self) -> None:
        """沒有地區年份就建不出可追溯的記錄，必須明確失敗。"""
        t = parse_response(table_json(region=None, year=None))
        with self.assertRaises(ValueError):
            to_source_records(t)
        recs = to_source_records(t, region="新北市", year=2025)   # 呼叫端補上就可以
        self.assertEqual(len(recs), 4)


class TestBackend(unittest.TestCase):
    def test_stub_drives_the_whole_path(self) -> None:
        backend = StubBackend({"統計表轉錄工具": table_json()})
        t = read_table("fake.png", backend)
        self.assertEqual(len(t.rows), 4)
        self.assertEqual(backend.calls[0][1], "fake.png")
        self.assertIn(EXTRACT_PROMPT[:20], backend.calls[0][0])

    def test_stub_fails_loudly_when_unconfigured(self) -> None:
        """假後端沒設定就要爆掉，不能讓測試在無聲中通過。"""
        with self.assertRaises(RuntimeError):
            StubBackend().complete("anything")


if __name__ == "__main__":
    unittest.main(verbosity=2)
