"""Pluggable state store — local JSON files (dev) or Firestore (deployed).

Every CloudCap config/state store (onboarding, sources, policy, freezes, findings
history, ...) is a single JSON document. This module is the one seam that decides WHERE
that document lives, so the store classes don't care:

  CLOUDCAP_STORE = local (default)  → eval/<name>.json      (byte-identical to before)
  CLOUDCAP_STORE = firestore        → Firestore doc `cloudcap/<name>`  (deployed)

The append-only AUDIT trail is NOT routed here — it goes to a file locally and to Cloud
Logging in live (see agents/adapters/google_geap.OtelObservabilityAdapter).

Usage from a store class (keeps its existing self.path):
    self.data = load_state(self.path, default={})
    save_state(self.path, self.data)
"""

from __future__ import annotations

import json
import os

_FIRESTORE_COLLECTION = "cloudcap"
_backend = None


def firestore_client():
    """Firestore client for the configured database. A NAMED database (CLOUDCAP_FIRESTORE_DB,
    e.g. 'cloudcap') avoids the '(default)' → '%28default%29' REST-encoding bug that breaks
    some client versions in slim containers. Shared by the state + session stores."""
    from google.cloud import firestore  # raises if lib absent → caller falls back
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    db = os.environ.get("CLOUDCAP_FIRESTORE_DB", "")
    if db:
        return firestore.Client(project=project, database=db)
    return firestore.Client(project=project) if project else firestore.Client()


def _doc_name(path: str) -> str:
    """Logical document id from a store path — the filename without extension.
    'eval/sources_state.json' -> 'sources_state'."""
    return os.path.splitext(os.path.basename(path))[0]


class _LocalBackend:
    """JSON files on disk — the dev/default backend. Preserves exact paths + format."""

    def load(self, path, default):
        if os.path.exists(path):
            try:
                return json.load(open(path))
            except (ValueError, OSError):
                return default
        return default

    def save(self, path, data):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as fh:
            json.dump(data, fh, indent=2)


class _FirestoreBackend:
    """Firestore documents — the deployed backend. Each store = one doc in `cloudcap`.
    Values are wrapped as {"_value": data} so lists/scalars persist as well as dicts."""

    def __init__(self):
        self.db = firestore_client()
        # Per-OP fallback (not a one-shot probe): a transient Firestore error on one call
        # must not permanently pin the process to local and lose all persisted state.
        self._local = _LocalBackend()

    def _ref(self, path):
        return self.db.collection(_FIRESTORE_COLLECTION).document(_doc_name(path))

    def load(self, path, default):
        try:
            snap = self._ref(path).get()
            return (snap.to_dict() or {}).get("_value", default) if snap.exists else default
        except Exception as exc:
            import sys
            print(f"[store] firestore load {_doc_name(path)} failed → local: {exc}", file=sys.stderr)
            return self._local.load(path, default)

    def save(self, path, data):
        try:
            self._ref(path).set({"_value": data})
        except Exception as exc:
            import sys
            print(f"[store] firestore save {_doc_name(path)} failed → local: {exc}", file=sys.stderr)
            self._local.save(path, data)


def backend():
    global _backend
    if _backend is None:
        if os.environ.get("CLOUDCAP_STORE", "").lower() == "firestore":
            try:
                _backend = _FirestoreBackend()   # per-op fallback lives inside it
                import sys
                print("[store] backend = firestore", file=sys.stderr)
            except Exception as exc:
                import sys
                print(f"[store] firestore init failed → local: {exc}", file=sys.stderr)
                _backend = _LocalBackend()
        else:
            _backend = _LocalBackend()
    return _backend


def load_state(path, default=None):
    return backend().load(path, {} if default is None else default)


def save_state(path, data):
    backend().save(path, data)
