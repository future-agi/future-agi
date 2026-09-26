"""The environment-variable reference stays complete and honest.

docs/configuration.md is the one place a self-hoster looks up a key. These
tests fail when a compose file starts reading a ``${VAR}`` the page does not
document, when .env.example shows a key the page does not document, or when
.env.example carries a key that nothing reads any more.

Files are parsed, never run: no Docker, no services, no database.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs" / "configuration.md"
ENV_EXAMPLE = ROOT / ".env.example"
INSTALLER = ROOT / "bin" / "install"

# The compose files a user runs. Every other root-level docker-compose*.yml is
# picked up too, so a new one cannot slip past the reference.
REQUIRED_COMPOSE_FILES = (
    "docker-compose.yml",
    "docker-compose.distributed.yml",
    "docker-compose.dev.yml",
    "docker-compose.distributed.dev.yml",
    "deploy/docker-compose.production.yml",
)

# Read by Docker Compose itself rather than by anything in this repository.
COMPOSE_BUILTINS = frozenset(
    {"COMPOSE_FILE", "COMPOSE_PROFILES", "COMPOSE_PROJECT_NAME"}
)

# Keys .env.example may keep although nothing reads them any more, each with
# the reason. Every entry must also have a row in the "Legacy and retired
# keys" section of docs/configuration.md.
LEGACY_ENV_EXAMPLE_KEYS: dict[str, str] = {}

# Where a key counts as read. Python and Go sources match on a quoted literal
# ("KEY"), shell and config files on the bare word.
PYTHON_READER_ROOT = ROOT / "futureagi"
GO_READER_ROOTS = (ROOT / "fi-collector", ROOT / "agentcc-gateway")
WORD_READER_FILES = (
    "bin/install",
    "bin/install.ps1",
    "bin/dev",
    "deploy/platform/supervisord.conf",
    "frontend/docker-entrypoint.sh",
    "futureagi/entrypoint.sh",
)
WORD_READER_DIRS = ("deploy/platform/bin",)
SKIPPED_DIRS = frozenset(
    {
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        "tests",
        "migrations",
        ".git",
        "vendor",
    }
)

KEY = r"[A-Z][A-Z0-9_]*"
ASSIGNED = re.compile(rf"^({KEY})=(.*)$")
COMMENTED = re.compile(rf"^#\s?({KEY})=")
BACKTICKED_KEY = re.compile(rf"`({KEY})`")
INTERPOLATION = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)")


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------


class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader that also accepts Compose's merge tags (!override, !reset)."""


def _any_tag(loader: yaml.SafeLoader, _suffix: str, node: yaml.Node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


_ComposeLoader.add_multi_constructor("!", _any_tag)


def _compose_files() -> list[Path]:
    found = {ROOT / name for name in REQUIRED_COMPOSE_FILES}
    found.update(ROOT.glob("docker-compose*.yml"))
    return sorted(found)


def _strings(node) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [
            s for key, value in node.items() for s in (*_strings(key), *_strings(value))
        ]
    if isinstance(node, list):
        return [s for item in node for s in _strings(item)]
    return []


def _interpolated(text: str) -> set[str]:
    """Variables Compose substitutes in one string. ``$$`` is a literal ``$``
    (a shell variable inside a command), so it never names a Compose variable."""
    return set(INTERPOLATION.findall(text.replace("$$", "")))


@lru_cache(maxsize=1)
def compose_variables() -> dict[str, set[str]]:
    """Every variable a compose file interpolates -> the files that do.

    Parsed as YAML, so ``${VAR}`` in comments does not count."""
    variables: dict[str, set[str]] = {}
    for path in _compose_files():
        document = yaml.load(path.read_text(encoding="utf-8"), Loader=_ComposeLoader)
        for text in _strings(document):
            for name in _interpolated(text):
                variables.setdefault(name, set()).add(str(path.relative_to(ROOT)))
    return variables


@lru_cache(maxsize=1)
def env_example() -> tuple[list[tuple[str, str]], list[str]]:
    """(assigned (key, value) pairs, commented-out example keys) of .env.example."""
    assigned: list[tuple[str, str]] = []
    commented: list[str] = []
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if match := ASSIGNED.match(line):
            assigned.append((match.group(1), match.group(2)))
        elif match := COMMENTED.match(line):
            commented.append(match.group(1))
    return assigned, commented


def env_example_keys() -> set[str]:
    assigned, commented = env_example()
    return {key for key, _ in assigned} | set(commented)


def _table_rows(markdown: str) -> list[list[str]]:
    rows = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or set(stripped) <= {"|", "-", " ", ":"}:
            continue
        rows.append([cell.strip() for cell in stripped.strip("|").split("|")])
    return rows


def _row_keys(markdown: str) -> set[str]:
    """Keys named in the first cell of a table row: the rows that document them."""
    return {
        key
        for cells in _table_rows(markdown)
        for key in BACKTICKED_KEY.findall(cells[0])
    }


def _section(markdown: str, heading_prefix: str) -> str:
    lines = markdown.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.startswith(f"## {heading_prefix}")),
        None,
    )
    assert start is not None, (
        f"docs/configuration.md has no '## {heading_prefix}' section"
    )
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    return "\n".join(lines[start:end])


