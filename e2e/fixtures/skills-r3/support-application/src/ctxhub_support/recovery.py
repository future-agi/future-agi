"""Prepare an explicitly reviewed clean request without executing an application.

    python -m ctxhub_support.recovery REVIEW.json --output NEW-REQUEST.json
    python -m ctxhub_support.recovery --case-id ID --order-id ID --request TEXT --output NEW-REQUEST.json

The review contains only schema=1, case and reconstruction_reviewed=true; the
three reconstruction flags are the same reviewed case typed on the command line
(all three, never mixed with a file). No
state path, provider, registry or business client is accepted. The operator must
reconstruct the task, review the new binding separately and run the application
explicitly. Existing messages, session IDs, locks and business receipts are never
copied, erased or replayed. Output creation is exclusive and private.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .entrypoint import reset
from .store import canonical


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Prepare a reviewed clean thread; do not run it"
    )
    parser.add_argument("review", type=Path, nargs="?")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-id")
    parser.add_argument("--order-id")
    parser.add_argument("--request")
    args = parser.parse_args(argv)
    flags = {
        "case_id": args.case_id,
        "order_id": args.order_id,
        "request": args.request,
    }
    typed = [name for name, value in flags.items() if value is not None]
    if args.review is not None:
        if typed:
            parser.error("give a review file or the reconstruction flags, not both")
        review = json.loads(args.review.read_text())
    elif len(typed) == len(flags):
        review = {"schema": 1, "case": flags, "reconstruction_reviewed": True}
    else:
        parser.error("give a review file or all of --case-id, --order-id and --request")
    request = reset(review)
    # O_EXCL also refuses symlinks. Never clobber a previous request or state DB.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(canonical(request) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    print(
        canonical(
            {
                "status": "prepared",
                "executed": False,
                "evidence_class": "synthetic fixture",
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
