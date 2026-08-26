"""Manage finding suppressions (accept / compliance exceptions).

    # accept a finding, suppress forever (permanent compliance exception)
    python -m agents.suppress add CC-1a2b3c4d --for forever --reason "PCI logging appliance" --by you@corp.com

    # suppress for a week / month / until a date
    python -m agents.suppress add CC-1a2b3c4d --for week   --reason "migration in progress"
    python -m agents.suppress add CC-1a2b3c4d --for 2026-12-31 --reason "audit window"

    python -m agents.suppress list
    python -m agents.suppress rm CC-1a2b3c4d
"""

from __future__ import annotations

import argparse
from datetime import date

from agents.suppressions import Suppression, SuppressionStore, parse_duration


def main() -> int:
    p = argparse.ArgumentParser(description="Manage CloudCap finding suppressions.")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="accept + suppress a finding by fingerprint")
    a.add_argument("fingerprint")
    a.add_argument("--for", dest="duration", default="forever",
                   help="forever | week | month | Nd | Nw | YYYY-MM-DD")
    a.add_argument("--reason", required=True)
    a.add_argument("--resource", default="")
    a.add_argument("--by", default="operator")

    sub.add_parser("list", help="list all suppressions")
    r = sub.add_parser("rm", help="remove a suppression")
    r.add_argument("fingerprint")

    args = p.parse_args()
    store = SuppressionStore()
    today = date.today()

    if args.cmd == "add":
        until = parse_duration(args.duration, today)
        store.add(Suppression(
            fingerprint=args.fingerprint, resource=args.resource, reason=args.reason,
            until=until, created_by=args.by, created_at=today.isoformat(),
        ))
        when = "forever" if until is None else f"until {until}"
        print(f"suppressed {args.fingerprint} {when} — {args.reason}")
    elif args.cmd == "rm":
        store.remove(args.fingerprint)
        print(f"removed suppression {args.fingerprint}")
    else:  # list
        rows = store.all()
        if not rows:
            print("(no suppressions)")
        for s in rows:
            state = "active" if s.active(today) else "EXPIRED"
            when = "forever" if s.until is None else f"until {s.until}"
            print(f"  {s.fingerprint}  [{state:7}] {when:18} {s.reason}  (by {s.created_by})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
