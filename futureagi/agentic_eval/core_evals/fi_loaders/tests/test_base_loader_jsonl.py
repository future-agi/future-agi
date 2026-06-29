import json

import pytest

from agentic_eval.core_evals.fi_loaders.base_loader import BaseLoader, LoadFormat


class _PassthroughLoader(BaseLoader):
    def process(self):
        self._processed_dataset = self._raw_dataset
        return self._processed_dataset

    def load_fi_inferences(self, data: dict):
        self._raw_dataset = data
        return self.process()


def test_load_jsonl_skips_blank_lines_and_processes_records(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        '\n'.join(
            [
                json.dumps({"input": "first", "expected": "one"}),
                "",
                "   ",
                json.dumps({"input": "second", "expected": "two"}),
            ]
        )
    )

    loader = _PassthroughLoader()

    assert loader.load(LoadFormat.JSONL.value, filename=str(dataset)) == [
        {"input": "first", "expected": "one"},
        {"input": "second", "expected": "two"},
    ]
    assert loader.raw_dataset == loader.processed_dataset


def test_load_jsonl_reports_invalid_line_number(tmp_path):
    dataset = tmp_path / "bad.jsonl"
    dataset.write_text('{"ok": true}\n{bad json}\n')

    loader = _PassthroughLoader()

    with pytest.raises(json.JSONDecodeError, match="line 2"):
        loader.load_jsonl(str(dataset))
