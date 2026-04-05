from __future__ import annotations as _annotations

import json
from dataclasses import dataclass
from json import JSONDecodeError
from pathlib import Path

from .state import CodexAuthState

__all__ = ("CodexAuthStore",)


@dataclass(slots=True)
class CodexAuthStore:
    path: Path

    def read_state(self) -> CodexAuthState:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"Codex auth file was not found at `{self.path}`.") from exc

        try:
            raw = json.loads(text)
        except JSONDecodeError as exc:
            raise ValueError(
                f"Codex auth file at `{self.path}` does not contain valid JSON."
            ) from exc

        if not isinstance(raw, dict):
            raise ValueError(f"Codex auth file at `{self.path}` must contain a JSON object.")
        return CodexAuthState.from_json_dict(raw)

    def write_state(self, state: CodexAuthState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(state.to_json_dict(), indent=2) + "\n"
        self.path.write_text(encoded, encoding="utf-8")
