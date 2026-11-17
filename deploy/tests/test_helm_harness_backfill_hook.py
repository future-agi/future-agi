"""The harness versions backfill hook Job, as `helm template` renders it.

No cluster, credentials or vendor contact: the chart's own bundled example,
and a stand-in `python` for running the Job's script on this host.
"""

import shutil
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy/helm/futureagi"
# The operator one-shot authorization (model_hub/apps.py).
OPERATOR_PAIR = {"SERVICE_TYPE": "bootstrap", "STARTUP_DB_MUTATION_MODE": "operator"}
STATE_LINE = (
    "Harness versions backfill: state={} total=3 processed=3 succeeded={} "
    "failed={} skipped=0 busy_organizations=0"
)


def render():
    assert shutil.which("helm"), "helm required for the backfill hook render"
    result = subprocess.run(
        ["helm", "template", "futureagi", str(CHART), "--namespace", "futureagi",
         "-f", str(CHART / "examples/bundled.yaml"),
         "--set", "secrets.allowGeneratedKeysWithoutCluster=true"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def test_backfill_hook_runs_after_bootstrap_as_the_operator_one_shot_and_never_fails(tmp_path):
    jobs = {
        doc["metadata"]["labels"]["app.kubernetes.io/component"]: doc
        for doc in render()
        if doc["kind"] == "Job"
    }
    bootstrap, backfill = jobs["bootstrap"], jobs["harness-backfill"]
    hooks = backfill["metadata"]["annotations"]
    assert {hook.strip() for hook in hooks["helm.sh/hook"].split(",")} == {
        "post-install", "post-upgrade",
    }
    assert int(hooks["helm.sh/hook-weight"]) > int(
        bootstrap["metadata"]["annotations"]["helm.sh/hook-weight"]
    )
    assert hooks["argocd.argoproj.io/hook"] == "PostSync"

    spec = backfill["spec"]["template"]["spec"]
    boot = bootstrap["spec"]["template"]["spec"]
    (container,) = spec["containers"]
    (boot_container,) = boot["containers"]
    # The bootstrap job's image, identity and security context.
    assert container["image"] == boot_container["image"]
    assert spec["serviceAccountName"] == boot["serviceAccountName"]
    assert spec["securityContext"] == boot["securityContext"]
    assert container["securityContext"] == boot_container["securityContext"]
    env = {entry["name"]: entry.get("value") for entry in container["env"]}
    for name, value in OPERATOR_PAIR.items():
        assert env.get(name) == value, name

    command = container["command"] + container.get("args", [])
    assert command[:2] == ["sh", "-c"]
    assert "python manage.py harness_versions_backfill --auto" in command[2]

    # The rendered script under a stand-in python: whatever the backfill
    # prints or returns, the Job exits 0, so it never fails the release.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python = bin_dir / "python"
    python.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" > "$BACKFILL_ARGV"\n'
        '[ -z "$BACKFILL_LINE" ] || printf \'%s\\n\' "$BACKFILL_LINE"\n'
        'exit "$BACKFILL_RC"\n',
        encoding="utf-8",
    )
    python.chmod(0o755)
    argv = tmp_path / "argv"
    for rc, line in (
        (0, STATE_LINE.format("complete", 3, 0)),
        (0, STATE_LINE.format("off", 0, 0)),
        (1, STATE_LINE.format("failed", 2, 1)),
        (1, ""),
        (137, ""),
    ):
        result = subprocess.run(
            command,
            cwd=tmp_path,
            env={
                "PATH": f"{bin_dir}:/usr/bin:/bin",
                "HOME": str(tmp_path),
                "BACKFILL_ARGV": str(argv),
                "BACKFILL_LINE": line,
                "BACKFILL_RC": str(rc),
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        where = f"rc={rc} line={line!r}"
        assert result.returncode == 0, where
        assert argv.read_text(encoding="utf-8") == "manage.py harness_versions_backfill --auto\n", where
        if line:
            assert line in result.stdout, where
        if rc:
            assert f"exited {rc}" in result.stdout, where
        else:
            assert "WARNING" not in result.stdout + result.stderr, where
