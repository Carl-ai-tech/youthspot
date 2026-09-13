"""後端選擇的測試。

不測「真的連得上模型」—— 那需要金鑰，放進測試就變成沒有金鑰的人跑不了。
這裡測的是**選錯後端時會不會講清楚**，以及三個後端都還在註冊表上。

會寫這支是因為 9/12 當天的切換動作就是改一個環境變數：
如果 `bedrock` 這個名字哪天被改掉而沒人發現，當天才會炸。
"""

from __future__ import annotations

import os
import pathlib
import unittest
from unittest.mock import patch, Mock

from llm import backend as B


class _NoLocalConfig:
    """暫時把 .env 與環境變數挪開，讓測試看到「什麼都沒設定」的狀態。

    沒有這層隔離，`load_backend()` 會讀到開發者自己的 .env ——
    那條測試就變成在測「這台機器怎麼設定」而不是「程式的預設是什麼」，
    而且會在別人的機器上莫名其妙地紅掉。
    """

    def __enter__(self):
        self.path = pathlib.Path(B.__file__).resolve().parent.parent / ".env"
        self.moved = self.path.with_suffix(".env.testbak")
        self.had_file = self.path.exists()
        if self.had_file:
            self.path.replace(self.moved)
        self.prev = os.environ.pop("YOUTHLENS_LLM_BACKEND", None)
        return self

    def __exit__(self, *exc):
        if self.had_file:
            self.moved.replace(self.path)
        if self.prev is not None:
            os.environ["YOUTHLENS_LLM_BACKEND"] = self.prev
        else:
            os.environ.pop("YOUTHLENS_LLM_BACKEND", None)
        return False


class TestLoadBackend(unittest.TestCase):

    def test_stub_is_the_default_when_nothing_is_configured(self):
        """什麼都沒設定時必須落在 stub —— 跑測試不該連網、不該花錢。"""
        with _NoLocalConfig():
            self.assertEqual(B.load_backend().name, "stub")

    def test_explicit_argument_beats_local_config(self):
        """明確指定的後端要贏過 .env 與環境變數。

        測試與腳本靠這條保證自己拿到的是想要的後端，
        不受開發者本機設定影響。
        """
        os.environ["YOUTHLENS_LLM_BACKEND"] = "anthropic"
        try:
            self.assertEqual(B.load_backend("stub").name, "stub")
        finally:
            os.environ.pop("YOUTHLENS_LLM_BACKEND", None)

    def test_all_three_kinds_are_registered(self):
        """三個後端都要認得。

        認不得會丟 ValueError；缺套件／缺金鑰是 RuntimeError。
        這裡只要求「名字有被註冊」，所以 RuntimeError 算通過。
        """
        with patch.object(B, "AnthropicBackend") as dev, patch.object(B, "BedrockBackend") as prod:
            self.assertEqual(B.load_backend("stub").name, "stub")
            self.assertIs(B.load_backend("anthropic"), dev.return_value)
            self.assertIs(B.load_backend("bedrock"), prod.return_value)

    def test_unknown_kind_lists_the_valid_ones(self):
        """錯誤訊息要把可用選項列出來，不要只說『不認識』。"""
        with self.assertRaises(ValueError) as ctx:
            B.load_backend("gpt")
        msg = str(ctx.exception)
        for kind in ("stub", "anthropic", "bedrock"):
            self.assertIn(kind, msg, f"錯誤訊息漏了 {kind}：{msg}")

    def test_missing_package_says_how_to_fix_it(self):
        """缺套件時要給指令，不要丟 ImportError 讓人自己猜。"""
        import builtins
        original = builtins.__import__
        def missing(name, *args, **kwargs):
            if name in {"anthropic", "boto3"}:
                raise ImportError("package unavailable")
            return original(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=missing), patch.object(B, "_load_dotenv"):
            for kind in ("anthropic", "bedrock"):
                with self.assertRaisesRegex(RuntimeError, "pip install"):
                    B.load_backend(kind)

    def test_dev_backend_is_not_the_competition_path(self):
        """釘住一件容易忘的事：直連後端不能拿去交件。

        比賽規定只能用 Bedrock／SageMaker 的模型。這條測試本身擋不住誤用，
        但它讓「為什麼有兩個真後端」這件事在程式碼裡留下記錄。
        """
        self.assertEqual(B.AnthropicBackend.name, "anthropic")
        self.assertEqual(B.BedrockBackend.name, "bedrock")
        self.assertIn("不能拿去交件", B.AnthropicBackend.__doc__)


