import unittest
from llm.synthesize import verify
class ReleaseChecks(unittest.TestCase):
    def test_percentage_points_need_same_unit_evidence(self):
        records=[{'value':8888,'unit':'人'}]
        self.assertEqual(verify('上升 8888 個百分點',records)[1],['8888'])
        self.assertEqual(verify('上升 0.3 個百分點',records,extra=['模型條件差異 +0.3 個百分點'])[1],[])
        self.assertEqual(verify('下降 -0.3 個百分點',records,extra=['模型條件差異 +0.3 個百分點'])[1],['-0.3'])
