"""Check offline configuration and destination attribution without network access."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile


EXPECTED = {
    "NO_GCE_CHECK": "True",
    "GCE_METADATA_HOST": "127.0.0.1:9",
    "GCE_METADATA_IP": "127.0.0.1:9",
    "AWS_EC2_METADATA_DISABLED": "true",
    "CODE_EXECUTOR_URL": "",
    "LITELLM_LOCAL_MODEL_COST_MAP": "True",
}
ASSETS = (
    "fonts.googleapis.com",
    "api.iconify.design",
    "api.simplesvg.com",
    "api.unisvg.com",
)


def environment_control(generator, root):
    names = ("backend", "worker", "worker-exact-aggregation",
             "postgres-schema-bootstrap", "one-shot")
    source = root / "base.json"
    source.write_text(json.dumps({"services": {
        name: {"image": "futureagi/future-agi:e2e-local"} for name in names
    }}))
    output = root / "guarded.json"
    subprocess.run([sys.executable, "-I", str(generator), str(source),
                    str(root / "kit"), str(root / "tools"), str(output)], check=True)
    services = json.loads(output.read_text())["services"]
    missing = {name: [key for key, value in EXPECTED.items()
                      if services[name]["environment"].get(key) != value]
               for name in (*names, "editor-egress-recorder")}
    missing = {name: keys for name, keys in missing.items() if keys}
    print(json.dumps({"status": "FAIL" if missing else "PASS",
                      "services": len(services), "missingSwitchNames": missing}))
    return not missing


def audit_control(auditor, root):
    records = root / "events"
    records.mkdir()
    installed = {"kind": "installed", "layer": "browser.route",
                 "hostname": "localhost", "port": 0}
    checks = []
    for host, layer, mode, expected in [
        *((host, "browser.route", "browser-pre-assets", 0) for host in ASSETS),
        ("api.iconify.design.invalid", "browser.route", "browser-pre-assets", 1),
        ("unclassified.invalid", "browser.route", "browser-pre-assets", 1),
        ("fonts.googleapis.com", "socket.connect", "browser-pre-assets", 1),
        ("fonts.googleapis.com", "browser.route", "runtime", 1),
    ]:
        (records / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in [
            installed, {"kind": "refused", "layer": layer, "hostname": host, "port": 443}
        ]))
        run = subprocess.run([sys.executable, "-I", str(auditor), str(records), mode],
                             capture_output=True, text=True)
        checks.append((run.returncode == 0) == (expected == 0))
    print(json.dumps({"status": "PASS" if all(checks) else "FAIL",
                      "attributionControls": len(checks), "passed": sum(checks)}))
    return all(checks)


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    if len(sys.argv) == 2:
        success = environment_control(Path(sys.argv[1]), root)
    else:
        success = environment_control(Path(__file__).with_name("compose-guards.py"), root)
        success = audit_control(Path(__file__).with_name("audit-events.py"), root) and success
    raise SystemExit(0 if success else 1)
