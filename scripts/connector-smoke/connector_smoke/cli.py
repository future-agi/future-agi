"""The connector live-smoke command (L4, key-gated).

    cd scripts/connector-smoke
    CONNECTOR_SMOKE_LIVE=1 CONNECTOR_SMOKE_KEYS="$(...)" \\
        python3 -m connector_smoke --out <dir> [--provider <app>/<provider>]... [--allow-writes]

Gates, checked before anything is read, contacted or written:
1. No keys (CONNECTOR_SMOKE_KEYS unset or empty): it skips cleanly, exit 0.
2. Keys without CONNECTOR_SMOKE_LIVE=1: it refuses, exit 3. The keys alone
   never start a live run.
3. A keys entry that does not name its test account, or any other malformed
   field: a configuration error, exit 2.
Then each catalog provider with a keys entry is smoked (smoke.py); one
without is skipped (no_key). The drift report (report.py) is written to
--out only when at least one provider was smoked on a named test account;
otherwise nothing is written. It never pushes or opens a PR.

Exit: 0 every smoked provider passed (or nothing to do), 1 drift, a
failure or an incomplete provider (smoked, but nothing could be checked) (a
provider's own error, a keys entry that does not fit its manifest
and an unexpected error inside one provider's smoke are each a failed check),
2 configuration error, 3 refused by the gate, 4 the report or the output
would hold a credential or an email (nothing written).
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import catalog, keys, report, smoke, transport

LIVE_ENV = "CONNECTOR_SMOKE_LIVE"
EXIT_OK, EXIT_DRIFT, EXIT_CONFIG, EXIT_REFUSED, EXIT_UNSAFE = 0, 1, 2, 3, 4
DEFAULT_CATALOG = (
    Path(__file__).resolve().parents[3] / "agentcc-gateway" / "catalog" / "connectors"
)
PREFIX = "connector-smoke: "
MAX_TIMEOUT = 600.0


class _Parser(argparse.ArgumentParser):
    def error(
        self, message
    ):  # argparse's default exits 2 as well; keep its text off values
        self.print_usage(sys.stderr)
        raise SystemExit(EXIT_CONFIG)


def _args(argv):
    p = _Parser(
        prog="python3 -m connector_smoke",
        description="Connector live smoke (L4), key-gated.",
    )
    p.add_argument(
        "--out", required=True, type=Path, help="directory for drift-report.json"
    )
    p.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_CATALOG,
        help="catalog directory (default: the repo's)",
    )
    p.add_argument(
        "--provider",
        action="append",
        default=[],
        metavar="APP/PROVIDER",
        help="smoke only these providers (repeatable)",
    )
    p.add_argument(
        "--allow-writes",
        action="store_true",
        help="also call write tools, only for keys entries with sandbox: true",
    )
    p.add_argument(
        "--timeout",
        type=_seconds,
        default=transport.DEFAULT_TIMEOUT,
        help=f"seconds per request, above 0 and at most {MAX_TIMEOUT:g}",
    )
    return p.parse_args(argv)


def _seconds(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("not a number") from None
    if not math.isfinite(value) or value <= 0 or value > MAX_TIMEOUT:
        raise argparse.ArgumentTypeError(f"must be above 0 and at most {MAX_TIMEOUT:g}")
    return value


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _all_secrets(doc: keys.Keys) -> list:
    out = []
    for entry in doc.entries.values():
        values = [entry.bearer] + list(entry.parts.values())
        out += [v for v in values if v and len(v) >= keys.MIN_SCANNED_SECRET]
    return out


def main(argv=None, environ=None, net: transport.Net | None = None, out=None) -> int:
    """Run the smoke. environ, net and out are for tests: the process
    environment, the network and stdout by default."""
    out = out or sys.stdout
    say = lambda text: print(PREFIX + text, file=out)  # noqa: E731
    args = _args(argv)
    env = os.environ if environ is None else environ
    raw = env.get(keys.KEYS_ENV)
    if raw is None or raw.strip() == "":
        say(
            f"skipped: no keys ({keys.KEYS_ENV} is empty); nothing contacted, nothing written"
        )
        return EXIT_OK
    if env.get(LIVE_ENV) != "1":
        say(
            f"refused: {keys.KEYS_ENV} is set but {LIVE_ENV} is not 1; nothing contacted, nothing written"
        )
        return EXIT_REFUSED
    try:
        doc = keys.parse(raw)
    except keys.KeysError as err:
        say(
            f"configuration error: {keys.KEYS_ENV}: {err}; nothing contacted, nothing written"
        )
        return EXIT_CONFIG
    try:
        apps = catalog.load(args.catalog)
    except catalog.CatalogError as err:
        say(f"configuration error: catalog: {err}")
        return EXIT_CONFIG
    providers = [p for group in apps.values() for p in group]
    wanted = set(args.provider)
    unknown = wanted - {p.key for p in providers}
    if unknown:
        say(
            f"configuration error: --provider names {len(unknown)} provider(s) the catalog does not have"
        )
        return EXIT_CONFIG
    if wanted:
        providers = [p for p in providers if p.key in wanted]
    return _run(providers, doc, args, net, say)


def _run(providers, doc: keys.Keys, args, net, say) -> int:
    opts = smoke.Options(allow_writes=args.allow_writes, timeout=args.timeout, net=net)
    started = _now()
    results = []
    for provider in providers:
        entry = doc.entry(provider.app, provider.id)
        if entry is None:
            results.append(report.skipped(provider, "no_key"))
            continue
        try:
            results.append(smoke.run(provider, entry, opts))
        except Exception as err:  # noqa: BLE001 - its text may hold a URL: report the type only
            failed = report.ProviderResult(
                app=provider.app,
                provider=provider.id,
                kind=provider.kind,
                test_account=entry.test_account,
                sandbox=entry.sandbox,
            )
            failed.add("run", "fail", reason=f"internal_error:{type(err).__name__}")
            results.append(failed.finish())
    unused = len(set(doc.entries) - {p.key for p in providers})
    secrets = _all_secrets(doc)
    lines = [report.line(r) for r in results]
    try:
        report.check_safe("\n".join(lines), secrets)
    except report.UnsafeReport as err:
        say(f"refused to print the summary: {err.code}; nothing written")
        return EXIT_UNSAFE
    for text in lines:
        say(text)
    attempted = [r for r in results if r.attempted]
    if not attempted:
        say(
            f"nothing smoked ({len(results)} provider(s), none with a usable keys entry); nothing written"
        )
        return EXIT_OK
    built = report.build(
        results, started, _now(), args.allow_writes, unused_keys=unused
    )
    try:
        text = report.render(built, secrets)
    except report.UnsafeReport as err:
        say(f"refused to write the report: {err.code}; nothing written")
        return EXIT_UNSAFE
    path = report.write(args.out, text)
    summary = built["summary"]
    say(f"report: {path} ({', '.join(f'{k} {v}' for k, v in summary.items())})")
    # a provider with a key that was smoked but checked nothing is not a pass
    failed = summary["fail"] or summary["drift"] or summary["incomplete"]
    return EXIT_DRIFT if failed else EXIT_OK
