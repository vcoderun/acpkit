from __future__ import annotations as _annotations

from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

__all__ = ("CodexAuthConfig",)


def default_auth_path() -> Path:
    return Path.home() / ".codex" / "auth.json"


@dataclass(frozen=True, slots=True)
class CodexAuthConfig:
    auth_path: Path = field(default_factory=default_auth_path)
    api_base_url: str = "https://chatgpt.com/backend-api/codex"
    client_id: str = "app_EMoamEEZ73f0CkXaXp7hrann"
    default_token_ttl: timedelta = timedelta(hours=1)
    issuer: str = "https://auth.openai.com"
    refresh_margin: timedelta = timedelta(seconds=30)
    timeout_seconds: float = 30.0
