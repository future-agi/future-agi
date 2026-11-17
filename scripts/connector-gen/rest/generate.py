#!/usr/bin/env python3
"""Generate (or --check) every rest provider's tools.generated.json from its
pinned vendor spec and curated tools.yaml. See scripts/connector-gen/README.md.

    python3 scripts/connector-gen/rest/generate.py [--check] [--app <app-id>]...

Needs PyYAML. Reads only files; never fetches a spec."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from restgen.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
