"""Tamper-evident audit trail — the governance system-of-record.

An enterprise governance tool is only as trustworthy as its audit log. This module
provides an append-only, HASH-CHAINED audit trail: each record embeds the hash of the
previous record, so any edit/reorder/deletion of history breaks the chain and is
detectable by `verify_audit_log`. The chain survives process restarts (it re-reads the
last record on init and continues the chain).

  mock/local : FileAuditObservability -> eval/audit_log.jsonl (this file)
  live       : OtelObservabilityAdapter -> Cloud Logging (immutable) + same chain

Verify from the CLI:
    python -m agents.audit                      # verify + tail eval/audit_log.jsonl
    python -m agents.audit --path <file> --tail 20
"""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from agents.ports.interfaces import ObservabilityPort

GENESIS = "0" * 64
DEFAULT_PATH = "eval/audit_log.jsonl"


def _chain_hash(prev: str, payload: dict[str, Any]) -> str:
    """Hash of (previous hash + canonical payload). Canonical = sorted-key JSON."""
    return hashlib.sha256((prev + json.dumps(payload, sort_keys=True)).encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class FileAuditObservability(ObservabilityPort):
    """Durable, tamper-evident observability. Audit records are appended as one JSON
    object per line (JSONL), each linked to the prior record by a hash chain.

    Reasoning spans are recorded as lightweight begin/end audit events so the
    reasoning chain is reconstructable from the trail alone.
    """

    def __init__(self, path: str = DEFAULT_PATH, trace_spans: bool = False) -> None:
        self.path = path
        self.trace_spans = trace_spans
        self.audit_log: list[dict[str, Any]] = []  # in-process view (same run)
        self._seq = 0
        self._prev = GENESIS
        self._resume()

    def _resume(self) -> None:
        """Continue the chain from the last persisted record (survives restarts)."""
        if not os.path.exists(self.path):
            return
        try:
            last = None
            with open(self.path) as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        last = json.loads(line)
            if last:
                self._seq = int(last.get("seq", 0))
                self._prev = str(last.get("hash", GENESIS))
        except (ValueError, OSError):
            pass  # corrupt tail — verify_audit_log will surface it; start fresh chain head

    @contextmanager
    def span(self, name: str, attrs: dict[str, Any] | None = None):
        if self.trace_spans:
            self.audit("trace", "span_begin", {"span": name, "attrs": attrs or {}})
        try:
            yield None
        finally:
            if self.trace_spans:
                self.audit("trace", "span_end", {"span": name})

    def audit(self, agent_id: str, action: str, detail: dict[str, Any]) -> None:
        self._seq += 1
        payload = {
            "seq": self._seq,
            "ts": _now(),
            "agent": agent_id,
            "action": action,
            "detail": detail,
            "prev": self._prev,
        }
        digest = _chain_hash(self._prev, payload)
        record = {**payload, "hash": digest}
        self._prev = digest
        self.audit_log.append(record)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a") as fh:
            fh.write(json.dumps(record) + "\n")


def verify_audit_log(path: str = DEFAULT_PATH) -> tuple[bool, str]:
    """Re-walk the chain; detect any tampering (edit/reorder/deletion).

    Returns (ok, message). A break names the first offending sequence number.
    """
    if not os.path.exists(path):
        return True, "no audit log yet (0 records)"
    prev = GENESIS
    count = 0
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                count += 1
                stored = rec.pop("hash", None)
                if rec.get("prev") != prev:
                    return False, f"chain break at seq {rec.get('seq')} (prev-hash mismatch)"
                if _chain_hash(prev, rec) != stored:
                    return False, f"tampered record at seq {rec.get('seq')} (content hash mismatch)"
                prev = stored
    except (ValueError, OSError) as exc:
        return False, f"unreadable audit log: {exc}"
    return True, f"{count} records verified — chain intact"


def _main() -> int:
    import argparse

    p = argparse.ArgumentParser(description="Verify / inspect the CloudCap audit trail")
    p.add_argument("--path", default=DEFAULT_PATH)
    p.add_argument("--tail", type=int, default=10, help="show the last N records")
    a = p.parse_args()

    ok, msg = verify_audit_log(a.path)
    print(f"Audit chain: {'OK ✓' if ok else 'FAILED ✗'} — {msg}")
    if os.path.exists(a.path) and a.tail:
        rows = [json.loads(x) for x in open(a.path) if x.strip()]
        print(f"\nLast {min(a.tail, len(rows))} of {len(rows)} records:")
        for r in rows[-a.tail:]:
            print(f"  #{r.get('seq'):<4} {r.get('ts')}  {r.get('agent'):14} {r.get('action')}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_main())
