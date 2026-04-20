from __future__ import annotations as _annotations

import json
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Protocol
from uuid import uuid4

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover
    _FCNTL: ModuleType | None = None
else:  # pragma: no cover
    _FCNTL = _fcntl

from .state import AcpSessionContext, StoredSessionUpdate, utc_now

__all__ = ("FileSessionStore", "MemorySessionStore", "SessionStore")

_STORE_LOCKS: dict[str, threading.RLock] = {}
_STORE_LOCKS_GUARD = threading.Lock()


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

        forked_session = _clone_session(session)
        forked_session.session_id = new_session_id
        forked_session.cwd = cwd
        forked_session.created_at = utc_now()
        forked_session.updated_at = forked_session.created_at
        self.save(forked_session)
        return _clone_session(forked_session)

    def get(self, session_id: str) -> AcpSessionContext | None:
        session = self._sessions.get(session_id)
        return _clone_session(session) if session is not None else None

    def list_sessions(self) -> list[AcpSessionContext]:
        sessions = [_clone_session(session) for session in self._sessions.values()]
        return sorted(sessions, key=lambda session: session.updated_at, reverse=True)

    def save(self, session: AcpSessionContext) -> None:
        self._sessions[session.session_id] = _clone_session(session)


@dataclass(slots=True)
class FileSessionStore:
    root: Path
    _process_lock: threading.RLock = field(init=False, repr=False)
    _lock_path: Path = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._process_lock = _store_lock(self.root)
        self._lock_path = self.root / ".acpkit-session-store.lock"
        self._cleanup_stale_temp_files()

    def delete(self, session_id: str) -> None:
        with self._locked():
            path = self._session_path(session_id)
            if path.exists():
                path.unlink()

    def fork(self, session_id: str, *, new_session_id: str, cwd: Path) -> AcpSessionContext | None:
        with self._locked():
            session = self._load_session_unlocked(session_id)
            if session is None:
                return None

            session.session_id = new_session_id
            session.cwd = cwd
            session.created_at = utc_now()
            session.updated_at = session.created_at
            self._save_unlocked(session)
            return self._load_session_unlocked(new_session_id)

    def get(self, session_id: str) -> AcpSessionContext | None:
        with self._locked():
            return self._load_session_unlocked(session_id)

    def list_sessions(self) -> list[AcpSessionContext]:
        with self._locked():
            sessions: list[AcpSessionContext] = []
            for path in sorted(self.root.glob("*.json")):
                session = self._load_session_unlocked(path.stem)
                if session is not None:
                    sessions.append(session)
            return sorted(sessions, key=lambda session: session.updated_at, reverse=True)

    def save(self, session: AcpSessionContext) -> None:
        with self._locked():
            self._save_unlocked(session)

    def _load_session_unlocked(self, session_id: str) -> AcpSessionContext | None:
        path = self._session_path(session_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return AcpSessionContext(
                session_id=payload["session_id"],
                cwd=Path(payload["cwd"]),
                created_at=self._parse_datetime(payload["created_at"]),
                updated_at=self._parse_datetime(payload["updated_at"]),
                title=payload["title"],
                session_model_id=payload["session_model_id"],
                session_mode_id=payload.get("session_mode_id"),
                plan_entries=payload.get("plan_entries", []),
                plan_markdown=payload.get("plan_markdown"),
                config_values=payload["config_values"],
                mcp_servers=payload.get("mcp_servers", []),
                metadata=payload["metadata"],
                transcript=[StoredSessionUpdate(**item) for item in payload["transcript"]],
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def _save_unlocked(self, session: AcpSessionContext) -> None:
        payload = {
            "config_values": session.config_values,
            "created_at": session.created_at.isoformat(),
            "cwd": str(session.cwd),
            "plan_entries": session.plan_entries,
            "plan_markdown": session.plan_markdown,
            "mcp_servers": session.mcp_servers,
            "metadata": session.metadata,
            "session_id": session.session_id,
            "session_model_id": session.session_model_id,
            "session_mode_id": session.session_mode_id,
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
        session_path = self._session_path(session.session_id)
        temp_path = self._temp_session_path(session.session_id)
        try:
            with temp_path.open("w", encoding="utf-8") as temp_file:
                temp_file.write(json.dumps(payload, indent=2, sort_keys=True))
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, session_path)
            self._fsync_directory()
        finally:
            temp_path.unlink(missing_ok=True)

    def _parse_datetime(self, value: str) -> datetime:
        return datetime.fromisoformat(value)

    def _session_path(self, session_id: str) -> Path:
        return self.root / f"{session_id}.json"

    def _temp_session_path(self, session_id: str) -> Path:
        return self.root / f".acpkit-session-{session_id}-{uuid4().hex}.tmp"

    def _cleanup_stale_temp_files(self) -> None:
        for path in self.root.glob(".acpkit-session-*.tmp"):
            path.unlink(missing_ok=True)

    def _fsync_directory(self) -> None:
        try:
            directory_fd = os.open(self.root, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(directory_fd)
        except OSError:
            return
        finally:
            os.close(directory_fd)

    @contextmanager
    def _locked(self):
        with self._process_lock:
            if _FCNTL is None:  # pragma: no cover
                yield
                return
            with self._lock_path.open("a+", encoding="utf-8") as lock_file:
                _FCNTL.flock(lock_file.fileno(), _FCNTL.LOCK_EX)
                try:
                    yield
                finally:
                    _FCNTL.flock(lock_file.fileno(), _FCNTL.LOCK_UN)


def _clone_session(session: AcpSessionContext) -> AcpSessionContext:
    return AcpSessionContext(
        session_id=session.session_id,
        cwd=session.cwd,
        created_at=session.created_at,
        updated_at=session.updated_at,
        title=session.title,
        session_model_id=session.session_model_id,
        session_mode_id=session.session_mode_id,
        plan_entries=json.loads(json.dumps(session.plan_entries)),
        plan_markdown=session.plan_markdown,
        config_values=json.loads(json.dumps(session.config_values)),
        mcp_servers=json.loads(json.dumps(session.mcp_servers)),
        metadata=json.loads(json.dumps(session.metadata)),
        transcript=[
            StoredSessionUpdate(kind=item.kind, payload=json.loads(json.dumps(item.payload)))
            for item in session.transcript
        ],
    )


def _store_lock(root: Path) -> threading.RLock:
    key = str(root.resolve())
    with _STORE_LOCKS_GUARD:
        lock = _STORE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _STORE_LOCKS[key] = lock
        return lock
