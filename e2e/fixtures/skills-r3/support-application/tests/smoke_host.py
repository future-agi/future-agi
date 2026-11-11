"""Cloud-only host for the packaged module smoke; not a native qualification relay."""

import json
import os
import subprocess
import sys
from pathlib import Path

from ctxhub_support.business import Business, server
from support import BINDING, RUNTIME, TOKEN, FixtureRegistry, serving


def main():
    if sys.platform != "linux":
        raise RuntimeError("Run application tests only on the assigned cloud VM")
    output = Path(os.environ["CTXHUB_SMOKE_OUTPUT"])
    output.mkdir(parents=True, exist_ok=False)
    request = {
        "schema": 1,
        "thread": "synthetic-native-smoke",
        "case": {
            "case_id": "refund-boundary",
            "order_id": "synthetic-smoke-order",
            "request": "Refund 25 units for an order purchased 30 days ago.",
        },
    }
    (output / "request.json").write_text(json.dumps(request))
    fixture = FixtureRegistry()
    # Native routing exercises all 15 text members. Binary-intake rejection is
    # tested separately with libraryFixture()'s default unsupported member.
    assert not any(skill["unsupported"] for skill in fixture.catalog["skills"])
    business = Business(output / "business.sqlite")
    business.seed(request["case"]["order_id"])
    with (
        serving(fixture.server()) as registry_url,
        serving(server(business)) as business_url,
    ):
        env = {
            **os.environ,
            "CTXHUB_REGISTRY_URL": registry_url,
            "CTXHUB_BUSINESS_URL": business_url,
            "CTXHUB_BINDING_ID": BINDING,
            "CTXHUB_CONTEXT_CREDENTIAL": TOKEN,
            "CTXHUB_RUNTIME_DIGEST": RUNTIME,
        }
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "fi.context.adapters.simulation_entrypoint",
                str(output / "request.json"),
                "--state",
                str(output / "state.sqlite"),
                "--output",
                str(output / "application.json"),
            ],
            check=False,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Packaged application smoke failed ({proc.returncode}): {proc.stderr}"
            )
        result = json.loads((output / "application.json").read_text())
        ledger = business.ledger()
        assert result["status"] == "completed", result
        assert ledger["request_count"] == 1 and len(ledger["operations"]) == 1
        assert (
            ledger["operations"][0]["status"] == "applied"
            and ledger["operations"][0]["amount"] == 25
        )
        assert result["selected"] == ["00000000-0000-4000-8000-000000000001"]
        assert not any("resolve" in row["path"] for row in fixture.requests)
        receipt = {
            "schema": 1,
            "evidence_class": "synthetic fixture",
            "status": "passed",
            "launch": "packaged_module_on_gcp_vm",
            "native_qualified": False,
            "registry": "synthetic_http_fixture_via_real_sdk",
            "provider": "Skills ProgrammableProvider",
            "catalog_intake_members": 15,
            "catalog_executable_members": 15,
            "fixture_mode": "libraryFixture({unsupportedMember:false}): text-only native routing",
            "fixture_sha256": fixture.catalog["fixture_sha256"],
            "unsupported_members": [],
            "business_ledger": ledger,
            "application": result,
        }
        (output / "smoke-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print(
            json.dumps(
                {"status": "passed", "native_qualified": False, "business_effects": 1}
            )
        )


if __name__ == "__main__":
    main()
