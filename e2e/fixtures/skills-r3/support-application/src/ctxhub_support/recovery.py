"""Prepare an explicitly reviewed clean request without executing an application.

    python -m ctxhub_support.recovery REVIEW.json --output NEW-REQUEST.json

The review contains only schema=1, case and reconstruction_reviewed=true. No
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
    parser.add_argument("review", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    request = reset(json.loads(args.review.read_text()))
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
