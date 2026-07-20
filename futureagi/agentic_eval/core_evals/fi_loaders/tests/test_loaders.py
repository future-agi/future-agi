import json
import pytest
from agentic_eval.core_evals.fi_loaders.base_loader import LoadFormat
from agentic_eval.core_evals.fi_loaders.json_loader import JsonLoader


class TestLoadersJSONL:
    def test_load_jsonl_valid(self, tmp_path):
        # Create a valid jsonl file
        file_path = tmp_path / "valid.jsonl"
        content = (
            '{"actual_json": {"a": 1}, "expected_json": {"a": 1}}\n'
            '{"actual_json": {"b": 2}}\n'
        )
        file_path.write_text(content)

        loader = JsonLoader()
        result = loader.load(format="jsonl", filename=str(file_path))

        assert len(result) == 2
        assert result[0] == {"actual_json": {"a": 1}, "expected_json": {"a": 1}}
        assert result[1] == {"actual_json": {"b": 2}}

    def test_load_jsonl_skips_empty_lines(self, tmp_path):
        # Create a jsonl file with empty lines and whitespace lines
        file_path = tmp_path / "empty_lines.jsonl"
        content = (
            '{"actual_json": {"a": 1}}\n'
            '\n'
            '   \n'
            '{"actual_json": {"b": 2}}\n'
            '\n'
        )
        file_path.write_text(content)

        loader = JsonLoader()
        result = loader.load(format="jsonl", filename=str(file_path))

        assert len(result) == 2
        assert result[0] == {"actual_json": {"a": 1}}
        assert result[1] == {"actual_json": {"b": 2}}

    def test_load_jsonl_invalid_json_reports_line_number(self, tmp_path):
        # Create a jsonl file with an error on line 3
        file_path = tmp_path / "invalid.jsonl"
        content = (
            '{"actual_json": {"a": 1}}\n'
            '\n'
            '{"actual_json": {"b": 2\n'
        )
        file_path.write_text(content)

        loader = JsonLoader()
        with pytest.raises(json.JSONDecodeError) as exc_info:
            loader.load(format="jsonl", filename=str(file_path))

        assert "line 3" in str(exc_info.value)

    def test_load_jsonl_file_not_found(self):
        loader = JsonLoader()
        with pytest.raises(FileNotFoundError):
            loader.load(format="jsonl", filename="non_existent_file.jsonl")
