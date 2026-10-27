"""Small OpenAI-compatible provider boundary; secrets are environment-only."""

import json
import os

import httpx


class ProviderError(RuntimeError):
    pass


class ChatProvider:
    def __init__(self, *, provider=None, key=None, base_url=None, model=None, transport=None):
        self.provider = (provider or os.getenv("FUTURELEDGER_PROVIDER", "local")).lower()
        if self.provider not in {"local", "deepseek", "openai", "compatible"}:
            raise ValueError("FUTURELEDGER_PROVIDER must be local, deepseek, openai or compatible")
        self.key = key or os.getenv("FUTURELEDGER_API_KEY") or (
            os.getenv("DEEPSEEK_API_KEY") if self.provider == "deepseek" else os.getenv("OPENAI_API_KEY")
        )
        defaults = {"deepseek": "https://api.deepseek.com", "openai": "https://api.openai.com/v1"}
        self.base_url = (base_url or os.getenv("FUTURELEDGER_API_BASE") or defaults.get(self.provider, "")).rstrip("/")
        self.model = model or os.getenv("FUTURELEDGER_MODEL")
        self.transport = transport
        if self.provider == "compatible" and not self.base_url:
            raise ValueError("compatible provider requires FUTURELEDGER_API_BASE")
        if self.provider != "local" and not self.model:
            raise ValueError("FUTURELEDGER_MODEL must be set to the model ID provided by the selected service")

    async def json_completion(self, system: str, user: str) -> dict:
        if self.provider == "local":
            raise ProviderError("local mode does not call a model")
        if not self.key:
            raise ProviderError("模型 API key 未配置")
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": 2048,
        }
        try:
            async with httpx.AsyncClient(timeout=20, transport=self.transport) as client:
                choice = None
                for attempt, token_limit in enumerate((2048, 4096)):
                    payload["max_tokens"] = token_limit
                    response = await client.post(
                        self.base_url + "/chat/completions",
                        json=payload,
                        headers={"Authorization": f"Bearer {self.key}"},
                    )
                    response.raise_for_status()
                    raw = response.json()
                    choice = raw["choices"][0]
                    finish_reason = choice.get("finish_reason")
                    if finish_reason == "stop":
                        break
                    if finish_reason == "length" and attempt == 0:
                        continue
                    if finish_reason == "length":
                        raise ProviderError("模型输出达到长度上限（已自动扩容重试）；请缩短问题或稍后重试。")
                    if finish_reason == "content_filter":
                        raise ProviderError("模型服务拦截了这次请求，请改写问题后重试。")
                    raise ProviderError(f"模型未正常结束生成（finish_reason={finish_reason or 'unknown'}）")
                content = choice["message"]["content"]
                if not isinstance(content, str) or not content.strip():
                    raise ProviderError("模型返回空内容")
                result = json.loads(content)
                if not isinstance(result, dict):
                    raise ProviderError("模型必须返回 JSON 对象")
                return result
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("模型服务不可用或返回了无效 JSON") from exc
