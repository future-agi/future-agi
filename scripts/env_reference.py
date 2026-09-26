#!/usr/bin/env python3
"""Inventory of the environment variables the compose files read.

Prints every ${VAR} in the user-facing compose files with the default each
file gives it, and whether docs/configuration.md documents it. Use it when a
compose change makes futureagi/tests/test_env_reference.py fail, to see what
to add to the reference.

    python3 scripts/env_reference.py            # full inventory
    python3 scripts/env_reference.py --missing  # only undocumented keys
    python3 scripts/env_reference.py --check    # exit 1 if any is undocumented

Standard library only.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "configuration.md"
COMPOSE_FILES = {
    "S": "docker-compose.yml",
    "D": "docker-compose.distributed.yml",
    "S-dev": "docker-compose.dev.yml",
    "D-dev": "docker-compose.distributed.dev.yml",
    "prod": "deploy/docker-compose.production.yml",
    "ui-only": "docker-compose.frontend.yml",
}
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
DOC_KEY = re.compile(r"`([A-Z][A-Z0-9_]*)`")


def strip_comments(text: str) -> str:
    """Drop YAML comments: whole-line ones and ' #' outside quotes."""
    lines = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        quote = None
        for i, char in enumerate(line):
            if quote:
                if char == quote:
                    quote = None
            elif char in "'\"":
                quote = char
            elif char == "#" and (i == 0 or line[i - 1] in " \t"):
                line = line[:i]
                break
        lines.append(line)
    return "\n".join(lines)


def references(text: str):
    """Yield (name, operator, argument) for each ${...} or $NAME, nested
    defaults included. $$ is an escaped dollar, not a reference."""
    i = 0
    while i < len(text):
        if text.startswith("$$", i):
            i += 2
            continue
        if text[i] != "$":
            i += 1
            continue
        if text.startswith("${", i):
            match = NAME.match(text, i + 2)
            if not match:
                i += 2
                continue
            j = match.end()
            depth, k = 1, j
            while k < len(text) and depth:
                if text.startswith("${", k):
                    depth += 1
                    k += 2
                    continue
                if text[k] == "}":
                    depth -= 1
                k += 1
            body = text[j : k - 1]
            operator = next(
                (op for op in (":-", ":?", ":+", "-", "?", "+") if body.startswith(op)),
                "",
            )
            argument = body[len(operator) :]
            yield match.group(0), operator, argument
            yield from references(argument)
            i = k
            continue
        match = NAME.match(text, i + 1)
        if match:
            yield match.group(0), "", ""
            i = match.end()
        else:
            i += 1


def inventory() -> dict[str, dict[str, set[str]]]:
    found: dict[str, dict[str, set[str]]] = {}
    for label, name in COMPOSE_FILES.items():
        path = ROOT / name
        if not path.is_file():
            continue
        for var, operator, argument in references(
            strip_comments(path.read_text(encoding="utf-8"))
        ):
            if operator in (":-", "-"):
                shown = argument if argument else "(empty)"
            elif operator in (":?", "?"):
                shown = "(required)"
            else:
                shown = "(no default)"
            found.setdefault(var, {}).setdefault(label, set()).add(shown)
    return found


def documented() -> set[str]:
    keys = set()
    for line in DOCS.read_text(encoding="utf-8").splitlines():
        if line.startswith("|"):
            first_cell = line.strip().strip("|").split("|")[0]
            keys.update(DOC_KEY.findall(first_cell))
    return keys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--missing", action="store_true", help="list only undocumented keys"
    )
    parser.add_argument(
        "--check", action="store_true", help="exit 1 if any key is undocumented"
    )
    args = parser.parse_args()

    found = inventory()
    known = documented()
    missing = sorted(set(found) - known)

    rows = missing if args.missing or args.check else sorted(found)
    for var in rows:
        per_file = "; ".join(
            f"{label}={','.join(sorted(defaults))}"
            for label, defaults in found[var].items()
        )
        flag = "" if var in known else "   <- not in docs/configuration.md"
        print(f"{var:48} {per_file}{flag}")
    print(
        f"\n{len(found)} variables in the compose files, {len(missing)} undocumented.",
        file=sys.stderr,
    )
    return 1 if args.check and missing else 0


if __name__ == "__main__":
    sys.exit(main())
