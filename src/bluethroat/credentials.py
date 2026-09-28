"""Local GitHub session.

The operating-system credential store holds the session when it is available.
Otherwise the session is a mode ``0600`` file under the config directory.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Protocol

from bluethroat.config import config_dir

_SERVICE = "bluethroat"
_ACCOUNT = "default"


@dataclass
class Session:
    access_token: str
    refresh_token: str
    expires_at: float
    refresh_expires_at: float
    login: str
    user_id: int

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> Session | None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        try:
            return cls(
                access_token=str(data["access_token"]),
                refresh_token=str(data.get("refresh_token") or ""),
                expires_at=float(data.get("expires_at") or 0),
                refresh_expires_at=float(data.get("refresh_expires_at") or 0),
                login=str(data.get("login") or ""),
                user_id=int(data.get("user_id") or 0),
            )
        except (KeyError, TypeError, ValueError):
            return None


class SessionStore(Protocol):
    def load(self) -> Session | None: ...

    def save(self, session: Session) -> None: ...

    def clear(self) -> None: ...


class MemoryStore:
    """In-process store for tests."""

    def __init__(self) -> None:
        self.session: Session | None = None

    def load(self) -> Session | None:
        return self.session

    def save(self, session: Session) -> None:
        self.session = session

    def clear(self) -> None:
        self.session = None


class CredentialStore:
    def load(self) -> Session | None:
        raw = _keyring_get()
        if raw:
            session = Session.from_json(raw)
            if session is not None:
                return session
        return _file_load()

    def save(self, session: Session) -> None:
        encoded = session.to_json()
        if _keyring_set(encoded):
            _file_path().unlink(missing_ok=True)
            return
        _file_save(encoded)

    def clear(self) -> None:
        _keyring_delete()
        _file_path().unlink(missing_ok=True)


def _keyring_get() -> str | None:
    try:
        import keyring
    except ImportError:
        return None
    try:
        return keyring.get_password(_SERVICE, _ACCOUNT)
    except Exception:
        return None


def _keyring_set(encoded: str) -> bool:
    try:
        import keyring
    except ImportError:
        return False
    try:
        keyring.set_password(_SERVICE, _ACCOUNT, encoded)
    except Exception:
        return False
    return True


def _keyring_delete() -> None:
    try:
        import keyring
        keyring.delete_password(_SERVICE, _ACCOUNT)
    except Exception:
        return


def _file_path():
    return config_dir() / "credentials.json"


def _file_load() -> Session | None:
    try:
        raw = _file_path().read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    return Session.from_json(raw)


def _file_save(encoded: str) -> None:
    path = _file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, encoded.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
