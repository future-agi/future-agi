#!/usr/bin/env python3
"""Inventory of the environment variables the compose files read, and the
check of the reference that documents them.

deploy/env-reference.toml holds a row for every environment variable of a
self-hosted install; scripts/docs_site.py renders it as
https://docs.futureagi.com/docs/self-hosting/configuration/reference.
This script prints every ${VAR} in the user-facing compose files with the
default each file gives it, and whether the reference has a row for it. Use it
when a compose change makes futureagi/tests/test_env_reference.py fail, to see
what to add.

    python3 scripts/env_reference.py              # full inventory
    python3 scripts/env_reference.py --missing    # only undocumented keys
    python3 scripts/env_reference.py --check      # exit 1 if any is undocumented
                                                  # or the reference data is invalid
    python3 scripts/env_reference.py --render-mdx # the docs-site page
    python3 scripts/env_reference.py --render-md  # parts 1 to 5 as GitHub markdown

Standard library only (Python 3.11 or newer, for tomllib).
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _sibling(name: str):
    """A module next to this one, however this one was loaded."""
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name(f"{name}.py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


docs_site = _sibling("docs_site")
DATA = docs_site.ENV_REFERENCE
# Every root-level docker-compose*.yml belongs here:
# futureagi/tests/test_env_reference.py reads the compose files through this
# list and fails on one that is missing from it.
COMPOSE_FILES = {
    "S": "docker-compose.yml",
    "D": "docker-compose.distributed.yml",
    "S-dev": "docker-compose.dev.yml",
    "D-dev": "docker-compose.distributed.dev.yml",
    "prod": "deploy/docker-compose.production.yml",
    "ui-only": "docker-compose.frontend.yml",
}
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
KEY = re.compile(r"[A-Z][A-Z0-9_]*")
ROLES = ("generated", "internal", "legacy")


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


def load() -> dict:
    """The reference data, as tomllib reads it."""
    return docs_site.load_toml(DATA)


def rows(reference: dict, role: str | None = None):
    """Every row of the reference, or of the part with that role."""
    for part in reference["part"]:
        if role is None or part.get("role") == role:
            for section in part.get("section", []):
                yield from section.get("row", [])


def section_keys(reference: dict, role: str) -> set[str]:
    """Keys documented in the part with that role (generated, internal, legacy)."""
    return {key for row in rows(reference, role) for key in row["keys"]}


def documented(reference: dict | None = None) -> set[str]:
    reference = load() if reference is None else reference
    return {key for row in rows(reference) for key in row["keys"]}


def validate(reference: dict | None = None) -> list[str]:
    """Everything wrong with the reference data; empty when it is sound.

    The one implementation behind --check and futureagi/tests/test_env_reference.py."""
    reference = load() if reference is None else reference
    problems: list[str] = []
    if reference.get("schema") != 1:
        return [f"{DATA.name}: schema must be 1"]
    for key in ("title", "description", "intro", "tldr", "how", "production", "note"):
        if not str(reference.get(key, "")).strip():
            problems.append(f"top-level {key!r} is missing")
    for key in ("title", "description"):
        if '"' in str(reference.get(key, "")):
            problems.append(
                f"top-level {key!r} goes into YAML front matter: no double quotes"
            )
    if not reference.get("card"):
        problems.append("no [[card]] for Dive deeper")
    codes = [setup.get("code") for setup in reference.get("setup", [])]
    if len(set(codes)) != len(codes) or not codes:
        problems.append(f"[[setup]] codes must be unique and present: {codes}")

    roles = [part.get("role") for part in reference.get("part", []) if part.get("role")]
    for role in ROLES:
        if roles.count(role) != 1:
            problems.append(f"exactly one [[part]] needs role = {role!r}")
    for role in set(roles) - set(ROLES):
        problems.append(f"unknown role {role!r}")

    for part in reference.get("part", []):
        for section in part.get("section", []):
            where = f"{section.get('heading') or part['heading']!r}"
            columns = section.get("columns", docs_site.DEFAULT_COLUMNS)
            if len(columns) not in (2, 4):
                problems.append(f"{where}: columns must be 4 wide (or 2 for legacy)")
                continue
            if part.get("role") == "legacy" and len(columns) != 2:
                problems.append(f"{where}: legacy rows take columns Key, Status")
            for row in section.get("row", []):
                label = row.get("label") or row.get("keys")
                keys = row.get("keys")
                if not isinstance(keys, list) or not all(
                    isinstance(key, str) and KEY.fullmatch(key) for key in keys
                ):
                    problems.append(
                        f"{where} {label}: keys must be a list of KEY_NAMEs"
                    )
                    continue
                if not keys and not row.get("label"):
                    problems.append(f"{where}: a row needs keys or a label")
                if not str(row.get("text", "")).strip():
                    problems.append(f"{where} {label}: text is empty")
                fields = {
                    "keys",
                    "label",
                    "text",
                    "default",
                    "setups",
                    "tag",
                    "audience",
                }
                if len(columns) == 2:
                    fields -= {"default", "setups"}
                elif not {"default", "setups"} <= row.keys():
                    problems.append(f"{where} {label}: needs default and setups")
                for extra in sorted(row.keys() - fields):
                    problems.append(f"{where} {label}: unknown field {extra!r}")
                setups = row.get("setups", [])
                for code in setups:
                    if code not in codes:
                        problems.append(f"{where} {label}: unknown setup {code!r}")
                if isinstance(row.get("default"), dict):
                    for group in row["default"]:
                        for code in group.split():
                            if code not in codes:
                                problems.append(
                                    f"{where} {label}: default for unknown setup {code!r}"
                                )
                if row.get("audience", "public") not in ("public", "internal"):
                    problems.append(f"{where} {label}: audience is public or internal")
                if row.get("tag", "telemetry") != "telemetry":
                    problems.append(f"{where} {label}: the only tag is telemetry")

    headings = docs_site.reference_headings(reference)
    slugs = [docs_site.slug(heading) for heading in headings]
    for anchor in sorted({s for s in slugs if slugs.count(s) > 1}):
        problems.append(f"two headings share the anchor #{anchor}")
    problems += docs_site.data_problems(
        docs_site.reference_texts(reference), anchors=set(slugs)
    )

    page_url = docs_site.SITE + docs_site.REFERENCE_PATH
    for path, anchor in docs_site.links_into(page_url):
        if anchor not in slugs:
            problems.append(f"{path} links {page_url}#{anchor}, which is not a heading")

    try:
        first = docs_site.render_reference(reference)
        if first != docs_site.render_reference(reference):
            problems.append("rendering is not deterministic")
    except (KeyError, TypeError, ValueError) as error:
        problems.append(f"the page does not render: {error!r}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--missing", action="store_true", help="list only undocumented keys"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if any key is undocumented or the reference data is invalid",
    )
    parser.add_argument(
        "--render-mdx", action="store_true", help="print the docs-site page"
    )
    parser.add_argument(
        "--render-md",
        action="store_true",
        help="print parts 1 to 5 and the legacy keys as GitHub markdown",
    )
    parser.add_argument(
        "--ref", default="main", help="git ref that repo: links point at"
    )
    args = parser.parse_args()

    if args.render_mdx:
        sys.stdout.write(docs_site.render_reference(load(), args.ref))
        return 0
    if args.render_md:
        sys.stdout.write(docs_site.render_reference_markdown(load(), args.ref))
        return 0

    found = inventory()
    reference = load()
    known = documented(reference)
    missing = sorted(set(found) - known)

    shown = missing if args.missing or args.check else sorted(found)
    for var in shown:
        per_file = "; ".join(
            f"{label}={','.join(sorted(defaults))}"
            for label, defaults in found[var].items()
        )
        flag = "" if var in known else f"   <- not in {DATA.relative_to(ROOT)}"
        print(f"{var:48} {per_file}{flag}")
    print(
        f"\n{len(found)} variables in the compose files, {len(missing)} undocumented.",
        file=sys.stderr,
    )
    if not args.check:
        return 0
    problems = validate(reference)
    for problem in problems:
        print(f"{DATA.relative_to(ROOT)}: {problem}", file=sys.stderr)
    return 1 if missing or problems else 0


if __name__ == "__main__":
    sys.exit(main())