@lru_cache(maxsize=1)
def docs_text() -> str:
    return DOCS.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def documented_keys() -> set[str]:
    return _row_keys(docs_text())


def _walk(root: Path, suffix: str):
    """Source files under root, pruning virtualenvs, vendored code and tests."""
    for directory, subdirs, files in os.walk(root):
        subdirs[:] = [name for name in subdirs if name not in SKIPPED_DIRS]
        for name in files:
            if name.endswith(suffix) and not (
                name.startswith("test_") or name.endswith("_test.go")
            ):
                yield Path(directory, name)


@lru_cache(maxsize=1)
def read_keys() -> frozenset[str]:
    """Names some code or script outside the compose files reads."""
    quoted = re.compile(rf"[\"']({KEY})[\"']")
    word = re.compile(rf"\b({KEY})\b")
    names: set[str] = set()
    sources = [(path, quoted) for path in _walk(PYTHON_READER_ROOT, ".py")]
    sources += [
        (path, quoted) for root in GO_READER_ROOTS for path in _walk(root, ".go")
    ]
    sources += [(ROOT / name, word) for name in WORD_READER_FILES]
    sources += [
        (path, word)
        for directory in WORD_READER_DIRS
        for path in (ROOT / directory).iterdir()
        if path.is_file()
    ]
    for path, pattern in sources:
        if path.is_file():
            names.update(
                pattern.findall(path.read_text(encoding="utf-8", errors="ignore"))
            )
    return frozenset(names)


def installer_generated_keys() -> set[str]:
    """Keys ./bin/install fills with a generated secret."""
    script = INSTALLER.read_text(encoding="utf-8")
    array = re.search(r"declare -a INSTALL_SECRETS=\(([^)]*)\)", script)
    assert array, "bin/install no longer declares INSTALL_SECRETS; update this test"
    keys = set(re.findall(KEY, array.group(1)))
    # Filled outside the loop, e.g. `fill_secret INTEGRATION_ENCRYPTION_KEY gen_fernet_key`.
    keys.update(re.findall(rf"fill_secret ({KEY}) gen_", script))
    return keys


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


def test_the_compose_files_parse_and_interpolate_something() -> None:
    for name in REQUIRED_COMPOSE_FILES:
        assert (ROOT / name).is_file(), f"{name} is missing"
    variables = compose_variables()
    # A parser that silently found nothing would make every check below pass.
    assert {"SECRET_KEY", "PG_PASSWORD", "FRONTEND_PORT", "VITE_HOST_API"} <= set(
        variables
    )
    # Shell variables escaped as $${...} inside compose commands are not keys.
    assert not {"topic", "error_feed", "server_pid", "attempt"} & set(variables)


