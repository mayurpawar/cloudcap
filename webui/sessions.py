"""Server-side sessions — Firestore-backed on deploy, in-memory for local dev.

Cloud Run scales to zero and can run multiple instances, so an in-process session dict
loses logins on cold start / cross-instance routing. Firestore-backed sessions survive
both. Selected by CLOUDCAP_STORE=firestore (same signal as the state store); falls back
to in-memory if Firestore is unreachable so local dev is never blocked.

The session cookie holds only an opaque random id; all session data lives server-side,
so logout truly invalidates (delete the doc).
"""

from __future__ import annotations

import os
import secrets

_COLLECTION = "cloudcap_sessions"


class _MemorySessions:
    def __init__(self):
        self._s: dict[str, dict] = {}

    def create(self, data: dict) -> str:
        sid = secrets.token_urlsafe(24)
        self._s[sid] = dict(data)
        return sid

    def get(self, sid: str) -> dict | None:
        return self._s.get(sid)

    def save(self, sid: str, data: dict) -> None:
        self._s[sid] = dict(data)

    def delete(self, sid: str) -> None:
        self._s.pop(sid, None)


class _FirestoreSessions:
    def __init__(self):
        from agents.store import firestore_client
        self.db = firestore_client()
        self._mem = _MemorySessions()  # per-op fallback (no one-shot probe that pins memory)

    def _doc(self, sid: str):
        return self.db.collection(_COLLECTION).document(sid)

    def create(self, data: dict) -> str:
        sid = secrets.token_urlsafe(24)
        try:
            self._doc(sid).set(dict(data))
        except Exception:
            self._mem.save(sid, data)
        return sid

    def get(self, sid: str) -> dict | None:
        try:
            snap = self._doc(sid).get()
            if snap.exists:
                return snap.to_dict()
        except Exception:
            pass
        return self._mem.get(sid)

    def save(self, sid: str, data: dict) -> None:
        try:
            self._doc(sid).set(dict(data))
        except Exception:
            self._mem.save(sid, data)

    def delete(self, sid: str) -> None:
        try:
            self._doc(sid).delete()
        except Exception:
            pass
        self._mem.delete(sid)


def build_sessions():
    if os.environ.get("CLOUDCAP_STORE", "").lower() == "firestore":
        try:
            return _FirestoreSessions()
        except Exception:
            pass  # Firestore lib/creds/db missing → in-memory (local dev)
    return _MemorySessions()
