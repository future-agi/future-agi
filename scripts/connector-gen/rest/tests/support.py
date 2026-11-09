"""Test support: the example catalog and the pinned vendor specs.

tests/fixtures/catalog is a catalog in the layout the gateway's loader
reads, minus the spec bytes: each tools.yaml names its spec as
fixtures/spec/<file>, and materialize() copies that file from
tests/fixtures/specs (where SOURCES.json pins it) into place. The committed
tools.generated.json files are the reviewed output; regenerate them after
an intended change with

    python3 scripts/connector-gen/rest/tests/support.py --write-goldens
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from restgen import cli  # noqa: E402
from restgen.catalog import GENERATED, OVERLAY  # noqa: E402
from restgen.yamlio import load_yaml  # noqa: E402

FIXTURES = HERE / "fixtures"
CATALOG = FIXTURES / "catalog"
SPECS = FIXTURES / "specs"
SOURCES = json.loads((SPECS / "SOURCES.json").read_text(encoding="utf-8"))


def source(file: str) -> dict:
    """The SOURCES.json entry of one stored spec."""
    for entry in SOURCES["specs"]:
        if entry["file"] == file:
            return entry
    raise KeyError(file)


def overlay_paths(catalog: Path) -> list[Path]:
    return sorted(catalog.glob(f"apps/*/providers/*/{OVERLAY}"))


def place_specs(catalog: Path) -> None:
    """Copy each overlay's spec from tests/fixtures/specs into its provider."""
    for overlay in overlay_paths(catalog):
        spec = load_yaml(overlay.read_text(encoding="utf-8"))["spec"]
        target = overlay.parent / spec
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SPECS / Path(spec).name, target)


def materialize(dst: Path, apps: list[str] | None = None) -> Path:
    """A runnable copy of the example catalog (only apps, when given)."""
    catalog = dst / "catalog"
    if apps is None:
        shutil.copytree(CATALOG, catalog)
    else:
        for app in apps:
            shutil.copytree(CATALOG / "apps" / app, catalog / "apps" / app)
    place_specs(catalog)
    return catalog


def run(*args: str) -> tuple[int, str, str]:
    """generate.py's main with args: (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


def write_goldens() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        catalog = materialize(Path(tmp))
        code, out, err = run("--catalog", str(catalog))
        print(out + err, end="")
        if code != 0:
            return code
        for generated in sorted(catalog.glob(f"apps/*/providers/*/{GENERATED}")):
            rel = generated.relative_to(catalog)
            shutil.copyfile(generated, CATALOG / rel)
            print(f"golden   {rel}")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] != ["--write-goldens"]:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(write_goldens())
