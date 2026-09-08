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
DEFAULT_MODEL = os.environ.get("YOUTHLENS_BEDROCK_MODEL", "anthropic.claude-opus-5")
DEFAULT_REGION = os.environ.get("YOUTHLENS_AWS_REGION", "us-east-1")

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

    def __init__(self, model: str = DEFAULT_MODEL, region: str = DEFAULT_REGION) -> None:
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


def load_backend(kind: str | None = None, **kwargs) -> Backend:
    """依環境變數 YOUTHLENS_LLM_BACKEND 選後端，預設 stub。

    比賽當天把它設成 bedrock 就切換過去，程式其他地方一行都不用改。
    """
    kind = kind or os.environ.get("YOUTHLENS_LLM_BACKEND", "stub")
    if kind == "bedrock":
        return BedrockBackend(**kwargs)
    if kind == "stub":
        return StubBackend(**kwargs)
    raise ValueError(f"不認識的後端 {kind!r}（可用：stub、bedrock）")


def list_available_models(region: str = DEFAULT_REGION) -> list[str]:
    """列出這個 AWS 帳號在該區域能用的 Bedrock 模型。

    **9/12 早上第一件事就跑這個。** Workshop 帳號有哪些模型、開在哪一區，
    賽前無法得知；與其猜，不如一跑就知道。需要 boto3（AWS 環境內建）。
    """
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("需要 boto3 才能列出模型：pip install boto3") from exc
    client = boto3.client("bedrock", region_name=region)
    return sorted(
        m["modelId"] for m in client.list_foundation_models().get("modelSummaries", [])
        if "anthropic" in m["modelId"]
    )


if __name__ == "__main__":
    print(f"區域 {DEFAULT_REGION}　預設模型 {DEFAULT_MODEL}\n")
    try:
        models = list_available_models()
    except Exception as exc:                      # noqa: BLE001
        print(f"無法列出模型：{exc}")
        print("（賽前正常 —— AWS 環境 9/12 08:00 才開通）")
    else:
        print(f"可用的 Anthropic 模型（{len(models)} 個）：")
        for m in models:
            print(f"  {m}")
