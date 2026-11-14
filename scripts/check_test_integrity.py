#!/usr/bin/env python3
"""Flag new test-integrity errors and advisory test changes against a merge base.

Run from the repository root with --base <ref>. Exit 1 means new errors, 2 means
an operational failure. Warnings and notices never fail the check. This is a
source-level review aid, not proof of semantic equivalence: JavaScript detection
uses lexical patterns, not a parser, and assertion warnings compare source lines.
"""

from __future__ import annotations

import argparse
import ast
import html
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath

QUARANTINE = "futureagi/.test_quarantine.json"
JS_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"}
REVIEW_HINT = (
    "Fix the code. If the behavior change is intended, explain it in the PR body "
    "and ask a code owner to review the test change."
)
HINTS = {
    "FOCUSED_TEST": "Remove the focused marker before pushing so CI runs the full suite.",
    "BLANKET_ESLINT": "Fix the code. If suppression must stay, name the rule and give a reason after --.",
    "TS_SUPPRESSION": "Fix the type error, or use @ts-expect-error with a reason; it fails when the error goes away.",
    "ASSERTION_CHANGED": REVIEW_HINT,
    "TEST_DELETED": REVIEW_HINT,
    "TEST_FILE_DELETED": REVIEW_HINT,
    "BACKEND_SKIP": (
        f"Do not add a skip or xfail marker. Add an entry to {QUARANTINE} with id, reason, "
        "owner, issue, added, expires (at most 45 days after added), and mode. conftest.py "
        "applies the marker. Use a real issue; never invent one. Ask a code owner to review."
    ),
    "FRONTEND_SKIP": (
        "Put a real ticket ID in the skipped test's name until a frontend quarantine exists. "
        "Never invent one. Ask a code owner to review."
    ),
    "QUARANTINE_CHANGED": (
        "Ask a code owner to review the reason, owner, real issue and expiry. Keep the "
        "strict xfail default so a passing test fails the run; do not add inline markers."
    ),
}
# Masking preserves offsets so annotations refer to the original source. Template
# literals are treated as strings, including interpolation; this is not a JS AST.
JS_NONCODE = re.compile(
    r"//[^\n]*|/\*[\s\S]*?\*/|'(?:\\[\s\S]|[^'\\\r\n])*'|\"(?:\\[\s\S]|[^\"\\\r\n])*\"|`(?:\\[\s\S]|[^`\\])*`"
)
JS_REGEX = re.compile(
    r"/(?:\\[^\r\n]|\[(?:\\[^\r\n]|[^\]\\\r\n])*\]|[^/\\[\r\n])+/[a-z]*"
)
JS_TOKEN = re.compile(r"[\w$]+|\+\+|--|=>|[^\s]")
JS_BEFORE_REGEX = set("([{,;:=!?&|+*-/%~^<>}") | {
    "return",
    "throw",
    "case",
    "delete",
    "void",
    "typeof",
    "instanceof",
    "in",
    "of",
    "yield",
    "await",
    "else",
    "do",
    "=>",
}
JS_CONTROL_PARENS = {"if", "while", "for", "with", "switch", "catch"}
JS_FOCUS = re.compile(r"(?<![\w$.])(?:it|test|describe)\s*\.\s*only\s*\(")
JS_SKIP = re.compile(r"(?<![\w$.])(?:it|test|describe)\s*\.\s*skip\s*\(")
JS_TEST = re.compile(r"(?<![\w$.])(?:it|test)(?:\s*\.\s*\w+)*\s*\(")
JS_EXPECT = re.compile(r"(?<![\w$.])expect\s*\(")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    code: str
    level: str
    source: str
    message: str


@dataclass(frozen=True)
class Change:
    old: str | None
    new: str | None


class GateError(Exception):
    """The gate could not inspect the requested revisions."""


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode:
        raise GateError(f"git {args[0]} failed: {proc.stderr.strip()[:2000]}")
    return proc.stdout


