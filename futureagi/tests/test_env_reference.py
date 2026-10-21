"""The environment-variable reference stays complete and honest.

deploy/env-reference.toml is the one place a self-hoster's key is described:
scripts/docs_site.py renders it as the configuration reference page of
docs.futureagi.com. These tests fail when a compose file starts reading a
``${VAR}`` the reference has no row for, when .env.example shows a key it has
no row for, when .env.example carries a key that nothing reads any more, or
when the reference data would not render as a sound page.

Files are parsed, never run: no Docker, no services, no database.
"""

from __future__ import annotations

import copy
import importlib.util
import os
import re
from collections import Counter
from fnmatch import fnmatchcase
from functools import lru_cache
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "env_reference.py"
ENV_EXAMPLE = ROOT / ".env.example"
# What ./bin/install generates (it sources bin/lib/*.sh).
INSTALLER_SECRETS = ROOT / "bin" / "lib" / "secrets.sh"
BACKEND_CI = ROOT / ".github" / "workflows" / "backend-ci.yml"

# The compose files, the ${VAR} parser and the reference data are the ones
# scripts/env_reference.py reports with, so the test and the script agree.
_spec = importlib.util.spec_from_file_location("env_reference", SCRIPT)
env_reference = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(env_reference)
docs_site = env_reference.docs_site
DATA = env_reference.DATA
COMPOSE_FILES = tuple(env_reference.COMPOSE_FILES.values())

# Read by Docker Compose itself rather than by anything in this repository.
COMPOSE_BUILTINS = frozenset(
    {"COMPOSE_FILE", "COMPOSE_PROFILES", "COMPOSE_PROJECT_NAME"}
)

# Keys .env.example may keep although nothing reads them any more, each with
# the reason. Every entry must also have a row in the part of
# deploy/env-reference.toml with role = "legacy" ("Legacy and retired keys").
LEGACY_ENV_EXAMPLE_KEYS: dict[str, str] = {}

# Where a key counts as read. Python and Go sources match on a quoted literal
# ("KEY"), shell and config files on the bare word.
PYTHON_READER_ROOT = ROOT / "futureagi"
GO_READER_ROOTS = (ROOT / "fi-collector", ROOT / "agentcc-gateway")
WORD_READER_FILES = (
    "bin/install",
    "bin/install.ps1",
    "bin/dev",
    "deploy/standalone/supervisord.conf",
    "frontend/docker-entrypoint.sh",
    "futureagi/entrypoint.sh",
)
WORD_READER_DIRS = ("bin/lib", "deploy/standalone/bin")
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


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------


@lru_cache(maxsize=1)
def compose_variables() -> dict[str, list[str]]:
    """Every variable a compose file interpolates -> the files that do."""
    return {
        name: sorted(env_reference.COMPOSE_FILES[label] for label in files)
        for name, files in env_reference.inventory().items()
    }


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


@lru_cache(maxsize=1)
def reference() -> dict:
    return env_reference.load()


@lru_cache(maxsize=1)
def documented_keys() -> set[str]:
    return env_reference.documented(reference())


def role_keys(role: str) -> set[str]:
    """Keys of the part with that role: generated, internal or legacy."""
    return env_reference.section_keys(reference(), role)


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
    script = INSTALLER_SECRETS.read_text(encoding="utf-8")
    array = re.search(r"^INSTALL_SECRETS=\(([^)]*)\)", script, re.MULTILINE)
    assert array, (
        f"{INSTALLER_SECRETS.name} no longer declares INSTALL_SECRETS; update this test"
    )
    keys = set(re.findall(KEY, array.group(1)))
    # Filled outside the loop, e.g. `fill_secret INTEGRATION_ENCRYPTION_KEY gen_fernet_key`.
    keys.update(re.findall(rf"fill_secret ({KEY}) gen_", script))
    return keys


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


def test_the_compose_files_are_listed_and_interpolate_something() -> None:
    listed = {ROOT / name for name in COMPOSE_FILES}
    for path in sorted(listed):
        assert path.is_file(), f"{path.relative_to(ROOT)} is missing"
    unlisted = sorted(
        path.name for path in ROOT.glob("docker-compose*.yml") if path not in listed
    )
    assert not unlisted, (
        f"add these to COMPOSE_FILES in scripts/env_reference.py: {unlisted}"
    )
    variables = compose_variables()
    # A parser that silently found nothing would make every check below pass.
    assert {"SECRET_KEY", "PG_PASSWORD", "FRONTEND_PORT", "VITE_HOST_API"} <= set(
        variables
    )
    # Shell variables escaped as $${...} inside compose commands are not keys.
    assert not {"topic", "error_feed", "server_pid", "attempt"} & set(variables)


def test_every_compose_variable_is_documented() -> None:
    missing = {
        name: files
        for name, files in compose_variables().items()
        if name not in documented_keys()
    }
    assert not missing, (
        "These ${VAR}s are read by a compose file but have no row in "
        f"{DATA.relative_to(ROOT)} (a row's keys): {missing}. "
        "`python3 scripts/env_reference.py --missing` lists them with their defaults."
    )


def test_every_env_example_key_is_documented() -> None:
    missing = sorted(env_example_keys() - documented_keys())
    assert not missing, (
        f".env.example shows keys {DATA.relative_to(ROOT)} has no row for: {missing}"
    )


def test_env_example_carries_no_key_that_nothing_reads() -> None:
    readers = set(compose_variables()) | COMPOSE_BUILTINS | read_keys()
    unread = sorted(env_example_keys() - readers - set(LEGACY_ENV_EXAMPLE_KEYS))
    assert not unread, (
        f".env.example carries keys nothing reads: {unread}. Remove them, or keep "
        "one on purpose by adding it to LEGACY_ENV_EXAMPLE_KEYS with a reason and "
        f"a row to the legacy part of {DATA.relative_to(ROOT)}."
    )


