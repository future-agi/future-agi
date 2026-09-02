#!/usr/bin/env python3
"""Regenerate futureagi/accounts/disposable_domains.py from a sorted,
one-domain-per-line source file.

Used by .github/workflows/refresh-disposable-domains.yml (TH-7620) to build
the PR diff, and safe to run locally for the same result:

    python3 scripts/generate_disposable_domains.py \\
        --domains /tmp/upstream_domains.txt \\
        --version 0.0.240 \\
        --out futureagi/accounts/disposable_domains.py
"""

import argparse
import ast
import re
from pathlib import Path


VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+!-]{0,63}$")

TEMPLATE = '''"""Disposable/throwaway email domains used to reject signups.

Generated from disposable-email-domains {version}'s source list
(source_data/disposable_email_blocklist.conf) and refreshed weekly by
.github/workflows/refresh-disposable-domains.yml (TH-7620), which opens a PR
for review rather than writing here directly.

Do not hand-edit entries into the middle of this file - the next scheduled
refresh will overwrite them. Add permanent exceptions in accounts/utils.py
instead.
"""

DISPOSABLE_EMAIL_DOMAINS = frozenset(
    {{
{body}
    }}
)
'''


def load_domains(path):
    with open(path) as f:
        return {line.strip() for line in f if line.strip()}


def load_existing_domains(out_path):
    """Parse the domain set out of a previously generated module.

    The upstream source is untrusted, so a previous run of this script could
    in principle have produced anything; this walks the AST and literal_evals
    just the set passed to frozenset() rather than importing the file.
    """
    path = Path(out_path)
    if not path.exists():
        return set()

    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "DISPOSABLE_EMAIL_DOMAINS"
            for target in node.targets
        ):
            set_literal = node.value.args[0]
            return set(ast.literal_eval(set_literal))
    return set()


def validate_version(version):
    if not VERSION_RE.match(version or ""):
        raise ValueError(
            f"Refusing to render untrusted upstream version {version!r}: "
            "expected only alphanumerics, dot, underscore, plus, hyphen or '!'"
        )
    return version


def render(domains, version):
    # repr() rather than an f-string interpolation: the domain came from an
    # untrusted upstream file, and this text becomes a Python module that
    # gets imported. repr() always produces a quoted, escaped string literal,
    # so a value like `x", __import__("os").system("..."), "y` stays inert
    # data instead of becoming code.
    body = "\n".join(f"        {d!r}," for d in sorted(domains))
    # Same problem one level up: the version comes from the upstream PyPI JSON
    # and lands inside the generated module's docstring, where a triple quote
    # would close it and turn everything after into code.
    return TEMPLATE.format(version=validate_version(version), body=body)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--domains", required=True, help="path to a sorted, one-domain-per-line file"
    )
    parser.add_argument(
        "--version", required=True, help="upstream disposable-email-domains version"
    )
    parser.add_argument("--out", required=True, help="output path for the generated module")
    parser.add_argument(
        "--github-output",
        help="if set, append changed/added/removed to this $GITHUB_OUTPUT file",
    )
    args = parser.parse_args(argv)

    new_domains = load_domains(args.domains)
    old_domains = load_existing_domains(args.out)

    # Render before opening for write: a rejected version raises here rather
    # than after "w" has already truncated the vendored list.
    rendered = render(new_domains, args.version)
    with open(args.out, "w") as f:
        f.write(rendered)

    added = len(new_domains - old_domains)
    removed = len(old_domains - new_domains)
    changed = new_domains != old_domains

    print(f"Wrote {len(new_domains)} domains to {args.out}")
    print(f"changed={str(changed).lower()} added={added} removed={removed}")

    if args.github_output:
        with open(args.github_output, "a") as f:
            f.write(f"changed={str(changed).lower()}\n")
            f.write(f"added={added}\n")
            f.write(f"removed={removed}\n")


if __name__ == "__main__":
    main()
