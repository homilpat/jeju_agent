"""SQLite storage for accounts, sessions and planners (PoC, single local file).

Passwords are stored only as scrypt hashes; session tokens only as SHA-256 digests,
so a leaked database file does not reveal usable credentials.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

# OWASP Password Storage Cheat Sheet: scrypt N=2^17, r=8, p=1
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**17, 8, 1
SCRYPT_MAXMEM = 256 * 1024 * 1024  # N*r*128 = 128 MiB needs more than hashlib's 32 MiB default
# shortcut: 근거 없는 세션 유지 기간 7일, PoC — 실서비스면 보안 정책으로 정하기
SESSION_DAYS = 7

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash BLOB NOT NULL,
    salt BLOB NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_digest TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS planners (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
        maxmem=SCRYPT_MAXMEM,
    )


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(SCHEMA)

    # accounts -------------------------------------------------------------
    def create_user(self, username: str, password: str) -> int | None:
        """Returns the new user id, or None when the username is taken."""
        salt = secrets.token_bytes(16)
        try:
            with self._db:
                cur = self._db.execute(
                    "INSERT INTO users (username, password_hash, salt, created_at)"
                    " VALUES (?,?,?,?)",
                    (username, _hash_password(password, salt), salt, _now().isoformat()),
                )
        except sqlite3.IntegrityError:
            return None
        return cur.lastrowid

    def verify_user(self, username: str, password: str) -> int | None:
        row = self._db.execute(
            "SELECT id, password_hash, salt FROM users WHERE username = ?", (username,)
        ).fetchone()
        if row is None:
            _hash_password(password, b"0" * 16)  # 없는 아이디도 같은 시간이 걸리게
            return None
        user_id, stored, salt = row
        return user_id if hmac.compare_digest(stored, _hash_password(password, salt)) else None

    def delete_user(self, user_id: int) -> None:
        with self._db:  # 세션·플래너는 ON DELETE CASCADE로 함께 지워진다
            self._db.execute("DELETE FROM users WHERE id = ?", (user_id,))

    # sessions -------------------------------------------------------------
    def create_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        expires = _now() + timedelta(days=SESSION_DAYS)
        with self._db:
            self._db.execute(
                "INSERT INTO sessions (token_digest, user_id, expires_at) VALUES (?,?,?)",
                (_digest(token), user_id, expires.isoformat()),
            )
        return token

    def session_user(self, token: str) -> int | None:
        row = self._db.execute(
            "SELECT user_id, expires_at FROM sessions WHERE token_digest = ?", (_digest(token),)
        ).fetchone()
        if row is None or datetime.fromisoformat(row[1]) <= _now():
            return None
        return row[0]

    def delete_session(self, token: str) -> None:
        with self._db:
            self._db.execute("DELETE FROM sessions WHERE token_digest = ?", (_digest(token),))

    def username(self, user_id: int) -> str | None:
        row = self._db.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
        return row[0] if row else None

    # planners -------------------------------------------------------------
    def list_planners(self, user_id: int) -> list[tuple[int, dict, str]]:
        rows = self._db.execute(
            "SELECT id, body, updated_at FROM planners WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
        return [(pid, json.loads(body), updated) for pid, body, updated in rows]

    def get_planner(self, user_id: int, planner_id: int) -> tuple[dict, str] | None:
        row = self._db.execute(
            "SELECT body, updated_at FROM planners WHERE id = ? AND user_id = ?",
            (planner_id, user_id),
        ).fetchone()
        return (json.loads(row[0]), row[1]) if row else None

    def save_planner(self, user_id: int, body: dict, planner_id: int | None = None) -> int | None:
        """Insert (planner_id None) or update one of the user's planners. None if not theirs."""
        now = _now().isoformat()
        text = json.dumps(body, ensure_ascii=False)
        with self._db:
            if planner_id is None:
                cur = self._db.execute(
                    "INSERT INTO planners (user_id, body, updated_at) VALUES (?,?,?)",
                    (user_id, text, now),
                )
                return cur.lastrowid
            cur = self._db.execute(
                "UPDATE planners SET body = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (text, now, planner_id, user_id),
            )
        return planner_id if cur.rowcount else None

    def delete_planner(self, user_id: int, planner_id: int) -> bool:
        with self._db:
            cur = self._db.execute(
                "DELETE FROM planners WHERE id = ? AND user_id = ?", (planner_id, user_id)
            )
        return bool(cur.rowcount)
