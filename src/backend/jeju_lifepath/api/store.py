"""SQLite storage for accounts, sessions, planners and agent runs (PoC, single local file).

Passwords are stored only as scrypt hashes; session tokens only as SHA-256 digests,
so a leaked database file does not reveal usable credentials.

One connection is shared by the request threads, so every read and write runs under one lock
(AG-403). Schema changes are applied as numbered migrations (PRAGMA user_version) that keep
existing accounts and planners.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
from contextlib import contextmanager
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

# 순서대로 한 번씩 적용한다. 이미 적용된 단계는 건너뛴다(user_version).
TITLE_MAX = 40  # 대화 제목 글자 수(기본 제목도 첫 질문을 여기까지 자름)

MIGRATIONS = [
    # 1: 여행(플래너) 버전 + Agent 대화·실행·도구 호출 기록
    """
    ALTER TABLE planners ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
    CREATE TABLE conversations (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        state TEXT NOT NULL,
        revision INTEGER NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE agent_runs (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        run_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        status TEXT NOT NULL,
        stage TEXT NOT NULL,
        checkpoint TEXT NOT NULL,
        response TEXT,
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        committed INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id, run_id)
    );
    CREATE TABLE tool_invocations (
        invocation_id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        run_id TEXT NOT NULL,
        tool TEXT NOT NULL,
        attempt INTEGER NOT NULL,
        args_hash TEXT NOT NULL,
        args_masked TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        status TEXT NOT NULL,
        error_kind TEXT,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        duration_ms INTEGER,
        result_summary TEXT
    );
    CREATE INDEX tool_invocations_run ON tool_invocations(user_id, run_id);
    """,
    # 2: AI가 만든 일정은 초안(draft=1)으로 두고 사용자가 저장해야 내 여행 목록에 보인다.
    # draft=2: 원래 여행에 덮어써 다 쓴 초안(행을 남겨 번호가 새 초안에 다시 쓰이지 않게).
    # draft_of: 저장된 여행을 대화에서 고친 초안이면 원래 여행 번호
    """
    ALTER TABLE planners ADD COLUMN draft INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE planners ADD COLUMN draft_of INTEGER;
    """,
    # 3: draft_base: 초안을 만들 때 원래 여행 버전(덮어쓸 때 그사이 바뀌었는지 본다)
    """
    ALTER TABLE planners ADD COLUMN draft_base INTEGER;
    """,
]

# 실행이 끝나지 않은 상태. 서버가 다시 켜질 때 이 상태로 남은 실행은 중단된 것으로 본다
ACTIVE_STAGES = ("pending", "interpreting", "running_tool", "observing", "validating", "committing")


class VersionConflict(Exception):
    """기대한 여행 버전과 저장된 버전이 다름(다른 곳에서 먼저 고침)."""


class DraftOutdated(Exception):
    """덮어쓰려는 원래 여행이 초안을 만든 뒤 바뀌었음."""


class RunCancelled(Exception):
    """게시 직전에 보니 취소 요청이 와 있었음."""


def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        maxmem=SCRYPT_MAXMEM,
    )


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.RLock()
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(SCHEMA)
        self._migrate()
        self._mark_interrupted()

    def _migrate(self) -> None:
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        for target, script in enumerate(MIGRATIONS[version:], start=version + 1):
            # 한 단계(스키마 변경 + 버전 번호)를 한 트랜잭션으로: 중간에 꺼져도 반쯤 적용되지 않게
            self._db.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {target};\nCOMMIT;")

    def _mark_interrupted(self) -> None:
        """지난번 프로세스가 실행 도중 꺼졌으면 그 실행은 중단, 끝을 못 본 도구 호출은 불확실로 표시."""
        marks = ",".join("?" * len(ACTIVE_STAGES))
        with self._tx() as db:
            db.execute(
                f"UPDATE agent_runs SET stage = 'interrupted' WHERE stage IN ({marks})",
                ACTIVE_STAGES,
            )
            db.execute("UPDATE tool_invocations SET status = 'unknown' WHERE status = 'started'")

    @contextmanager
    def _tx(self):
        with self._lock, self._db:
            yield self._db

    def _rows(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    # accounts -------------------------------------------------------------
    def create_user(self, username: str, password: str) -> int | None:
        """Returns the new user id, or None when the username is taken."""
        salt = secrets.token_bytes(16)
        try:
            with self._tx() as db:
                cur = db.execute(
                    "INSERT INTO users (username, password_hash, salt, created_at)"
                    " VALUES (?,?,?,?)",
                    (username, _hash_password(password, salt), salt, _now().isoformat()),
                )
        except sqlite3.IntegrityError:
            return None
        return cur.lastrowid

    def verify_user(self, username: str, password: str) -> int | None:
        rows = self._rows(
            "SELECT id, password_hash, salt FROM users WHERE username = ?", (username,)
        )
        if not rows:
            _hash_password(password, b"0" * 16)  # 없는 아이디도 같은 시간이 걸리게
            return None
        user_id, stored, salt = rows[0]
        return user_id if hmac.compare_digest(stored, _hash_password(password, salt)) else None

    def delete_user(self, user_id: int) -> None:
        with self._tx() as db:  # 세션·플래너·대화 기록은 ON DELETE CASCADE로 함께 지워진다
            db.execute("DELETE FROM users WHERE id = ?", (user_id,))

    # sessions -------------------------------------------------------------
    def create_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        expires = _now() + timedelta(days=SESSION_DAYS)
        with self._tx() as db:
            db.execute(
                "INSERT INTO sessions (token_digest, user_id, expires_at) VALUES (?,?,?)",
                (_digest(token), user_id, expires.isoformat()),
            )
        return token

    def session_user(self, token: str) -> int | None:
        rows = self._rows(
            "SELECT user_id, expires_at FROM sessions WHERE token_digest = ?", (_digest(token),)
        )
        if not rows or datetime.fromisoformat(rows[0][1]) <= _now():
            return None
        return rows[0][0]

    def delete_session(self, token: str) -> None:
        with self._tx() as db:
            db.execute("DELETE FROM sessions WHERE token_digest = ?", (_digest(token),))

    def username(self, user_id: int) -> str | None:
        rows = self._rows("SELECT username FROM users WHERE id = ?", (user_id,))
        return rows[0][0] if rows else None

    # planners (= 여행) ------------------------------------------------------
    def list_planners(
        self, user_id: int, include_drafts: bool = False
    ) -> list[tuple[int, dict, str]]:
        rows = self._rows(
            "SELECT id, body, updated_at FROM planners WHERE user_id = ?"
            + ("" if include_drafts else " AND draft = 0")
            + " ORDER BY updated_at DESC",
            (user_id,),
        )
        return [(pid, json.loads(body), updated) for pid, body, updated in rows]

    def get_planner(self, user_id: int, planner_id: int) -> tuple[dict, str] | None:
        found = self.get_trip(user_id, planner_id)
        return (found[0], found[2]) if found else None

    def get_trip(self, user_id: int, planner_id: int) -> tuple[dict, int, str] | None:
        """(본문, 버전, 고친 시각). 다른 사용자의 여행이면 None."""
        rows = self._rows(
            "SELECT body, version, updated_at FROM planners"
            " WHERE id = ? AND user_id = ? AND draft != 2",
            (planner_id, user_id),
        )
        return (json.loads(rows[0][0]), rows[0][1], rows[0][2]) if rows else None

    def save_planner(self, user_id: int, body: dict, planner_id: int | None = None) -> int | None:
        """Insert (planner_id None) or update one of the user's planners. None if not theirs.
        고칠 때마다 버전이 1 오른다(Agent가 오래된 계산으로 덮어쓰지 못하게)."""
        now = _now().isoformat()
        with self._tx() as db:
            if planner_id is None:
                cur = db.execute(
                    "INSERT INTO planners (user_id, body, updated_at) VALUES (?,?,?)",
                    (user_id, _dumps(body), now),
                )
                return cur.lastrowid
            cur = db.execute(
                "UPDATE planners SET body = ?, updated_at = ?, version = version + 1"
                " WHERE id = ? AND user_id = ?",
                (_dumps(body), now, planner_id, user_id),
            )
        return planner_id if cur.rowcount else None

    def draft_info(self, user_id: int, planner_id: int) -> dict | None:
        """AI 초안이면 {"replaces": 원래 여행이 있는지}, 저장된 여행이면 None."""
        rows = self._rows(
            "SELECT draft, draft_of FROM planners WHERE id = ? AND user_id = ?",
            (planner_id, user_id),
        )
        if not rows or not rows[0][0]:
            return None
        return {"replaces": rows[0][1] is not None}

    def save_draft(self, user_id: int, draft_id: int, overwrite: bool) -> int | None:
        """초안을 내 여행에 넣는다. overwrite면 원래 여행 본문을 초안으로 바꾸고 초안은 다 쓴 것으로 표시.
        반환: 저장된 여행 번호. 초안이 아니거나(이미 저장) 남의 것이면 None.
        원래 여행이 초안을 만든 뒤 바뀌었으면 DraftOutdated(그사이 고친 내용을 덮지 않게)."""
        now = _now().isoformat()
        with self._tx() as db:
            row = db.execute(
                "SELECT body, draft_of, draft_base FROM planners"
                " WHERE id = ? AND user_id = ? AND draft = 1",
                (draft_id, user_id),
            ).fetchone()
            if row is None:
                return None
            body, original, base = row
            current = original is not None and db.execute(
                "SELECT version FROM planners WHERE id = ? AND user_id = ? AND draft = 0",
                (original, user_id),
            ).fetchone()
            if overwrite and current:
                if current[0] != base:
                    raise DraftOutdated(draft_id)
                db.execute(
                    "UPDATE planners SET body = ?, updated_at = ?, version = version + 1"
                    " WHERE id = ?",
                    (body, now, original),
                )
                db.execute("UPDATE planners SET draft = 2 WHERE id = ?", (draft_id,))
                return original
            # 새 여행으로(원래 여행이 그사이 지워졌어도 여기로)
            db.execute(
                "UPDATE planners SET draft = 0, draft_of = NULL, updated_at = ? WHERE id = ?",
                (now, draft_id),
            )
        return draft_id

    def delete_planner(self, user_id: int, planner_id: int) -> bool:
        with self._tx() as db:
            cur = db.execute(
                "DELETE FROM planners WHERE id = ? AND user_id = ?", (planner_id, user_id)
            )
        return bool(cur.rowcount)

    # agent: conversations -------------------------------------------------
    def get_conversation(self, user_id: int, conversation_id: str) -> tuple[dict, int] | None:
        rows = self._rows(
            "SELECT state, revision FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        )
        return (json.loads(rows[0][0]), rows[0][1]) if rows else None

    def list_conversations(self, user_id: int) -> list[dict]:
        """내 대화 목록(최근 것부터). 제목은 사용자가 붙인 제목, 없으면 첫 질문 앞부분(기본 제목)."""
        out = []
        for cid, state, updated in self._rows(
            "SELECT id, state, updated_at FROM conversations WHERE user_id = ?"
            " ORDER BY updated_at DESC",
            (user_id,),
        ):
            data = json.loads(state)
            first = next((h["text"] for h in data.get("history", []) if h.get("role") == "user"), "")
            if first:  # 아직 말이 없는 대화는 목록에 넣지 않음
                title = data.get("title")
                out.append({"id": cid, "title": title or first[:TITLE_MAX],
                            "custom": bool(title), "updated_at": updated})
        return out

    def set_conversation_title(self, user_id: int, conversation_id: str, title: str | None) -> bool:
        """대화 제목 바꾸기. None이면 붙인 제목을 지워 기본 제목(첫 질문)으로. 내 대화가 아니면 False.
        revision을 올려, 실행 중인 요청이 저장할 때 이 제목을 덮지 않고 합치게 한다."""
        with self._tx() as db:
            row = db.execute(
                "SELECT state, revision FROM conversations WHERE id = ? AND user_id = ?",
                (conversation_id, user_id),
            ).fetchone()
            if row is None:
                return False
            state = json.loads(row[0])
            if title:
                state["title"] = title
            else:
                state.pop("title", None)
            db.execute(
                "UPDATE conversations SET state = ?, revision = ? WHERE id = ?",
                (_dumps(state), row[1] + 1, conversation_id),
            )
        return True

    def delete_conversation(self, user_id: int, conversation_id: str) -> bool:
        """대화와 그 대화의 실행·도구 호출 기록을 지운다. 지운 것이 없으면(남의 대화 포함) False.
        첫 답이 오기 전이라 대화 행이 아직 없어도 실행 기록은 지운다(끝난 뒤 되살아나지 않게)."""
        with self._tx() as db:
            gone = db.execute(
                "DELETE FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user_id)
            ).rowcount
            db.execute(
                "DELETE FROM tool_invocations WHERE user_id = ? AND run_id IN"
                " (SELECT run_id FROM agent_runs WHERE user_id = ? AND conversation_id = ?)",
                (user_id, user_id, conversation_id),
            )
            gone += db.execute(
                "DELETE FROM agent_runs WHERE user_id = ? AND conversation_id = ?",
                (user_id, conversation_id),
            ).rowcount
        return bool(gone)

    def conversation_owner(self, conversation_id: str) -> int | None:
        rows = self._rows("SELECT user_id FROM conversations WHERE id = ?", (conversation_id,))
        return rows[0][0] if rows else None

    def save_conversation(
        self, user_id: int, conversation_id: str, state: dict, expected_revision: int = 0,
        run_id: str | None = None,
    ) -> bool:
        """대화 상태 저장. 저장된 revision이 expected_revision과 같을 때만 저장하고 True.
        다른 요청이 먼저 저장했으면 False. 다른 사용자의 대화 ID면 PermissionError.
        run_id: 대화를 새로 만들 때 이 실행 기록이 아직 있어야 만든다(그사이 사용자가 대화를 지웠으면
        실행 기록도 지워져 있다). 확인과 저장을 한 처리 안에서 해 지운 대화가 되살아나지 않게 한다."""
        now = _now().isoformat()
        with self._tx() as db:
            row = db.execute(
                "SELECT user_id, revision FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if row is not None and row[0] != user_id:
                raise PermissionError(conversation_id)
            current = row[1] if row else 0
            if current != expected_revision:
                return False
            if row is None and run_id and not db.execute(
                "SELECT 1 FROM agent_runs WHERE user_id = ? AND run_id = ?", (user_id, run_id)
            ).fetchone():
                return False
            if row is None:
                db.execute(
                    "INSERT INTO conversations (id, user_id, state, revision, updated_at)"
                    " VALUES (?,?,?,1,?)",
                    (conversation_id, user_id, _dumps(state), now),
                )
            else:
                db.execute(
                    "UPDATE conversations SET state = ?, revision = ?, updated_at = ? WHERE id = ?",
                    (_dumps(state), current + 1, now, conversation_id),
                )
        return True

    # agent: runs ----------------------------------------------------------
    def start_run(
        self, user_id: int, run_id: str, conversation_id: str, payload_hash: str, checkpoint: dict
    ) -> tuple[str, dict | None]:
        """같은 run_id(=메시지 ID)가 이미 있으면 새로 실행하지 않는다.
        반환: ("new", None) / ("duplicate", 저장된 응답 또는 None) / ("conflict", None)."""
        now = _now().isoformat()
        with self._tx() as db:
            row = db.execute(
                "SELECT payload_hash, response FROM agent_runs WHERE user_id = ? AND run_id = ?",
                (user_id, run_id),
            ).fetchone()
            if row is not None:
                if row[0] != payload_hash:
                    return "conflict", None
                return "duplicate", json.loads(row[1]) if row[1] else None
            db.execute(
                "INSERT INTO agent_runs (user_id, run_id, conversation_id, payload_hash, status,"
                " stage, checkpoint, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    user_id,
                    run_id,
                    conversation_id,
                    payload_hash,
                    "running",
                    "pending",
                    _dumps(checkpoint),
                    now,
                    now,
                ),
            )
        return "new", None

    def get_run(self, user_id: int, run_id: str) -> dict | None:
        rows = self._rows(
            "SELECT conversation_id, status, stage, checkpoint, response, cancel_requested,"
            " committed, created_at, updated_at FROM agent_runs WHERE user_id = ? AND run_id = ?",
            (user_id, run_id),
        )
        if not rows:
            return None
        conv, status, stage, cp, resp, cancel, committed, created, updated = rows[0]
        return {
            "conversation_id": conv,
            "status": status,
            "stage": stage,
            "checkpoint": json.loads(cp),
            "response": json.loads(resp) if resp else None,
            "cancel_requested": bool(cancel),
            "committed": bool(committed),
            "created_at": created,
            "updated_at": updated,
        }

    def update_run(
        self,
        user_id: int,
        run_id: str,
        *,
        stage: str | None = None,
        status: str | None = None,
        checkpoint: dict | None = None,
        response: dict | None = None,
    ) -> None:
        sets, args = ["updated_at = ?"], [_now().isoformat()]
        for col, val in (("stage", stage), ("status", status)):
            if val is not None:
                sets.append(f"{col} = ?")
                args.append(val)
        for col, val in (("checkpoint", checkpoint), ("response", response)):
            if val is not None:
                sets.append(f"{col} = ?")
                args.append(_dumps(val))
        with self._tx() as db:
            db.execute(
                f"UPDATE agent_runs SET {', '.join(sets)} WHERE user_id = ? AND run_id = ?",
                (*args, user_id, run_id),
            )

    def request_cancel(self, user_id: int, run_id: str) -> bool:
        with self._tx() as db:
            cur = db.execute(
                "UPDATE agent_runs SET cancel_requested = 1 WHERE user_id = ? AND run_id = ?",
                (user_id, run_id),
            )
        return bool(cur.rowcount)

    def cancel_requested(self, user_id: int, run_id: str) -> bool:
        rows = self._rows(
            "SELECT cancel_requested FROM agent_runs WHERE user_id = ? AND run_id = ?",
            (user_id, run_id),
        )
        return bool(rows and rows[0][0])

    def commit_trip(
        self,
        user_id: int,
        run_id: str,
        trip_id: int | None,
        expected_version: int | None,
        body: dict,
        checkpoint: dict,
    ) -> tuple[int, int]:
        """Agent 계산 결과를 여행에 게시한다. 한 트랜잭션에서
        (1) 취소·이미 게시 여부 확인 (2) 기대 버전일 때만 저장 (3) 실행에 게시 완료와 체크포인트 기록.
        반환 (trip_id, 새 버전). 버전이 다르면 VersionConflict, 취소됐으면 RunCancelled."""
        now = _now().isoformat()
        with self._tx() as db:
            row = db.execute(
                "SELECT cancel_requested, committed FROM agent_runs WHERE user_id = ? AND run_id = ?",
                (user_id, run_id),
            ).fetchone()
            if row is None or row[0]:
                raise RunCancelled(run_id)
            if row[1]:  # 이미 게시했다(재개 중 중복 게시 방지)
                raise VersionConflict(run_id)
            # AI가 만든 일정은 초안으로 둔다. 저장된 여행을 고치면 원래 여행은 그대로 두고 고친 초안을 따로 만든다
            saved = trip_id is not None and db.execute(
                "SELECT version FROM planners WHERE id = ? AND user_id = ? AND draft = 0",
                (trip_id, user_id),
            ).fetchone()
            if saved and saved[0] != expected_version:
                raise VersionConflict(run_id)
            if trip_id is None or saved:
                cur = db.execute(
                    "INSERT INTO planners (user_id, body, updated_at, draft, draft_of, draft_base)"
                    " VALUES (?,?,?,1,?,?)",
                    (user_id, _dumps(body), now, trip_id, saved[0] if saved else None),
                )
                trip_id, version = cur.lastrowid, 1
            else:
                cur = db.execute(
                    "UPDATE planners SET body = ?, updated_at = ?, version = version + 1"
                    " WHERE id = ? AND user_id = ? AND version = ?",
                    (_dumps(body), now, trip_id, user_id, expected_version),
                )
                if cur.rowcount == 0:
                    raise VersionConflict(run_id)
                version = expected_version + 1
            checkpoint = {**checkpoint, "committed_trip": [trip_id, version]}
            db.execute(
                "UPDATE agent_runs SET committed = 1, checkpoint = ?, updated_at = ?"
                " WHERE user_id = ? AND run_id = ?",
                (_dumps(checkpoint), now, user_id, run_id),
            )
        return trip_id, version

    # agent: tool invocations (trace) -------------------------------------
    def log_invocation_start(self, user_id: int, run_id: str, inv: dict) -> None:
        with self._tx() as db:
            db.execute(
                "INSERT INTO tool_invocations (invocation_id, user_id, run_id, tool, attempt,"
                " args_hash, args_masked, idempotency_key, status, started_at)"
                " VALUES (?,?,?,?,?,?,?,?,'started',?)",
                (
                    inv["invocation_id"],
                    user_id,
                    run_id,
                    inv["tool"],
                    inv["attempt"],
                    inv["args_hash"],
                    _dumps(inv["args_masked"]),
                    inv["idempotency_key"],
                    _now().isoformat(),
                ),
            )

    def log_invocation_end(
        self,
        invocation_id: str,
        status: str,
        error_kind: str | None,
        duration_ms: int,
        summary: dict,
    ) -> None:
        with self._tx() as db:
            db.execute(
                "UPDATE tool_invocations SET status = ?, error_kind = ?, ended_at = ?,"
                " duration_ms = ?, result_summary = ? WHERE invocation_id = ?",
                (
                    status,
                    error_kind,
                    _now().isoformat(),
                    duration_ms,
                    _dumps(summary),
                    invocation_id,
                ),
            )

    def invocations(self, user_id: int, run_id: str) -> list[dict]:
        rows = self._rows(
            "SELECT tool, attempt, args_masked, status, error_kind, started_at, duration_ms,"
            " result_summary FROM tool_invocations WHERE user_id = ? AND run_id = ?"
            " ORDER BY started_at, rowid",
            (user_id, run_id),
        )
        return [
            {
                "tool": t,
                "attempt": a,
                "args": json.loads(args),
                "status": s,
                "error_kind": e,
                "started_at": st,
                "duration_ms": d,
                "summary": json.loads(r) if r else None,
            }
            for t, a, args, s, e, st, d, r in rows
        ]

    def purge_older_than(self, days: int) -> None:
        """보유기간이 지난 실행 기록·도구 호출·대화를 지운다(AG-406, AG-605)."""
        cutoff = (_now() - timedelta(days=days)).isoformat()
        with self._tx() as db:
            db.execute("DELETE FROM tool_invocations WHERE started_at < ?", (cutoff,))
            db.execute("DELETE FROM agent_runs WHERE updated_at < ?", (cutoff,))
            db.execute("DELETE FROM conversations WHERE updated_at < ?", (cutoff,))
