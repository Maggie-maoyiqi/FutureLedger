"""Authentication port: only trusted authentication code can create a Principal."""

import secrets
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Principal:
    subject: str
    account_ids: tuple[str, ...]
    dataset_ids: tuple[str, ...]


class Authenticator(Protocol):
    async def authenticate(self, bearer: str) -> Principal | None: ...


class DenyAuthentication:
    async def authenticate(self, bearer: str) -> Principal | None:
        return None


class DevelopmentTokens:
    """Explicit local/fake auth only. Never enabled by the default app."""
    def __init__(self, tokens: dict[str, Principal]):
        self.tokens = dict(tokens)

    async def authenticate(self, bearer: str) -> Principal | None:
        for token, principal in self.tokens.items():
            if secrets.compare_digest(token.encode(), bearer.encode()):
                return principal
        return None
