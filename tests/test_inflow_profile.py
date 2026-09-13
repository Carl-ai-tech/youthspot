"""參考標準必須是資料選的「移入區典型」，不是寫死的淡水：drivers.profiles、候選卡、問答提示都要用它。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.drivers import PROFILE_MIN_Y, PROFILE_MIN_YOUTH, inflow_profiles  # noqa: E402
from llm.advise import _drivers_block  # noqa: E402


def _load(name):
    return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))


class TestInflowProfile(unittest.TestCase):
    def test_members_follow_the_rule_and_taipei_falls_back_honestly(self):
        d = _load("drivers.json")
        rows = d["districts"]
        profiles = inflow_profiles(rows)
        for city, p in profiles.items():
            for m in p["members"]:
                r = next(x for x in rows if x["city"] == city and x["short"] == m)
                self.assertGreaterEqual(r["youth"], PROFILE_MIN_YOUTH)
                if not p["fallback"]:
                    self.assertGreaterEqual(r["y"], PROFILE_MIN_Y)
        self.assertIn("淡水區", profiles["新北市"]["members"])
        self.assertFalse(profiles["新北市"]["fallback"])
        self.assertTrue(profiles["臺北市"]["fallback"])           # 臺北沒有區達 +1%，要照實標
        self.assertIn("不足 2 個", profiles["臺北市"]["rule"])
        self.assertEqual(d["profiles"]["新北市"]["members"], profiles["新北市"]["members"])

    def test_candidate_card_and_prompt_use_the_profile_not_tamsui(self):
        u = _load("unified.json")
        card = next(n for n in u["policy_notes"] if "三年準備清單" in n["title"] and "候選" in n["title"] or "還沒起來" in n["title"])
        self.assertIn("參考標準是資料選的", card["body"])
        self.assertNotIn("跟淡水區最像", card["body"])
        lines, hint = _drivers_block(u, "新北市", "哪一區可能成為下一個青年聚集地？")
        joined = "\n".join(lines)
        self.assertIn("參考標準怎麼來的", joined)
        self.assertIn("移入區典型", hint)
        self.assertIn("不是跟淡水比", hint)
        # 典型的成員不能自己當候選；切入點要算在「還沒起來」的候選身上
        self.assertNotIn("施政切入點（淡水區", joined)
        self.assertNotIn("施政切入點（泰山區", joined)

    def test_fallback_rule_reaches_candidate_card_and_prompt(self):
        u = _load("unified_臺北市.json")
        rule = _load("drivers.json")["profiles"]["臺北市"]["rule"]
        card = next(n for n in u["policy_notes"] if "參考標準是資料選的" in n["body"])
        self.assertIn(rule, card["body"])
        _, hint = _drivers_block(u, "臺北市", "哪一區可能成為下一個青年聚集地？")
        self.assertIn(rule, hint)

    def test_one_qualifying_member_is_described_as_insufficient(self):
        rows = [dict(r) for r in _load("drivers.json")["districts"] if r["city"] == "臺北市"]
        for r in rows:
            r["y"] = 0.0
        rows[0]["y"] = 1.5
        rows[0]["youth"] = 6000
        p = inflow_profiles(rows)["臺北市"]
        self.assertTrue(p["fallback"])
        self.assertIn("不足 2 個", p["rule"])
        self.assertNotIn("沒有區", p["rule"])

    def test_explicit_district_still_works_as_reference(self):
        u = _load("unified.json")
        lines, hint = _drivers_block(u, "新北市", "三峽區條件像淡水區嗎？")
        self.assertTrue(any("新北市淡水區（近三年淨遷入" in l for l in lines))
        self.assertIn("主角是 三峽區", hint)


if __name__ == "__main__":
    unittest.main()
