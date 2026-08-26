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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--domains", required=True, help="path to a sorted, one-domain-per-line file"
    )
    parser.add_argument(
        "--version", required=True, help="upstream disposable-email-domains version"
    )
    parser.add_argument("--out", required=True, help="output path for the generated module")
    args = parser.parse_args()

    with open(args.domains) as f:
        domains = sorted({line.strip() for line in f if line.strip()})

    body = "\n".join(f'        "{d}",' for d in domains)
    content = TEMPLATE.format(version=args.version, body=body)

    with open(args.out, "w") as f:
        f.write(content)

    print(f"Wrote {len(domains)} domains to {args.out}")


if __name__ == "__main__":
    main()