def test_every_compose_variable_is_documented() -> None:
    missing = {
        name: sorted(files)
        for name, files in compose_variables().items()
        if name not in documented_keys()
    }
    assert not missing, (
        "These ${VAR}s are read by a compose file but have no row in "
        f"docs/configuration.md (first column, in backticks): {missing}"
    )


def test_every_env_example_key_is_documented() -> None:
    missing = sorted(env_example_keys() - documented_keys())
    assert not missing, (
        f".env.example shows keys docs/configuration.md does not document: {missing}"
    )


def test_env_example_carries_no_key_that_nothing_reads() -> None:
    readers = set(compose_variables()) | COMPOSE_BUILTINS | read_keys()
    unread = sorted(env_example_keys() - readers - set(LEGACY_ENV_EXAMPLE_KEYS))
    assert not unread, (
        f".env.example carries keys nothing reads: {unread}. Remove them, or keep "
        "one on purpose by adding it to LEGACY_ENV_EXAMPLE_KEYS with a reason and "
        "to the 'Legacy and retired keys' section of docs/configuration.md."
    )


def test_legacy_allowlist_is_current_and_documented_as_legacy() -> None:
    legacy_rows = _row_keys(_section(docs_text(), "Legacy and retired keys"))
    for key, reason in LEGACY_ENV_EXAMPLE_KEYS.items():
        assert reason.strip(), f"{key} needs a reason in LEGACY_ENV_EXAMPLE_KEYS"
        assert key in env_example_keys(), (
            f"{key} left .env.example; drop it from the allowlist"
        )
        assert key in legacy_rows, (
            f"{key} is kept as legacy but not listed as legacy in the docs"
        )


def test_env_example_assigns_each_key_once() -> None:
    assigned, commented = env_example()
    duplicates = sorted(
        key for key, count in Counter(key for key, _ in assigned).items() if count > 1
    )
    assert not duplicates, (
        f".env.example assigns these keys more than once: {duplicates}"
    )
    both = sorted({key for key, _ in assigned} & set(commented))
    assert not both, f".env.example both sets and comments out: {both}"


def test_env_example_values_carry_no_inline_comments() -> None:
    # bin/install reads a value as everything after '=', so a trailing
    # "# comment" would become part of it (and a secret would look set).
    assigned, _ = env_example()
    offenders = sorted(key for key, value in assigned if "#" in value)
    assert not offenders, f"move these comments to their own line: {offenders}"


def test_installer_generated_secrets_ship_empty_and_documented_in_section_one() -> None:
    generated = installer_generated_keys()
    assert {"SECRET_KEY", "PG_PASSWORD", "INTEGRATION_ENCRYPTION_KEY"} <= generated
    values = dict(env_example()[0])
    section_one = _row_keys(_section(docs_text(), "1. Generated by the installer"))
    for key in sorted(generated):
        assert key in values, (
            f"{key} is generated by bin/install but not listed in .env.example"
        )
        assert values[key] == "" or values[key].startswith("CHANGEME-"), (
            f"{key} must ship empty (or as a CHANGEME- placeholder) so the "
            f"installer generates it; .env.example has {values[key]!r}"
        )
        assert key in section_one, (
            f"{key} is generated by bin/install; document it in section 1"
        )


def test_internal_keys_are_not_offered_in_env_example() -> None:
    internal = _row_keys(_section(docs_text(), "5. Internal"))
    offered = sorted(env_example_keys() & internal)
    assert not offered, (
        f".env.example offers keys the compose files override, so setting them does nothing: {offered}"
    )


@pytest.mark.parametrize(
    "key, why",
    [
        (
            "OSS_RETURN_PASSWORD_RESET_LINK",
            "the reset endpoint is unauthenticated: anyone could take over any account",
        ),
        (
            "RECAPTCHA_ENABLED",
            "the published UI images carry no site key, so every sign-up would be rejected",
        ),
    ],
)
def test_env_example_never_turns_on_a_risky_opt_in(key: str, why: str) -> None:
    value = dict(env_example()[0]).get(key, "false").strip().lower()
    assert value not in {"true", "1", "yes"}, (
        f".env.example must not enable {key}: {why}"
    )
