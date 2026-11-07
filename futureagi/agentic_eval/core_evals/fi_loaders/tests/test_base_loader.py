import json

import pytest

from agentic_eval.core_evals.fi_loaders.base_loader import BaseLoader, LoadFormat
from agentic_eval.core_evals.fi_loaders.loader import Loader


class DummyLoader(BaseLoader):
    def __init__(self):
        self._raw_dataset = []
        self._processed_dataset = []

    def process(self):
        self._processed_dataset = [
            {"processed": item} for item in self._raw_dataset
        ]
        return self._processed_dataset

    def load_fi_inferences(self, data: dict):
        raise NotImplementedError


def test_load_jsonl_parses_records_and_skips_blank_lines(tmp_path):
    path = tmp_path / "dataset.jsonl"
    path.write_text(
        '{"query": "q1", "response": "r1"}\n'
        "\n"
        '{"query": "q2", "response": "r2"}\n',
        encoding="utf-8",
    )
    loader = DummyLoader()

    result = loader.load(LoadFormat.JSONL.value, filename=str(path))

    assert loader.raw_dataset == [
        {"query": "q1", "response": "r1"},
        {"query": "q2", "response": "r2"},
    ]
    assert result == [
        {"processed": {"query": "q1", "response": "r1"}},
        {"processed": {"query": "q2", "response": "r2"}},
    ]


def test_load_jsonl_reports_invalid_line_number(tmp_path):
    path = tmp_path / "dataset.jsonl"
    path.write_text(
        '{"query": "q1", "response": "r1"}\n'
        "{not-json}\n",
        encoding="utf-8",
    )
    loader = DummyLoader()

    with pytest.raises(json.JSONDecodeError, match="JSONL line 2"):
        loader.load_jsonl(str(path))


def test_load_jsonl_works_with_generic_loader(tmp_path):
    path = tmp_path / "dataset.jsonl"
    path.write_text(
        (
            '{"query": "What is Python?", '
            '"context": ["Python is a programming language."], '
            '"response": "Python is a programming language."}\n'
        ),
        encoding="utf-8",
    )

    result = Loader().load(LoadFormat.JSONL.value, filename=str(path))

    assert result == [
        {
            "query": "What is Python?",
            "context": ["Python is a programming language."],
            "response": "Python is a programming language.",
            "expected_response": None,
        }
    ]