def test_legacy_allowlist_is_current_and_documented_as_legacy() -> None:
    legacy_rows = role_keys("legacy")
    for key, reason in LEGACY_ENV_EXAMPLE_KEYS.items():
        assert reason.strip(), f"{key} needs a reason in LEGACY_ENV_EXAMPLE_KEYS"
        assert key in env_example_keys(), (
            f"{key} left .env.example; drop it from the allowlist"
        )
        assert key in legacy_rows, (
            f"{key} is kept as legacy but has no row in the legacy part of the reference"
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
    section_one = role_keys("generated")
    for key in sorted(generated):
        assert key in values, (
            f"{key} is generated by bin/install but not listed in .env.example"
        )
        assert values[key] == "" or values[key].startswith("CHANGEME-"), (
            f"{key} must ship empty (or as a CHANGEME- placeholder) so the "
            f"installer generates it; .env.example has {values[key]!r}"
        )
        assert key in section_one, (
            f"{key} is generated by bin/install; give it a row in the part with "
            'role = "generated" (1. Generated by the installer)'
        )


def test_internal_keys_are_not_offered_in_env_example() -> None:
    internal = role_keys("internal")
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


def test_backend_ci_runs_when_a_file_read_here_changes() -> None:
    """A PR that edits only deploy/env-reference.toml, .env.example, the two
    scripts, a file the reference links to, or bin/dev (which
    test_log_stream.py runs) still runs these tests. The Go sources are
    left out: their own CI covers them, and every push to dev runs this suite."""
    workflow = yaml.safe_load(BACKEND_CI.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["changes"]["steps"]
    filters = next(step for step in steps if step.get("id") == "filter")["with"]
    patterns = yaml.safe_load(filters["filters"])["backend"]
    names = [*COMPOSE_FILES, *WORD_READER_FILES]
    names += [
        str(path.relative_to(ROOT))
        for path in (DATA, ENV_EXAMPLE, SCRIPT, Path(docs_site.__file__))
    ]
    names += [
        str(path.relative_to(ROOT))
        for directory in WORD_READER_DIRS
        for path in (ROOT / directory).iterdir()
    ]
    names += sorted(
        {
            target.removeprefix("repo:").partition("#")[0]
            for _, text in docs_site.reference_texts(reference())
            for target in docs_site.links(str(text))
            if target.startswith("repo:")
        }
    )
    missed = sorted(
        name
        for name in names
        if not any(fnmatchcase(name, pattern) for pattern in patterns)
    )
    assert not missed, (
        f"add these to the backend paths filter in {BACKEND_CI.name}: {missed}"
    )


# --------------------------------------------------------------------------
# The reference data renders as a sound docs page
# --------------------------------------------------------------------------


def test_the_reference_data_is_sound() -> None:
    """Setup codes, fields, MDX-safe prose, links, headings, links into the
    page from this repository, and a deterministic render: see
    env_reference.validate()."""
    problems = env_reference.validate(reference())
    assert not problems, "\n".join(problems)


def _broken(change) -> list[str]:
    data = copy.deepcopy(reference())
    change(data)
    return env_reference.validate(data)


def _first_row(data: dict) -> dict:
    return data["part"][0]["section"][0]["row"][0]


def _set(target: dict, key: str, value):
    target[key] = value


@pytest.mark.parametrize(
    "change, expected",
    [
        (lambda d: _set(_first_row(d), "setups", ["S", "X"]), "unknown setup 'X'"),
        (
            lambda d: _set(_first_row(d), "default", {"S": "a", "Q": "b"}),
            "default for unknown setup 'Q'",
        ),
        (lambda d: _set(_first_row(d), "keys", []), "a row needs keys or a label"),
        (lambda d: _set(_first_row(d), "keys", ["lower_case"]), "KEY_NAMEs"),
        (lambda d: _set(_first_row(d), "text", "a \u2014 b"), "em dash"),
        (lambda d: _set(_first_row(d), "text", "see <http://x>"), "autolink"),
        (lambda d: _set(_first_row(d), "text", "a {b} c"), "outside code breaks MDX"),
        (
            lambda d: _set(_first_row(d), "text", "[x](repo:no/such/file.md)"),
            "no such file",
        ),
        (
            lambda d: _set(
                _first_row(d), "text", "[x](repo:INSTALLATION.md#no-such-heading)"
            ),
            "has no heading with that anchor",
        ),
        (
            lambda d: _set(_first_row(d), "text", "[x](#no-such-heading)"),
            "not a heading",
        ),
        (
            lambda d: _set(_first_row(d), "text", "[x](../INSTALLATION.md)"),
            "use /docs/",
        ),
        (lambda d: _set(_first_row(d), "surprise", 1), "unknown field 'surprise'"),
        (
            lambda d: _set(d["part"][1], "heading", d["part"][0]["heading"]),
            "share the anchor",
        ),
        (lambda d: _set(d["part"][0], "role", "other"), "role = 'generated'"),
        (lambda d: _set(d, "title", 'a "b"'), "no double quotes"),
    ],
)
def test_validate_catches_broken_reference_data(change, expected: str) -> None:
    problems = _broken(change)
    assert any(expected in problem for problem in problems), problems


def test_the_page_shows_public_rows_only() -> None:
    page = docs_site.render_reference(reference())
    for row in env_reference.rows(reference()):
        cell = docs_site.key_cell(row)
        if docs_site.is_public(row):
            assert f"| {cell} |" in page, cell
        else:
            assert cell not in page, cell
