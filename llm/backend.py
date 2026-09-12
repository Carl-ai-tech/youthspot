"""模型後端，可抽換。

為什麼要抽換：AWS 競賽環境只在 9/12 08:00 – 9/13 13:00 開放，
賽前碰不到 Bedrock。所以介面先定好，用 StubBackend 把所有邏輯
（提示詞、JSON 解析、驗證、錯誤處理）在賽前全部測完，
當天只把後端換成 BedrockBackend —— 改設定，不改邏輯。

比賽規定「僅限使用 Amazon Bedrock、SageMaker AI 所提供之基礎模型」，
所以正式後端一定要走 Bedrock，不能直接呼叫 Anthropic API。
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Protocol

# 當天可能不是這個模型、不是這個區域 —— 全部做成環境變數，不寫死。
# 先跑 python -m llm.backend 會列出這個帳號實際可用的模型。
# ⚠️ 這些**不能**在模組層級讀 os.environ。模組匯入的時機比 _load_dotenv()
# 早，所以寫在 .env 裡的設定會被整個略過，而且是靜靜地略過 ——
# 你設了 haiku 卻跑出 opus，帳單和延遲都對不上，還很難查。
# 改成在建構子裡讀，下面這三個只是「什麼都沒設定時」的備援值。
FALLBACK_BEDROCK_MODEL = "anthropic.claude-opus-5"
FALLBACK_REGION = "us-east-1"
FALLBACK_DEV_MODEL = "claude-opus-5"


def _env(name: str, fallback: str) -> str:
    """建構當下才讀環境變數，確保 .env 已經載入。"""
    _load_dotenv()
    return os.environ.get(name) or fallback

MEDIA_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
}


class Backend(Protocol):
    """所有後端只需要做一件事：收提示詞（可帶一張圖），回一段文字。"""

    name: str

    def complete(self, prompt: str, image_path: str | Path | None = None) -> str: ...


class StubBackend:
    """賽前用的假後端。不連網、不花錢、結果固定，所以邏輯可以寫測試。

    回應以「提示詞裡出現的關鍵字」對應到預先準備的答案。
    測試時直接塞 responses；沒對到就丟錯，避免測試在無聲中通過。
    """

    name = "stub"

    def __init__(self, responses: dict[str, str] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, str | None]] = []

    def complete(self, prompt: str, image_path: str | Path | None = None) -> str:
        self.calls.append((prompt, str(image_path) if image_path else None))
        for key, value in self.responses.items():
            if key in prompt:
                return value
        if "__default__" in self.responses:
            return self.responses["__default__"]
        raise RuntimeError(
            "StubBackend 沒有對應的假回應。測試請明確提供，"
            "不要讓它靜靜回傳空字串而讓測試假通過。"
        )


class BedrockBackend:
    """正式後端：Amazon Bedrock 上的 Claude。

    需要 `pip install anthropic` 與 AWS 憑證（環境變數或 ~/.aws/credentials）。
    Bedrock 的模型 ID 要加 `anthropic.` 前綴。
    """

    name = "bedrock"

    def __init__(self, model: str | None = None, region: str | None = None) -> None:
        model = model or _env("YOUTHLENS_BEDROCK_MODEL", FALLBACK_BEDROCK_MODEL)
        region = region or _env("YOUTHLENS_AWS_REGION", FALLBACK_REGION)
        try:
            from anthropic import AnthropicBedrockMantle
        except ImportError as exc:
            raise RuntimeError(
                "缺少 anthropic 套件。這是唯一需要安裝東西的一層：\n"
                "    pip install anthropic\n"
                "engine/ 與 data/ 不需要它，沒裝也能完整跑。"
            ) from exc
        self.model = model
        self.region = region
        self._client = AnthropicBedrockMantle(aws_region=region)

    def complete(self, prompt: str, image_path: str | Path | None = None) -> str:
        content: list[dict] = []
        if image_path:
            path = Path(image_path)
            media = MEDIA_TYPES.get(path.suffix.lower())
            if media is None:
                raise ValueError(f"不支援的圖片格式：{path.suffix}（支援 {', '.join(MEDIA_TYPES)}）")
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media,
                    "data": base64.standard_b64encode(path.read_bytes()).decode("ascii"),
                },
            })
        content.append({"type": "text", "text": prompt})

        response = self._client.messages.create(
            model=self.model,
            max_tokens=8000,
            messages=[{"role": "user", "content": content}],
        )
        return "".join(b.text for b in response.content if b.type == "text")


class AnthropicBackend:
    """**賽前開發用**的直連後端。

    存在的理由：賽前所有 AI 邏輯都只跟 StubBackend 對過 ——
    假後端回的是我們自己寫好的答案，所以它驗證的是「解析與查核程式對不對」，
    完全沒有驗證「提示詞能不能讓真的模型回出那個格式」。
    那件事目前排在 9/12 早上第一次做，是整個專案最大的風險。

    這個後端讓提示詞可以在賽前就被真模型讀一次。跑得通，9/12 只剩
    「換成 Bedrock」這一件事要驗；跑不通，現在還有時間改提示詞。

    ⚠️ **比賽規定只能用 Bedrock／SageMaker 的模型，所以這個後端不能拿去交件。**
    它的定位是本機開發工具，正式路徑永遠是 BedrockBackend。
    兩者讀的是同一份提示詞、走同一個 complete() 介面，所以這裡調好的東西
    換過去仍然成立。

    需要 `pip install anthropic` 與 ANTHROPIC_API_KEY（或 `ant auth login`）。
    """

    name = "anthropic"

    def __init__(self, model: str | None = None) -> None:
        model = model or _env("YOUTHLENS_ANTHROPIC_MODEL", FALLBACK_DEV_MODEL)
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError(
                "缺少 anthropic 套件：\n"
                "    pip install anthropic\n"
                "（engine/ 與 data/ 不需要它，沒裝也能完整跑）"
            ) from exc
        # 不傳 api_key：SDK 自己會依序找 ANTHROPIC_API_KEY、ANTHROPIC_AUTH_TOKEN、
        # 以及 `ant auth login` 存下的設定檔。金鑰不進程式碼、不進版控。
        self.model = model
        self._client = anthropic.Anthropic()

    def complete(self, prompt: str, image_path: str | Path | None = None) -> str:
        content: list[dict] = []
        if image_path:
            path = Path(image_path)
            media = MEDIA_TYPES.get(path.suffix.lower())
            if media is None:
                raise ValueError(f"不支援的圖片格式：{path.suffix}（支援 {', '.join(MEDIA_TYPES)}）")
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media,
                    "data": base64.standard_b64encode(path.read_bytes()).decode("ascii"),
                },
            })
        content.append({"type": "text", "text": prompt})

        response = self._client.messages.create(
            model=self.model,
            max_tokens=8000,
            messages=[{"role": "user", "content": content}],
        )
        # 安全分類器可能擋下請求：那是 HTTP 200 + stop_reason="refusal"，
        # 不是例外。直接讀 content[0] 會在這種情況下爆掉或拿到空字串。
        if getattr(response, "stop_reason", None) == "refusal":
            raise RuntimeError(
                "模型拒絕了這個請求（stop_reason=refusal）。"
                "檢查提示詞裡有沒有會誤觸安全分類器的內容。"
            )
        return "".join(b.text for b in response.content if b.type == "text")


def _load_dotenv() -> None:
    """把專案根目錄的 `.env` 讀進環境變數。

    為什麼自己寫而不用 python-dotenv：這一層已經因為 anthropic 破過一次
    「零第三方套件」，不要為了讀十行設定檔再破第二次。

    兩條規則：
      1. **已經存在的環境變數優先。** 臨時想換一把金鑰時，
         直接 set 就好，不必回去改檔案 —— 這是 dotenv 的標準行為。
      2. 檔案不存在就安靜跳過。沒有 .env 是正常情況，不是錯誤。

    ⚠️ `.env` 已被 .gitignore 擋住。**不要 cat 它、不要投影它、不要貼進聊天視窗。**
    """
    path = Path(__file__).resolve().parent.parent / ".env"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        # 去掉包住值的引號，但不動值中間的引號
        value = value.strip().strip('"').strip("'")
        # 空值直接跳過。範本裡的 `ANTHROPIC_API_KEY=` 如果被設成空字串，
        # SDK 會拿到一把空金鑰，錯誤訊息會變成「金鑰無效」而不是「沒有金鑰」——
        # 後者一眼就知道要去填，前者會讓人跑去檢查金鑰對不對。
        if key and value and key not in os.environ:
            os.environ[key] = value


def load_backend(kind: str | None = None, **kwargs) -> Backend:
    """依環境變數 YOUTHLENS_LLM_BACKEND 選後端，預設 stub。

    stub      賽前跑測試用，不連網、結果固定
    anthropic 賽前開發用，直連 API 調提示詞（不能交件）
    bedrock   比賽當天的正式路徑

    比賽當天把它設成 bedrock 就切換過去，程式其他地方一行都不用改。
    """
    _load_dotenv()          # 先補上 .env，再讀環境變數決定用哪個後端
    kind = kind or os.environ.get("YOUTHLENS_LLM_BACKEND", "stub")
    if kind == "bedrock":
        return BedrockBackend(**kwargs)
    if kind == "anthropic":
        return AnthropicBackend(**kwargs)
    if kind == "stub":
        return StubBackend(**kwargs)
    raise ValueError(f"不認識的後端 {kind!r}（可用：stub、anthropic、bedrock）")


def list_available_models(region: str | None = None) -> list[str]:
    """列出這個 AWS 帳號在該區域能用的 Bedrock 模型。

    **9/12 早上第一件事就跑這個。** Workshop 帳號有哪些模型、開在哪一區，
    賽前無法得知；與其猜，不如一跑就知道。需要 boto3（AWS 環境內建）。
    """
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("需要 boto3 才能列出模型：pip install boto3") from exc
    region = region or _env("YOUTHLENS_AWS_REGION", FALLBACK_REGION)
    client = boto3.client("bedrock", region_name=region)
    return sorted(
        m["modelId"] for m in client.list_foundation_models().get("modelSummaries", [])
        if "anthropic" in m["modelId"]
    )


if __name__ == "__main__":
    print(f"區域 {_env('YOUTHLENS_AWS_REGION', FALLBACK_REGION)}　"
          f"預設模型 {_env('YOUTHLENS_BEDROCK_MODEL', FALLBACK_BEDROCK_MODEL)}\n")
    try:
        models = list_available_models()
    except Exception as exc:                      # noqa: BLE001
        print(f"無法列出模型：{exc}")
        print("（賽前正常 —— AWS 環境 9/12 08:00 才開通）")
    else:
        print(f"可用的 Anthropic 模型（{len(models)} 個）：")
        for m in models:
            print(f"  {m}")
