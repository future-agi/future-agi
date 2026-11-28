"""Share the HTTP/SDK harness fixture with the focused recovery regressions."""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

pytest_plugins = ("test_application",)

# The single source of the synthetic catalog is the e2e library fixture module.
LIBRARY = Path(__file__).resolve().parents[2] / "library.mjs"
EXPORT = (
    "const { libraryFixture } = await import(process.argv[1]);"
    "process.stdout.write(JSON.stringify(libraryFixture()));"
)


def pytest_configure(config):
    """Materialise the default intake catalog when the caller did not supply one.

    The suite expects libraryFixture()'s default (15 members, one unsupported
    binary asset). An explicit CTXHUB_CATALOG is respected unchanged.
    """
    if os.environ.get("CTXHUB_CATALOG"):
        return
    node = shutil.which("node")
    if node is None or not LIBRARY.is_file():
        raise RuntimeError(
            "CTXHUB_CATALOG is unset and the catalog cannot be generated: "
            f"need node on PATH and {LIBRARY}"
        )
    produced = subprocess.run(
        [node, "--input-type=module", "-e", EXPORT, "--", LIBRARY.as_uri()],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    catalog = json.loads(produced.stdout)
    directory = Path(tempfile.mkdtemp(prefix="ctxhub-catalog-"))
    target = directory / "catalog.json"
    target.write_text(json.dumps(catalog))
    os.environ["CTXHUB_CATALOG"] = str(target)
