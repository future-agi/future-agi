#!/usr/bin/env python3
"""Regenerate (or --check) only model-prices-v1 synthetic contract fixtures.

Use futureagi/.venv/bin/python after uv sync --locked, with the gateway's Go
modules prepared. The Go projection test runs in the already-cached
node:22-alpine image with --network none; it never reads the bundled catalogue.
No schema reference may fetch a network resource. No existing generator runs.
"""

import argparse
import base64
import copy
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib

import jsonschema

GATEWAY = Path(__file__).resolve().parents[2]
CONTRACT = GATEWAY / "internal" / "pricesfeed"


def projected_fixture():
    # Keep Docker bind sources out of the job's potentially private /tmp.
    with tempfile.TemporaryDirectory(prefix="model-prices-fixture-", dir=GATEWAY) as work:
        work = Path(work)
        env = dict(os.environ, CGO_ENABLED="0", GOOS="linux", GOPROXY="off")
        subprocess.run(
            ["go", "test", "-vet=off", "-c", "-o", str(work / "pricesfeed.test"), "./internal/pricesfeed"],
            cwd=GATEWAY, env=env, check=True,
        )
        work.chmod(0o755)
        result = subprocess.run(
            ["docker", "run", "--rm", "--pull=never", "--network", "none",
             "--user", f"{os.getuid()}:{os.getgid()}", "-v", f"{work}:/gate:ro",
             "-e", "MODEL_PRICES_FIXTURE_STDOUT=1",
             "--entrypoint", "/gate/pricesfeed.test", "node:22-alpine",
             "-test.run=^TestModelPricesFixture$", "-test.v", "-test.count=1"],
            check=True, stdout=subprocess.PIPE,
        )
        prefix = b"MODEL_PRICES_FIXTURE:"
        rows = [line[len(prefix):] for line in result.stdout.splitlines() if line.startswith(prefix)]
        if len(rows) != 1:
            raise SystemExit("projection did not emit exactly one synthetic fixture")
        return base64.b64decode(rows[0], validate=True)


def invalid_cases(valid):
    cases = []

    def case(name, mutate):
        doc = copy.deepcopy(valid)
        mutate(doc)
        cases.append({"name": name, "document": doc})

    case("wrong_version", lambda d: d.update(schema_version="models-feed-v1"))
    case("unknown_private_field", lambda d: d.update(tenant_secret="synthetic-only"))
    case("negative_rate", lambda d: d["offers"][0]["dimensions"][0].update(provider_rate_usd="-1"))
    case("missing_dimension", lambda d: d["offers"][0]["dimensions"].pop())
    case("unknown_dimension", lambda d: d["offers"][0]["dimensions"][0].update(name="generation"))
    case("wrong_unit", lambda d: d["offers"][0]["dimensions"][0].update(unit="image"))
    case("unquoted_prompt", lambda d: d["offers"][0]["dimensions"][0].update(provider_rate_usd=None))
    case("byok_fee", lambda d: d.update(byok_gateway_fee_bps=500))
    case("missing_rounding_contract", lambda d: d["rules"].pop("settlement_rounding"))
    case("unsupported_fixed_image", lambda d: d["offers"][0].update(image_price_usd="0.1"))
    return {"cases": cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    lock = tomllib.loads((GATEWAY.parent / "futureagi" / "uv.lock").read_text())
    pinned = next(p["version"] for p in lock["package"] if p["name"] == "jsonschema")
    installed = importlib.metadata.version("jsonschema")
    if installed != pinned:
        raise SystemExit(f"jsonschema {installed} is not uv.lock's {pinned}; use the locked Python environment")
    schema = json.loads((CONTRACT / "schema" / "model-prices-v1.schema.json").read_text())

    def local_refs(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "$ref" and not item.startswith("#"):
                    raise SystemExit("external schema references are forbidden")
                local_refs(item)
        elif isinstance(value, list):
            for item in value:
                local_refs(item)

    local_refs(schema)
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    projected = projected_fixture()
    valid = json.loads(projected)
    validator.validate(valid)
    invalid = invalid_cases(valid)
    for case in invalid["cases"]:
        if not list(validator.iter_errors(case["document"])):
            raise SystemExit(f"invalid fixture accepted: {case['name']}")
    expected = {
        "model-prices-v1.json": projected,
        "invalid-model-prices-v1.json": (json.dumps(invalid, indent=2, ensure_ascii=False) + "\n").encode(),
    }
    for name, content in expected.items():
        path = CONTRACT / "testdata" / name
        if args.check:
            if not path.exists() or path.read_bytes() != content:
                raise SystemExit(f"fixture byte drift: {path}; run this script without --check")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    print(f"model-prices-v1: schema valid, 1 valid fixture, {len(invalid['cases'])} rejected invalid fixtures; "
          + ("no byte differences" if args.check else "fixtures regenerated"))


if __name__ == "__main__":
    main()
