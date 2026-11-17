"""Runtime credential boundaries; execute only in the lead's isolated test venue.

Children capture all output, and assertions expose counts/booleans, never values.
No Docker daemon, Node runtime or application settings are needed.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FERNET_TOKEN = re.compile(rb"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{43}=(?![A-Za-z0-9_=-])")
PROJECT = "credential-infoflow"
PUBLIC_ROOT_VARIABLES = (
    "OUT",
    "E2E_VALUE_FREE_PUBLISH",
    "PLAYWRIGHT_HTML_OUTPUT_DIR",
    "PLAYWRIGHT_JSON_OUTPUT_DIR",
    "PLAYWRIGHT_BLOB_OUTPUT_DIR",
    "E2E_RUNTIME_PUBLIC_ROOTS",
)


def _script(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o700)


@pytest.fixture
def sandbox(tmp_path):
    checkout = tmp_path / "checkout"
    library = checkout / "bin/lib"
    library.mkdir(parents=True)
    for relative in ("bin/e2e", "bin/lib/e2e-runtime-credentials.sh"):
        shutil.copy2(ROOT / relative, checkout / relative)
    (library / "build-local.sh").write_text("# No build operations in this fixture.\n")
    tools = tmp_path / "tools"
    tools.mkdir()
    _script(tools / "node", "#!/bin/sh\nprintf 'E2E_TEST_NODE=1\\n'\n")
    _script(
        tools / "docker",
        "#!/bin/sh\n"
        ': > "$DOCKER_CALLED"\n'
        'if [ "${E2E_TEST_RENDER:-}" = 1 ]; then\n'
        "  printf 'INTEGRATION_ENCRYPTION_KEY=%s\\nWEBHOOKS_ENCRYPTION_KEYS=%s\\n' "
        '"${INTEGRATION_ENCRYPTION_KEY:-}" "${WEBHOOKS_ENCRYPTION_KEYS:-}"\n'
        "else\n"
        '  test -n "${INTEGRATION_ENCRYPTION_KEY:-}" && '
        'test -n "${WEBHOOKS_ENCRYPTION_KEYS:-}" || exit 1\n'
        "  printf 'runtime-keys-present=1\\n'\n"
        "fi\n",
    )
    # Unsafe destinations must be refused before reaching generation. This stub
    # never generates a key, and lets the RED implementation fail value-free.
    _script(
        tools / "openssl",
        '#!/bin/sh\n: > "$GENERATOR_CALLED"\nprintf "invalid-test-material\\n"\n',
    )
    home = tmp_path / "home"
    home.mkdir()
    cache = tmp_path / "private-cache"
    env = {
        "PATH": str(tools) + os.pathsep + os.defpath,
        "HOME": str(home),
        "XDG_CACHE_HOME": str(cache),
        "E2E_COMPOSE_PROJECT": PROJECT,
        "DOCKER_CALLED": str(tmp_path / "docker-called"),
        "GENERATOR_CALLED": str(tmp_path / "generator-called"),
    }
    return checkout, cache, env


def _seed_cache(cache: Path, *, invalid=False) -> Path:
    directory = cache / "futureagi-e2e" / PROJECT / "credentials"
    directory.mkdir(parents=True, mode=0o700)
    # Generated only when this test is run, never at collection or authoring time.
    keys = [base64.urlsafe_b64encode(os.urandom(32)) for _ in range(2)]
    if invalid:
        keys[0] = b"invalid:" + keys[0]
    path = directory / "fernet-keys"
    path.write_bytes(b"\n".join(keys) + b"\n")
    path.chmod(0o600)
    return path


def _run(checkout, env, argv):
    return subprocess.run(
        argv, cwd=checkout, env=env, capture_output=True, check=False, timeout=15
    )


def _no_disclosures(result):
    counts = tuple(len(FERNET_TOKEN.findall(stream)) for stream in (result.stdout, result.stderr))
    assert counts == (0, 0), "Credential-shaped output counts must both be zero"


@pytest.mark.parametrize(
    "arguments",
    [
        ("config",),
        ("config", "--format", "json"),
        ("config", "--format=json"),
        ("config", "--environment"),
        ("convert",),
        ("--ansi", "never", "config"),
        ("--profile=test", "convert", "--format", "json"),
        ("--dry-run", "up"),
        ("--dry-run=true", "up"),
        ("up", "--dry-run"),
    ],
)
@pytest.mark.parametrize("stack", ["distributed", "standalone"])
def test_compose_rendering_is_refused_before_docker(sandbox, arguments, stack):
    checkout, cache, env = sandbox
    env.update(E2E_TEST_RENDER="1", E2E_STACK=stack)
    _seed_cache(cache)
    # Standalone uses a distinct fixed project, just like the real runner.
    if stack == "standalone":
        shutil.copytree(
            cache / "futureagi-e2e" / PROJECT,
            cache / "futureagi-e2e" / "futureagi-e2e-standalone",
        )
    result = _run(checkout, env, ["bash", "bin/e2e", "compose", *arguments])
    _no_disclosures(result)
    assert result.returncode != 0
    assert b"bin/e2e ps" in result.stderr, "Refusal must name the value-free alternative"
    assert not Path(env["DOCKER_CALLED"]).exists()


def test_operational_compose_command_still_receives_keys(sandbox):
    checkout, cache, env = sandbox
    _seed_cache(cache)
    result = _run(checkout, env, ["bash", "-x", "bin/e2e", "compose", "ps"])
    _no_disclosures(result)
    assert result.returncode == 0
    assert b"runtime-keys-present=1" in result.stdout
    assert Path(env["DOCKER_CALLED"]).exists()


@pytest.mark.parametrize("traced", [False, True])
@pytest.mark.parametrize("failure", ["none", "invalid-cache", "invalid-project", "missing-home"])
@pytest.mark.parametrize("overrides", [False, True])
def test_helper_preserves_trace_state_without_disclosure(sandbox, traced, failure, overrides):
    checkout, cache, env = sandbox
    _seed_cache(cache, invalid=failure == "invalid-cache")
    if overrides:
        for name in ("INTEGRATION_ENCRYPTION_KEY", "WEBHOOKS_ENCRYPTION_KEYS"):
            env[name] = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
    if failure == "missing-home":
        env.pop("HOME")
        env.pop("XDG_CACHE_HOME")
    project = "invalid/project" if failure == "invalid-project" else PROJECT
    program = (
        '. "$1"\n'
        'if prepare_runtime_credentials "$2"; then rc=0; else rc=$?; fi\n'
        'case $- in *x*) traced=1;; *) traced=0;; esac\n'
        'set +x\nprintf "trace=%s\\nstatus=%s\\n" "$traced" "$rc"\n'
        'test -n "${INTEGRATION_ENCRYPTION_KEY:-}" && '
        'test -n "${WEBHOOKS_ENCRYPTION_KEYS:-}" && printf "keys-present=1\\n"\n'
        "exit 0\n"
    )
    result = _run(
        checkout,
        env,
        [
            "bash",
            *(["-x"] if traced else []),
            "-c",
            program,
            "credential-check",
            str(checkout / "bin/lib/e2e-runtime-credentials.sh"),
            project,
        ],
    )
    _no_disclosures(result)
    assert result.returncode == 0, "Helper failure must return control to the caller"
    assert (b"trace=1" in result.stdout) == traced
    assert (b"status=0" in result.stdout) == (failure == "none")
    if failure == "none":
        assert b"keys-present=1" in result.stdout


@pytest.mark.parametrize(
    "destination",
    ["relative", "checkout", "symlink-checkout", "symlink-project", "checkout-link-out"],
)
def test_unsafe_cache_destinations_are_rejected_before_generation(sandbox, destination):
    checkout, cache, env = sandbox
    if destination == "relative":
        env["XDG_CACHE_HOME"] = ".cache"
    elif destination == "checkout":
        env["XDG_CACHE_HOME"] = str(checkout / ".cache")
    elif destination == "symlink-checkout":
        cache.symlink_to(checkout, target_is_directory=True)
    elif destination == "symlink-project":
        (cache / "futureagi-e2e").mkdir(parents=True)
        (cache / "futureagi-e2e" / PROJECT).symlink_to(checkout, target_is_directory=True)
    else:
        cache.mkdir()
        (checkout / "cache-link").symlink_to(cache, target_is_directory=True)
        env["XDG_CACHE_HOME"] = str(checkout / "cache-link")
    _assert_cache_refused(checkout, env)


def _assert_cache_refused(checkout, env):
    result = _run(
        checkout,
        env,
        [
            "bash",
            "-c",
            '. "$1"; prepare_runtime_credentials "$2"',
            "cache-check",
            str(checkout / "bin/lib/e2e-runtime-credentials.sh"),
            PROJECT,
        ],
    )
    _no_disclosures(result)
    assert result.returncode != 0
    assert not Path(env["GENERATOR_CALLED"]).exists(), "Refuse before generating any material"
    assert not any(checkout.rglob("fernet-keys")), "No credential files may land in the checkout"


@pytest.mark.parametrize("variable", PUBLIC_ROOT_VARIABLES)
@pytest.mark.parametrize("symlink", [False, True])
def test_configured_public_roots_are_rejected(sandbox, variable, symlink):
    checkout, cache, env = sandbox
    public = checkout.parent / "published"
    public.mkdir()
    env[variable] = str(public)
    if symlink:
        cache.symlink_to(public, target_is_directory=True)
    else:
        env["XDG_CACHE_HOME"] = str(public / "cache")
    _assert_cache_refused(checkout, env)
    assert not any(public.rglob("fernet-keys"))


def test_additional_mount_roots_check_every_declared_path(sandbox):
    checkout, cache, env = sandbox
    public = checkout.parent / "mounted"
    env["E2E_RUNTIME_PUBLIC_ROOTS"] = str(checkout.parent / "other") + "\n" + str(public)
    env["XDG_CACHE_HOME"] = str(public / "cache")
    _assert_cache_refused(checkout, env)
    assert not public.exists(), "Reject a planned public root before creating it"


def test_public_symlink_spelling_cannot_hide_an_outward_cache_link(sandbox):
    checkout, cache, env = sandbox
    public = checkout.parent / "published"
    public.mkdir()
    alias = checkout.parent / "published-alias"
    alias.symlink_to(public, target_is_directory=True)
    cache.mkdir()
    (public / "cache-link").symlink_to(cache, target_is_directory=True)
    env["OUT"] = str(alias)
    env["XDG_CACHE_HOME"] = str(alias / "cache-link")
    _assert_cache_refused(checkout, env)


def test_credential_filename_is_ignored_at_any_depth():
    # The cloud venue may receive an archive rather than a .git checkout. Bare
    # filename rules apply at every depth; no Git executable is needed here.
    rules = set((ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())
    checks = tuple(rule in rules for rule in ("fernet-keys", ".fernet-keys.*"))
    assert checks == (True, True)
