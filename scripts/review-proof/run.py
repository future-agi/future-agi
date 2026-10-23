#!/usr/bin/env python3
"""Run the seven unchanged managed flows, one-sided mutations, then restored flows.

This script lives only on the validation branch. Product images and original
specs must match source.json. Mutations run in a separate harness copy, and a
nonzero Playwright exit is evidence only if check.py matches the exact assertion.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
TOOLS = Path(__file__).resolve().parent
OUT = ROOT / "proof-output"
SPECS = [
    "flows/evals/jev-evaluator-models.spec.ts",
    "flows/observe/instant-eval-insights-gates.spec.ts",
]
MUTATIONS = TOOLS / "mutations.json"
PYTHON = [sys.executable, "-I"]


def command(args, *, cwd=ROOT):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def tool(name, *args):
    subprocess.run([*PYTHON, str(TOOLS / name), *map(str, args)], check=True)


def provenance():
    source = json.loads((TOOLS / "source.json").read_text())
    actual = {name: command(["git", "rev-parse", f"HEAD:{name}"])
              for name in source["trees"]}
    if actual != source["trees"]:
        raise RuntimeError("Product or original E2E source differs from the pinned product commit")
    images = {}
    for name in ("future-agi", "frontend", "fi-collector", "agentcc-gateway"):
        images[name] = command([
            "docker", "image", "inspect", f"futureagi/{name}:e2e-ci",
            "--format", "{{.Id}}",
        ])
    services = {
        "backend": "future-agi", "worker": "future-agi",
        "worker-exact-aggregation": "future-agi", "frontend": "frontend",
        "fi-collector": "fi-collector", "fi-property-catalog-consumer": "fi-collector",
        "agentcc-gateway": "agentcc-gateway",
    }
    running = {}
    for service, image in services.items():
        ids = command(["bin/e2e", "ps", "-q", service]).splitlines()
        if not ids:
            raise RuntimeError(f"No running container for {service}")
        running[service] = [command([
            "docker", "inspect", "--format", "{{.Image}}", cid,
        ]) for cid in ids]
        if any(got != images[image] for got in running[service]):
            raise RuntimeError(f"{service} is not using the image just built from source")
    result = {**source, "proof_commit": command(["git", "rev-parse", "HEAD"]),
              "actual_trees": actual, "built_images": images, "running_images": running}
    (OUT / "provenance.json").write_text(json.dumps(result, indent=2) + "\n")
    tool("mutate.py", "verify", MUTATIONS, ROOT / "e2e")
    return result


def play(args, cwd, env, logfile):
    with logfile.open("w") as log:
        process = subprocess.Popen(args, cwd=cwd, env=env, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            return process.wait(timeout=900)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            return 124


def run_round(name, *, shadow=None):
    directory = OUT / name
    directory.mkdir()
    target = shadow or ROOT / "e2e"
    env = dict(os.environ, CI="true", PLAYWRIGHT_HTML_OPEN="never",
               PLAYWRIGHT_JSON_OUTPUT_FILE=str(directory / "report.json"),
               PLAYWRIGHT_HTML_OUTPUT_DIR=str(directory / "html"))
    if shadow:
        tool("mutate.py", "apply", MUTATIONS, shadow, ROOT / "e2e", name,
             directory / "mutation.diff")
    only_one = name in ("P4", "P5")
    args = (["yarn", "playwright", "test"] if shadow else [str(ROOT / "bin/e2e"), "test"])
    args += [*SPECS, "--workers=2", "--reporter=list,json,html",
             f"--output={directory / 'test-results'}"]
    if only_one:
        args += ["--grep", "EVAL-E2E-006"]
    (directory / "command.json").write_text(json.dumps({"argv": args, "cwd": str(target if shadow else ROOT)}, indent=2))
    print(f"START {name}", flush=True)
    try:
        code = play(args, target if shadow else ROOT, env, directory / "list.log")
    finally:
        if shadow:
            tool("mutate.py", "restore", MUTATIONS, shadow, ROOT / "e2e")
    expected_code = 1 if shadow else 0
    report = directory / "report.json"
    if report.exists():
        checked = subprocess.run([
            *PYTHON, str(TOOLS / "check.py"), str(MUTATIONS), str(report), name,
            str(directory / "verdict.json"), "1" if only_one else "7",
        ]).returncode
    else:
        checked = 1
    ok = code == expected_code and checked == 0
    result = {"run": name, "ok": ok, "exit_code": code,
              "expected_exit_code": expected_code, "checker_exit_code": checked}
    (directory / "process.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"FINISH {name}: {'PASS' if ok else 'FAIL'} (Playwright exit {code})", flush=True)
    return result


def main():
    if os.environ.get("E2E_STACK", "distributed") != "distributed":
        raise SystemExit("review proof requires E2E_STACK=distributed")
    OUT.mkdir(exist_ok=False)
    source = provenance()
    results = []
    shadow = OUT / "shadow" / "e2e"
    try:
        first = run_round("G0")
        results.append(first)
        if not first["ok"]:
            return 1
        shutil.copytree(ROOT / "e2e", shadow,
                        ignore=shutil.ignore_patterns("node_modules", "test-results", "playwright-report", "blob-report"))
        (shadow / "node_modules").symlink_to(ROOT / "e2e/node_modules", target_is_directory=True)
        for name in ("P1", "P2", "P3", "P4", "P5", "P6", "P7"):
            results.append(run_round(name, shadow=shadow))
        tool("mutate.py", "verify", MUTATIONS, ROOT / "e2e")
        tool("mutate.py", "verify", MUTATIONS, shadow)
        results.append(run_round("G1"))
        return 0 if all(r["ok"] for r in results) else 1
    finally:
        if shadow.exists():
            # Only this invocation's disposable harness copy is removed.
            shutil.rmtree(shadow.parent)
        (OUT / "summary.json").write_text(json.dumps({
            "product_commit": source["product_commit"],
            "proof_commit": source["proof_commit"], "runs": results,
            "ok": len(results) == 9 and all(r["ok"] for r in results),
        }, indent=2) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