def is_test(path: str) -> bool:
    p = PurePosixPath(path)
    if p.suffix not in JS_EXTENSIONS | {".py"}:
        return False
    return (
        bool({"test", "tests", "__tests__", "e2e"} & set(p.parts[:-1]))
        or (
            p.suffix == ".py"
            and (
                p.name.startswith("test_")
                or p.name.endswith("_test.py")
                or p.name == "tests.py"
            )
        )
        or (
            p.suffix in JS_EXTENSIONS
            and bool(re.search(r"\.(?:test|spec)\.[^.]+$", p.name))
        )
    )


def is_discoverable_test(path: str) -> bool:
    """Match runner filename/path conventions, not test-support directories.

    Keep these patterns aligned with futureagi/**/pytest.ini,
    frontend/vitest.config.js and e2e/playwright.config.ts. This does not model
    command-line selection, ignored directories or configuration changes.
    """
    p = PurePosixPath(path)
    if path.startswith("futureagi/"):
        return p.suffix == ".py" and p.name.startswith("test_")
    if path.startswith("e2e/"):
        return path.startswith(("e2e/flows/", "e2e/harness/")) and p.name.endswith(
            ".spec.ts"
        )
    if path.startswith("frontend/src/"):
        return p.suffix in {".js", ".jsx", ".ts", ".tsx"} and (
            path.startswith("frontend/src/__tests__/")
            or bool(re.search(r"\.(?:test|spec)\.[^.]+$", p.name))
        )
    if path.startswith("frontend/scripts/api-journeys/"):
        return p.name.endswith((".test.js", ".test.mjs"))
    return False


def changed_files(repo: Path, base: str) -> list[Change]:
    fields = git(
        repo, "diff", "--name-status", "--no-ext-diff", "-M", "-z", base, "HEAD", "--"
    ).split("\0")
    changes = []
    i = 0
    while i < len(fields) and fields[i]:
        status, path = fields[i : i + 2]
        i += 2
        if status.startswith("R"):
            changes.append(Change(path, fields[i]))
            i += 1
        else:
            changes.append(
                Change(None if status == "A" else path, None if status == "D" else path)
            )
    return changes


def js_source(text: str) -> tuple[str, list[tuple[int, str]]]:
    """Mask literals/comments without changing offsets or consuming division as regex.

    A slash starts a regex where an expression can start. Control-statement
    parentheses allow a regex statement after their closing parenthesis; call
    parentheses do not. This remains lexical, not a JavaScript/JSX parser.
    """
    comments = []
    masked = list(text)
    offset = 0
    regex_allowed = True
    previous = ""
    control_parens = []
    while offset < len(text):
        if text[offset].isspace():
            offset += 1
            continue
        match = JS_NONCODE.match(text, offset)
        if match is None and regex_allowed and text[offset] == "/":
            match = JS_REGEX.match(text, offset)
        if match:
            if match.group().startswith(("//", "/*")):
                comments.append((offset, match.group()))
            else:
                regex_allowed = False
                previous = ""
            masked[offset : match.end()] = [
                "\n" if char == "\n" else " " for char in match.group()
            ]
            offset = match.end()
            continue
        token = JS_TOKEN.match(text, offset).group()
        if token == "(":
            control_parens.append(previous in JS_CONTROL_PARENS)
        if token == ")":
            regex_allowed = control_parens.pop() if control_parens else False
        else:
            regex_allowed = token in JS_BEFORE_REGEX and previous != "."
        previous = token
        offset += len(token)
    return "".join(masked), comments


def js_findings(path: str, text: str) -> list[Finding]:
    code, comments = js_source(text)
    lines = text.splitlines()
    found = []
    if is_test(path):
        for pattern, rule, level in (
            (JS_FOCUS, "FOCUSED_TEST", "error"),
            (JS_SKIP, "FRONTEND_SKIP", "warning"),
        ):
            for match in pattern.finditer(code):
                line = text.count("\n", 0, match.start()) + 1
                found.append(
                    Finding(
                        path,
                        line,
                        rule,
                        level,
                        lines[line - 1].strip(),
                        "New test selection marker.",
                    )
                )
    for offset, comment in comments:
        start_line = text.count("\n", 0, offset) + 1
        body = comment[2:-2] if comment.startswith("/*") else comment[2:]
        comment_lines = [re.sub(r"^\s*\*?\s*", "", line) for line in body.splitlines()]
        directive = "\n".join(comment_lines).strip()
        eslint = re.match(r"eslint-disable(?:-next-line|-line)?\b([\s\S]*)", directive)
        # ESLint consumes the whole block before separating its justification.
        if eslint and not eslint.group(1).split("--", 1)[0].strip():
            first_line = next(index for index, line in enumerate(comment_lines) if line)
            found.append(
                Finding(
                    path,
                    start_line + first_line,
                    "BLANKET_ESLINT",
                    "error",
                    " ".join(directive.split()),
                    "New blanket/type suppression.",
                )
            )
        for index, line in enumerate(comment_lines):
            if re.match(r"@ts-(?:nocheck|ignore)\b", line):
                found.append(
                    Finding(
                        path,
                        start_line + index,
                        "TS_SUPPRESSION",
                        "error",
                        line.strip(),
                        "New blanket/type suppression.",
                    )
                )
    return found


def dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{dotted_name(node.value)}.{node.attr}"
    return ""


def python_tree(path: str, text: str) -> ast.Module:
    try:
        return ast.parse(text, filename=path)
    except SyntaxError as exc:
        raise GateError(
            f"Cannot inspect {path}: {exc.msg} at line {exc.lineno}. Fix the syntax and rerun."
        ) from exc


def python_skips(path: str, text: str) -> list[Finding]:
    found = []
    tree = python_tree(path, text)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        name = dotted_name(node)
        if name in {
            "pytest.mark.skip",
            "pytest.mark.skipif",
            "pytest.mark.xfail",
            "pytest.skip",
            "unittest.skip",
            "unittest.skipIf",
            "unittest.skipUnless",
        }:
            source = text.splitlines()[node.lineno - 1].strip()
            found.append(
                Finding(
                    path,
                    node.lineno,
                    "BACKEND_SKIP",
                    "warning",
                    source,
                    "New backend skip or xfail.",
                )
            )
    return found


def selections(path: str, text: str) -> list[Finding]:
    if PurePosixPath(path).suffix in JS_EXTENSIONS:
        return js_findings(path, text)
    if path.endswith(".py") and is_test(path):
        return python_skips(path, text)
    return []


def assertions(path: str, text: str) -> list[tuple[int, str]]:
    lines = text.splitlines()
    if path.endswith(".py"):
        tree = python_tree(path, text)
        line_numbers = {
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Assert)
            or isinstance(node, ast.Call)
            and (
                dotted_name(node.func).startswith("self.assert")
                or dotted_name(node.func) == "pytest.raises"
            )
        }
    else:
        code, _ = js_source(text)
        line_numbers = {
            text.count("\n", 0, match.start()) + 1 for match in JS_EXPECT.finditer(code)
        }
    return [(line, lines[line - 1].strip()) for line in sorted(line_numbers)]


