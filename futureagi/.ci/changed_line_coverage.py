"""Collect selected backend packages and report changed-line coverage without a gate."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

PACKAGES = ("tracer", "model_hub", "agentic_eval")
ADVISORY_TARGET = 80


def configure(filters: list[str], destination: Path) -> list[str]:
    packages = [name for name in PACKAGES if f"coverage_{name}" in filters]
    if not packages:
        raise ValueError("No eval/trace package changed; do not enable coverage")
    destination.write_text(
        "[run]\nrelative_files = true\nsource = "
        + ", ".join(packages)
        + "\n[report]\ninclude_namespace_packages = true\n"
        "omit = */tests/*, */test_*.py, */conftest.py\nfail_under = 0\n"
    )
    return packages


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def escape_annotation(value: str) -> str:
    return (
        value.replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
        .replace(":", "%3A")
        .replace(",", "%2C")
    )


def summarize(report: dict, packages: list[str], merge_base: str) -> str:
    """Treat an empty executable diff as N/A, never as perfect coverage."""
    total = report["total_num_lines"]
    missing = report["total_num_violations"]
    lines = [
        "## Changed-line Python coverage (advisory)",
        "",
        f"Packages: {', '.join(packages)}. Merge base: `{merge_base}`.",
        f"Reference target: {ADVISORY_TARGET}%. No enforced threshold.",
        "Backend Tests Pass does not depend on this report.",
        "",
    ]
    if "agentic_eval" in packages:
        lines.extend(
            [
                "**agentic_eval scope:** backend-shard execution only. "
                "Its own tests are excluded by futureagi/pytest.ini. "
                "This does not measure the standalone agentic_eval suite.",
                "",
            ]
        )
    if total:
        percent = 100 * (total - missing) / total
        result = f"{percent:.2f}% ({total - missing}/{total} changed executable lines)"
        lines.append(f"**{result}**")
        print(
            f"::notice title=Advisory changed-line coverage::{result}; no enforced threshold"
        )
    else:
        lines.append("**N/A: no changed executable lines in the measured packages.**")
        print("::notice::Changed-line coverage N/A: no changed executable lines")
    lines.extend(["", "### Uncovered changed lines", ""])
    for path, stats in sorted(report["src_stats"].items()):
        uncovered = stats["violation_lines"]
        if not uncovered:
            continue
        lines.append(f"- `{path}`: {', '.join(map(str, uncovered))}")
        for number in uncovered:
            print(
                f"::notice file={escape_annotation(path)},line={number},"
                "title=Uncovered changed line::Not exercised by the backend shards (advisory)"
            )
    if not missing:
        lines.append("None." if total else "Not applicable.")
    return "\n".join(lines) + "\n"


def report_coverage(args: argparse.Namespace) -> None:
    """Union all shard execution data before comparing the checkout to its merge base."""
    packages = configure(json.loads(args.filters), args.config)
    data = sorted(args.artifacts.glob(".coverage.*"))
    if args.test_result != "success" or len(data) != args.shards:
        message = (
            "Changed-line coverage unavailable: "
            f"backend result={args.test_result}, coverage shards={len(data)}/{args.shards}. "
            "Do not interpret missing or incomplete test data as coverage."
        )
        args.summary.write_text(
            f"## Changed-line Python coverage (advisory)\n\n{message}\n"
        )
        print(f"::warning::{message}")
        return
    args.output.mkdir(parents=True, exist_ok=True)
    run(
        sys.executable,
        "-m",
        "coverage",
        "combine",
        "--rcfile",
        str(args.config),
        "--keep",
        *map(str, data),
    )
    xml = args.output / "coverage.xml"
    run(
        sys.executable,
        "-m",
        "coverage",
        "xml",
        "--rcfile",
        str(args.config),
        "-o",
        str(xml),
    )
    merge_base = subprocess.check_output(
        ["git", "merge-base", args.base, "HEAD"], text=True
    ).strip()
    report = args.output / "diff-coverage.json"
    run(
        "diff-cover",
        str(xml),
        "--compare-branch",
        merge_base,
        "--diff-range-notation",
        "..",
        "--ignore-staged",
        "--ignore-unstaged",
        "--fail-under",
        "0",
        "--format",
        f"json:{report}",
        "--include",
        *(f"futureagi/{name}/*" for name in packages),
    )
    args.summary.write_text(
        summarize(json.loads(report.read_text()), packages, merge_base)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("configure", "report"))
    parser.add_argument("--filters", required=True)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--base")
    parser.add_argument("--shards", type=int, default=10)
    parser.add_argument("--test-result", default="success")
    args = parser.parse_args()
    if args.command == "configure":
        configure(json.loads(args.filters), args.config)
    else:
        for name in ("artifacts", "output", "summary", "base"):
            if getattr(args, name) is None:
                parser.error(f"report requires --{name}")
        report_coverage(args)


if __name__ == "__main__":
    main()
