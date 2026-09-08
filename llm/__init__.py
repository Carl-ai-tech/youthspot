"""AI 層 —— 唯一會呼叫語言模型的地方。

架構主張（被評審追問時的答案）：
    **AI 只做「看懂」，不做「計算」。**

    看懂掃描檔上的表格、看懂使用者的中文問題 —— 這是 AI 的工作。
    數字一律交給 engine/ 的確定性公式，AI 碰不到，所以它不可能算錯、
    也不可能編造數字。

這一層是**選用的**：沒有安裝 anthropic 套件、沒有 AWS 憑證時，
engine/ 和 data/ 完全照常運作。零第三方套件的承諾只在這一層被打破。
"""

from .backend import Backend, BedrockBackend, StubBackend, load_backend
from .scan_table import ScannedTable, read_table, to_source_records

__all__ = [
    "Backend",
    "BedrockBackend",
    "StubBackend",
    "load_backend",
    "ScannedTable",
    "read_table",
    "to_source_records",
]
