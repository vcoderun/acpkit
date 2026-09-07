from __future__ import annotations as _annotations

from typing import Literal

from openai import OpenAIError

__all__ = ("CodexAuthAccountMismatchError", "CodexAuthRefreshError")


class CodexAuthAccountMismatchError(OpenAIError):
    """Credentials changed to a different account during an active client lifetime."""


class CodexAuthRefreshError(OpenAIError):
    """Permanent refresh rejection without exposing the credential response body."""

    def __init__(self, reason: Literal["expired", "reused", "invalidated", "rejected"]) -> None:
        self.reason = reason
        super().__init__(f"Codex credentials could not be refreshed ({reason}); sign in again.")
