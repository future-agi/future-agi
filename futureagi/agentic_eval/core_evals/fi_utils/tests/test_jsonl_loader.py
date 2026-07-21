import importlib.util
import json
import pathlib

import pytest

# Import base_loader directly to avoid triggering fi_loaders/__init__.py,
# which pulls in optional heavy dependencies (retrying, etc.) not required here.
_spec = importlib.util.spec_from_file_location(
    "base_loader",
    pathlib.Path(__file__).parent.parent.parent / "fi_loaders" / "base_loader.py",
)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
BaseLoader = _mod.BaseLoader
LoadFormat = _mod.LoadFormat


class _PassThroughLoader(BaseLoader):
    """Minimal concrete loader to exercise base-class file loading in isolation."""

    def __init__(self):
        self._raw_dataset = []
        self._processed_dataset = []

    def process(self):
        self._processed_dataset = list(self._raw_dataset)

    def load_fi_inferences(self, *args, **kwargs):
        raise NotImplementedError


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return str(path)


class TestJsonlLoader:
    def test_loads_one_object_per_line(self, tmp_path):
        path = _write(
            tmp_path,
            "data.jsonl",
            '{"response": "a"}\n{"response": "b"}\n{"response": "c"}\n',
        )
        out = _PassThroughLoader().load_jsonl(path)
        assert out == [{"response": "a"}, {"response": "b"}, {"response": "c"}]

    def test_skips_blank_lines(self, tmp_path):
        path = _write(
            tmp_path,
            "data.jsonl",
            '{"response": "a"}\n\n   \n{"response": "b"}\n',
        )
        out = _PassThroughLoader().load_jsonl(path)
        assert out == [{"response": "a"}, {"response": "b"}]

    def test_dispatch_via_load(self, tmp_path):
        path = _write(tmp_path, "data.jsonl", '{"response": "x"}\n')
        out = _PassThroughLoader().load(LoadFormat.JSONL.value, filename=path)
        assert out == [{"response": "x"}]

    def test_malformed_line_raises(self, tmp_path):
        path = _write(tmp_path, "bad.jsonl", '{"response": "a"}\n{not valid}\n')
        with pytest.raises(json.JSONDecodeError):
            _PassThroughLoader().load_jsonl(path)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            _PassThroughLoader().load_jsonl(str(tmp_path / "nope.jsonl"))

    def test_unknown_format_still_raises(self):
        with pytest.raises(NotImplementedError):
            _PassThroughLoader().load("parquet", filename="x")
