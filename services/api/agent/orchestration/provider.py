"""Retry policy around the existing provider; no second HTTP/model client."""

import asyncio
import httpx

from demo.provider import ProviderError


class StructuredProvider:
    def __init__(self, provider, *, timeout=20, max_attempts=2):
        if not 1 <= max_attempts <= 2 or not 0 < timeout <= 30:
            raise ValueError('provider bounds exceeded')
        self.provider, self.timeout, self.max_attempts = provider, timeout, max_attempts

    async def json_completion(self, system, user):
        for attempt in range(self.max_attempts):
            try:
                return await asyncio.wait_for(self.provider.json_completion(system, user), self.timeout)
            except (ProviderError, httpx.HTTPError, asyncio.TimeoutError) as exc:
                cause = exc.__cause__ if isinstance(exc, ProviderError) else exc
                transient = isinstance(cause, (httpx.TimeoutException, httpx.NetworkError, asyncio.TimeoutError))
                if isinstance(cause, httpx.HTTPStatusError):
                    transient = cause.response.status_code == 429 or cause.response.status_code >= 500
                if not transient or attempt + 1 == self.max_attempts:
                    raise ProviderError('模型服务不可用或结构化输出无效') from None