def test_functions(path: str, text: str) -> list[tuple[int, str]]:
    if not path.endswith(".py"):
        code, _ = js_source(text)
        result = []
        for match in JS_TEST.finditer(code):
            # Keep literal names, not callback bodies, so editing a test is not a deletion.
            name = re.match(
                r"\s*(['\"`])((?:\\.|(?!\1)[\s\S])*?)\1", text[match.end() :]
            )
            if name:
                result.append((text.count("\n", 0, match.start()) + 1, name.group(2)))
        return result
    result = []

    def visit(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{scope}.{child.name}" if scope else child.name
                if isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and child.name.startswith("test"):
                    result.append((child.lineno, name))
                visit(child, name)
            else:
                visit(child, scope)

    visit(python_tree(path, text), "")
    return result


def added_lines(old: str, new: str) -> set[int]:
    matcher = SequenceMatcher(None, old.splitlines(), new.splitlines(), autojunk=False)
    return {
        line + 1
        for tag, _, _, start, end in matcher.get_opcodes()
        if tag != "equal"
        for line in range(start, end)
    }


def new_findings(
    old: list[Finding], new: list[Finding], touched: set[int]
) -> list[Finding]:
    """Match old occurrences as a multiset, preferring changed lines for excess hits."""
    remaining = Counter((finding.code, finding.source) for finding in old)
    result = []
    for finding in sorted(new, key=lambda item: (item.line in touched, item.line)):
        key = (finding.code, finding.source)
        if remaining[key]:
            remaining[key] -= 1
        else:
            result.append(finding)
    return result


def removed_items(
    old: list[tuple[int, str]], new: list[tuple[int, str]]
) -> list[tuple[int, str]]:
    remaining = Counter(source for _, source in new)
    removed = []
    for line, source in old:
        if remaining[source]:
            remaining[source] -= 1
        else:
            removed.append((line, source))
    return removed


def inspect_change(change: Change, old: str, new: str) -> list[Finding]:
    path = change.new or change.old
    if path is None:
        raise GateError("A change must have a base or head path.")
    found = []
    if QUARANTINE in (change.old, change.new):
        found.append(
            Finding(
                path, 1, "QUARANTINE_CHANGED", "notice", "", "Test quarantine changed."
            )
        )
    if (
        change.old
        and is_test(change.old)
        and (
            not change.new
            or not is_test(change.new)
            or (
                is_discoverable_test(change.old)
                and not is_discoverable_test(change.new)
            )
        )
    ):
        found.append(
            Finding(
                path,
                1,
                "TEST_FILE_DELETED",
                "warning",
                "",
                f"Existing test file removed: {change.old}.",
            )
        )
    if not change.new:
        return found
    found += new_findings(
        selections(change.old, old) if change.old else [],
        selections(change.new, new),
        added_lines(old, new),
    )
    if change.old and is_test(change.old) and is_test(change.new):
        for detector, rule, label in (
            (assertions, "ASSERTION_CHANGED", "Assertion removed or changed"),
            (test_functions, "TEST_DELETED", "Test function removed or renamed"),
        ):
            for line, source in removed_items(
                detector(change.old, old), detector(change.new, new)
            ):
                head_line = min(line, max(1, len(new.splitlines())))
                message = f"{label} (base {change.old}:{line})."
                found.append(Finding(path, head_line, rule, "warning", source, message))
    return found


def check(repo: Path, base: str) -> tuple[str, list[Finding]]:
    base_commit = git(
        repo, "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}"
    ).strip()
    merge_base = git(repo, "merge-base", base_commit, "HEAD").strip()
    findings = []
    for change in changed_files(repo, merge_base):
        paths = [path for path in (change.old, change.new) if path]
        if not any(
            path == QUARANTINE
            or is_test(path)
            or PurePosixPath(path).suffix in JS_EXTENSIONS
            for path in paths
        ):
            continue
        old = git(repo, "show", f"{merge_base}:{change.old}") if change.old else ""
        new = git(repo, "show", f"HEAD:{change.new}") if change.new else ""
        findings.extend(inspect_change(change, old, new))
    return merge_base, sorted(
        findings, key=lambda item: (item.path, item.line, item.code)
    )


def escape_data(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def annotation(finding: Finding) -> str:
    path = escape_data(finding.path).replace(":", "%3A").replace(",", "%2C")
    message = escape_data(f"{finding.message} {HINTS[finding.code]}")
    return f"::{finding.level} file={path},line={finding.line},title=Test integrity {finding.code}::{message}"


def report(base: str, findings: list[Finding]) -> int:
    counts = Counter(finding.level for finding in findings)
    lines = [
        "## Test integrity (advisory rollout)",
        "",
        f"Compared with merge base `{base[:12]}`.",
        f"{counts['error']} error(s), {counts['warning']} warning(s), {counts['notice']} notice(s).",
        "Errors fail this check. Warnings need a code owner's review, but do not fail it.",
        "This source-level check does not prove tests retain the same coverage.",
        "",
    ]
    for finding in findings:
        print(annotation(finding))
        detail = html.escape(
            f"{finding.path}:{finding.line} {finding.code}: {finding.message} {HINTS[finding.code]}"
        )
        lines.append(f"- <code>{detail}</code>")
    summary = "\n".join(lines) + "\n"
    print(summary)
    if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary_path, "a", encoding="utf-8") as output:
            output.write(summary)
    return 1 if counts["error"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base", required=True, help="base ref; its merge base with HEAD is used"
    )
    args = parser.parse_args(argv)
    try:
        repo = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
        base, findings = check(repo, args.base)
        return report(base, findings)
    except (GateError, OSError) as exc:
        print(f"::error title=Test integrity gate::{escape_data(str(exc))}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
