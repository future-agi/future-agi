"""R10/R13, AC13 static and object ownership checks (no live egress)."""

import ast
from pathlib import Path

import pytest

from ee.voice.services.audio_analysis.errors import AudioProvenanceError
from ee.voice.services.audio_provenance import validate_object_key


@pytest.mark.parametrize(
    "key",
    [
        "call-recordings/project/vapi/other/a.wav",
        "https://host/a.wav",
        "//foreign/a",
        "call-recordings/project/../call/a.wav",
        "call-recordings/project/%2e%2e/call/a.wav",
        "call-recordings/project/vapi/call/",
        "call-recordings/project/vapi/call/a\\b.wav",
        "call-recordings/project/vapi/call/a?signature=secret",
        "call-recordings/call/customer.wav",
        "call-recordings/call/0/customer.wav",
        "call-recordings/project/vapi/call/extra/customer.wav",
        "call-recordings//vapi/call/customer.wav",
    ],
)
def test_key_outside_prefix_rejected(key):
    with pytest.raises(AudioProvenanceError, match="analysis_error") as exc:
        validate_object_key(key, "call")
    assert key not in str(exc.value)


def test_valid_key():
    validate_object_key("call-recordings/project/vapi/call/customer.wav", "call")


def test_no_outbound_http_clients_imported():
    root = Path(__file__).parents[2] / "services" / "audio_analysis"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            modules = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            assert not any(
                m.split(".")[0] in {"urllib", "requests", "httpx", "aiohttp"}
                for m in modules
            ), path
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = (
                    [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else [a.name for a in node.names]
                )
                assert not any(
                    m.split(".")[0] in {"onnxruntime", "librosa", "soundfile"}
                    for m in modules
                ), path


def test_no_temp_files_created():
    root = Path(__file__).parents[2] / "services" / "audio_analysis"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(a.name in {"tempfile", "subprocess"} for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert node.module not in {"tempfile", "subprocess"}


def test_cold_import_does_not_load_model_libraries():
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import ee.voice.services.audio_analysis; import ee.voice.services.audio_analysis.vqi; assert not {'onnxruntime', 'librosa', 'soundfile'} & sys.modules.keys()",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