class TestSystemTransport(unittest.TestCase):
    def test_anthropic_system_is_separate_and_optional(self):
        backend = object.__new__(B.AnthropicBackend)
        backend.model = "test-model"
        backend._client = Mock()
        backend._client.messages.create.return_value = Mock(
            stop_reason="end_turn", content=[Mock(type="text", text="answer")])
        self.assertEqual(backend.complete("question", system="rules"), "answer")
        body = backend._client.messages.create.call_args.kwargs
        self.assertEqual(body["system"], "rules")
        self.assertEqual(body["messages"], [{"role": "user", "content": [{"type": "text", "text": "question"}]}])
        backend.complete("legacy question")
        self.assertNotIn("system", backend._client.messages.create.call_args.kwargs)


class TestModelSelection(unittest.TestCase):
    """模型／區域必須在**建構當下**讀，不是模組匯入時讀。

    踩過一次：常數寫成 `os.environ.get(...)` 放在模組層級，
    而 .env 是在 load_backend() 裡才載入 —— 匯入早於載入，
    所以 .env 設的模型被靜靜略過。設了 haiku 卻跑 opus，
    帳單和延遲都對不上，而且看程式碼完全看不出哪裡錯。

    9/12 換 Bedrock 模型靠的就是這個機制，所以鎖起來。
    """

    def tearDown(self):
        for k in ("YOUTHLENS_ANTHROPIC_MODEL", "YOUTHLENS_BEDROCK_MODEL",
                  "YOUTHLENS_AWS_REGION"):
            os.environ.pop(k, None)

    def test_env_var_is_read_at_construction_not_import(self):
        # 模組早就匯入了；現在才設環境變數，仍然必須生效
        os.environ["YOUTHLENS_ANTHROPIC_MODEL"] = "claude-haiku-4-5"
        self.assertEqual(
            B._env("YOUTHLENS_ANTHROPIC_MODEL", B.FALLBACK_DEV_MODEL),
            "claude-haiku-4-5")

    def test_bedrock_model_and_region_too(self):
        """9/12 當天就是靠這兩個環境變數換模型與區域。"""
        os.environ["YOUTHLENS_BEDROCK_MODEL"] = "anthropic.claude-sonnet-5"
        os.environ["YOUTHLENS_AWS_REGION"] = "ap-northeast-1"
        self.assertEqual(
            B._env("YOUTHLENS_BEDROCK_MODEL", B.FALLBACK_BEDROCK_MODEL),
            "anthropic.claude-sonnet-5")
        self.assertEqual(
            B._env("YOUTHLENS_AWS_REGION", B.FALLBACK_REGION), "ap-northeast-1")

    def test_falls_back_when_unset(self):
        self.assertEqual(
            B._env("YOUTHLENS_BEDROCK_MODEL", B.FALLBACK_BEDROCK_MODEL),
            B.FALLBACK_BEDROCK_MODEL)


class TestDotEnv(unittest.TestCase):
    """`.env` 讀取。金鑰放這裡最方便，但行為錯了會很難查。"""

    def setUp(self):
        self.path = pathlib.Path(B.__file__).resolve().parent.parent / ".env"
        self.existed = self.path.exists()
        self.backup = self.path.read_text(encoding="utf-8") if self.existed else None
        for k in ("YL_T_A", "YL_T_B", "YL_T_C"):
            os.environ.pop(k, None)

    def tearDown(self):
        if self.existed:
            self.path.write_text(self.backup, encoding="utf-8")
        elif self.path.exists():
            self.path.unlink()
        for k in ("YL_T_A", "YL_T_B", "YL_T_C"):
            os.environ.pop(k, None)

    def test_reads_values_and_strips_quotes(self):
        self.path.write_text('# 註解\nYL_T_A=plain\nYL_T_B="quoted"\n', encoding="utf-8")
        B._load_dotenv()
        self.assertEqual(os.environ.get("YL_T_A"), "plain")
        self.assertEqual(os.environ.get("YL_T_B"), "quoted")

    def test_existing_env_var_wins(self):
        """想臨時換一把金鑰時直接 set 就好，不必回去改檔案。

        反過來（檔案蓋掉環境變數）會讓「我明明 set 了怎麼沒生效」變成
        非常難查的問題 —— 因為兩邊都看起來是對的。
        """
        self.path.write_text("YL_T_C=from_file\n", encoding="utf-8")
        os.environ["YL_T_C"] = "from_shell"
        B._load_dotenv()
        self.assertEqual(os.environ.get("YL_T_C"), "from_shell")

    def test_missing_file_is_not_an_error(self):
        """沒有 .env 是正常情況 —— 用環境變數的人就不會有這個檔。"""
        if self.path.exists():
            self.path.unlink()
        B._load_dotenv()          # 不該丟例外

    def test_env_is_gitignored(self):
        """釘住這條：.env 一旦進版控，金鑰就跟著上 GitHub 了。"""
        gitignore = (pathlib.Path(B.__file__).resolve().parent.parent
                     / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".env", gitignore.split())


if __name__ == "__main__":
    unittest.main()
