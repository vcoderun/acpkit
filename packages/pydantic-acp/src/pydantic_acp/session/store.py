from __future__ import annotations as _annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from .state import AcpSessionContext, StoredSessionUpdate, utc_now

__all__ = ("FileSessionStore", "MemorySessionStore", "SessionStore")


class SessionStore(Protocol):
    def delete(self, session_id: str) -> None: ...

    def fork(
        self, session_id: str, *, new_session_id: str, cwd: Path
    ) -> AcpSessionContext | None: ...

    def get(self, session_id: str) -> AcpSessionContext | None: ...

    def list_sessions(self) -> list[AcpSessionContext]: ...

    def save(self, session: AcpSessionContext) -> None: ...


@dataclass(slots=True)
class MemorySessionStore:
    _sessions: dict[str, AcpSessionContext] = field(default_factory=dict)

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def fork(self, session_id: str, *, new_session_id: str, cwd: Path) -> AcpSessionContext | None:
        session = self.get(session_id)
        if session is None:
            return None

        forked_session = deepcopy(session)
        forked_session.session_id = new_session_id
        forked_session.cwd = cwd
        forked_session.created_at = utc_now()
        forked_session.updated_at = forked_session.created_at
        self.save(forked_session)
        return deepcopy(forked_session)

    def get(self, session_id: str) -> AcpSessionContext | None:
        session = self._sessions.get(session_id)
        return deepcopy(session) if session is not None else None

    def list_sessions(self) -> list[AcpSessionContext]:
        sessions = [deepcopy(session) for session in self._sessions.values()]
        return sorted(sessions, key=lambda session: session.updated_at, reverse=True)

    def save(self, session: AcpSessionContext) -> None:
        self._sessions[session.session_id] = deepcopy(session)


@dataclass(slots=True)
class FileSessionStore:
    root: Path

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def delete(self, session_id: str) -> None:
        path = self._session_path(session_id)
        if path.exists():
            path.unlink()

    def fork(self, session_id: str, *, new_session_id: str, cwd: Path) -> AcpSessionContext | None:
        session = self.get(session_id)
        if session is None:
            return None

        session.session_id = new_session_id
        session.cwd = cwd
        session.created_at = utc_now()
        session.updated_at = session.created_at
        self.save(session)
        return self.get(new_session_id)

    def get(self, session_id: str) -> AcpSessionContext | None:
        path = self._session_path(session_id)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return AcpSessionContext(
            session_id=payload["session_id"],
            cwd=Path(payload["cwd"]),
            created_at=self._parse_datetime(payload["created_at"]),
            updated_at=self._parse_datetime(payload["updated_at"]),
            title=payload["title"],
            session_model_id=payload["session_model_id"],
            message_history_json=payload["message_history_json"],
            config_values=payload["config_values"],
            metadata=payload["metadata"],
            transcript=[StoredSessionUpdate(**item) for item in payload["transcript"]],
        )

    def list_sessions(self) -> list[AcpSessionContext]:
        sessions: list[AcpSessionContext] = []
        for path in sorted(self.root.glob("*.json")):
            session = self.get(path.stem)
            if session is not None:
                sessions.append(session)
        return sorted(sessions, key=lambda session: session.updated_at, reverse=True)

    def save(self, session: AcpSessionContext) -> None:
        payload = {
            "config_values": session.config_values,
            "created_at": session.created_at.isoformat(),
            "cwd": str(session.cwd),
            "message_history_json": session.message_history_json,
            "metadata": session.metadata,
            "session_id": session.session_id,
            "session_model_id": session.session_model_id,
            "title": session.title,
            "transcript": [
                {
                    "kind": item.kind,
                    "payload": item.payload,
                }
                for item in session.transcript
            ],
            "updated_at": session.updated_at.isoformat(),
        }
        self._session_path(session.session_id).write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _parse_datetime(self, value: str) -> datetime:
        return datetime.fromisoformat(value)

    def _session_path(self, session_id: str) -> Path:
        return self.root / f"{session_id}.json"
