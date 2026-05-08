"""
Hypothesis property tests for model_hub pure functions.

Targets (all importable without Django):
  - model_hub.utils.column_utils   — _is_uuid, get_response_format_type,
                                     is_json_response_format, get_column_data_type
  - model_hub.utils.scoring        — normalize_score, determine_pass_fail,
                                     apply_choice_scores, validate_choice_scores,
                                     validate_pass_threshold
  - model_hub.utils.eval_validators — validate_eval_name, validate_criteria_has_variables,
                                      validate_choices_for_output_type,
                                      validate_length_between_config,
                                      validate_required_key_mapping
"""
import sys
import pathlib
import uuid
import pytest
from hypothesis import given, assume, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_pkg = pathlib.Path(__file__).parent.parent.parent
if str(_pkg) not in sys.path:
    sys.path.insert(0, str(_pkg))

# ---------------------------------------------------------------------------
# Direct module loading — bypasses model_hub/utils/__init__.py which has
# Django imports. Each util file has zero external deps at module level.
# ---------------------------------------------------------------------------
import importlib.util

def _load(name, relpath):
    path = _pkg / "model_hub" / "utils" / relpath
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_cu = _load("column_utils", "column_utils.py")
_sc = _load("scoring", "scoring.py")
_ev = _load("eval_validators", "eval_validators.py")

_is_uuid = _cu._is_uuid
get_response_format_type = _cu.get_response_format_type
is_json_response_format = _cu.is_json_response_format
get_column_data_type = _cu.get_column_data_type
OUTPUT_FORMAT_TO_DATA_TYPE = _cu.OUTPUT_FORMAT_TO_DATA_TYPE
JSON_RESPONSE_FORMAT_TYPES = _cu.JSON_RESPONSE_FORMAT_TYPES

normalize_score = _sc.normalize_score
determine_pass_fail = _sc.determine_pass_fail
apply_choice_scores = _sc.apply_choice_scores
validate_choice_scores = _sc.validate_choice_scores
validate_pass_threshold = _sc.validate_pass_threshold

validate_eval_name = _ev.validate_eval_name
validate_criteria_has_variables = _ev.validate_criteria_has_variables
validate_choices_for_output_type = _ev.validate_choices_for_output_type
validate_length_between_config = _ev.validate_length_between_config
validate_required_key_mapping = _ev.validate_required_key_mapping

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
_valid_uuid_str = st.uuids().map(str)
_invalid_uuid_str = st.text(min_size=1).filter(
    lambda s: not _try_uuid(s)
)


def _try_uuid(s):
    try:
        uuid.UUID(s)
        return True
    except Exception:
        return False


_valid_name_chars = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_", min_size=1)
_valid_eval_name = _valid_name_chars.filter(
    lambda s: s[0] not in "-_" and s[-1] not in "-_" and "_-" not in s and "-_" not in s
)

_score_01 = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)
_any_float = st.floats(allow_nan=False, allow_infinity=False)

_choice_scores_valid = st.dictionaries(
    keys=st.text(min_size=1, alphabet="abcdefghijklmnopqrstuvwxyz ").filter(lambda k: k.strip()),
    values=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    min_size=1,
)


# ---------------------------------------------------------------------------
# column_utils tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestIsUuid:
    @given(st.uuids())
    def test_uuid_instance_always_true(self, u):
        assert _is_uuid(u)

    @given(_valid_uuid_str)
    def test_valid_uuid_string_always_true(self, s):
        assert _is_uuid(s)

    @given(st.integers())
    def test_integer_always_false(self, n):
        assert not _is_uuid(n)

    @given(st.none())
    def test_none_always_false(self, v):
        assert not _is_uuid(v)

    def test_garbage_string_false(self):
        assert not _is_uuid("not-a-uuid-at-all!")

    def test_empty_string_false(self):
        assert not _is_uuid("")


@pytest.mark.unit
class TestGetResponseFormatType:
    def test_none_returns_none(self):
        assert get_response_format_type(None) is None

    @given(st.text())
    def test_dict_with_type_returns_type(self, t):
        assert get_response_format_type({"type": t}) == t

    def test_dict_without_type_returns_none(self):
        assert get_response_format_type({"other": "key"}) is None

    @given(st.uuids())
    def test_uuid_instance_returns_json_object(self, u):
        assert get_response_format_type(u) == "json_object"

    @given(_valid_uuid_str)
    def test_uuid_string_returns_json_object(self, s):
        assert get_response_format_type(s) == "json_object"

    @given(st.text(min_size=1).filter(lambda s: not _try_uuid(s)))
    def test_non_uuid_string_returns_itself(self, s):
        assert get_response_format_type(s) == s


@pytest.mark.unit
class TestIsJsonResponseFormat:
    @pytest.mark.parametrize("fmt", ["json_object", "json", "object",
                                      "JSON_OBJECT", "JSON", "OBJECT",
                                      "Json_Object"])
    def test_json_types_return_true(self, fmt):
        assert is_json_response_format(fmt)

    def test_none_returns_false(self):
        assert not is_json_response_format(None)

    def test_text_returns_false(self):
        assert not is_json_response_format("text")

    def test_empty_string_returns_false(self):
        assert not is_json_response_format("")

    @given(st.uuids())
    def test_uuid_returns_true(self, u):
        # UUID → json_object → is JSON
        assert is_json_response_format(u)


@pytest.mark.unit
class TestGetColumnDataType:
    @pytest.mark.parametrize("rf", ["json_object", "json", "object"])
    @pytest.mark.parametrize("of", list(OUTPUT_FORMAT_TO_DATA_TYPE.keys()) + ["unknown"])
    def test_json_rf_always_returns_json(self, rf, of):
        assert get_column_data_type(of, rf) == "json"

    @pytest.mark.parametrize("of,expected", OUTPUT_FORMAT_TO_DATA_TYPE.items())
    def test_no_rf_uses_output_format_mapping(self, of, expected):
        assert get_column_data_type(of) == expected

    def test_unknown_output_format_defaults_to_text(self):
        assert get_column_data_type("unknown_format") == "text"

    def test_none_rf_uses_output_format(self):
        assert get_column_data_type("number", None) == "integer"


# ---------------------------------------------------------------------------
# scoring tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestNormalizeScore:
    @given(_any_float)
    def test_result_always_in_0_1_for_percentage(self, v):
        result = normalize_score(v, "percentage")
        assert 0.0 <= result <= 1.0

    @given(_score_01)
    def test_percentage_in_range_passes_through(self, v):
        assert normalize_score(v, "percentage") == pytest.approx(v)

    @given(st.floats(max_value=-0.001, allow_nan=False))
    def test_percentage_negative_clamps_to_zero(self, v):
        assert normalize_score(v, "percentage") == 0.0

    @given(st.floats(min_value=1.001, allow_nan=False, allow_infinity=False))
    def test_percentage_over_one_clamps_to_one(self, v):
        assert normalize_score(v, "percentage") == 1.0

    def test_none_returns_zero(self):
        assert normalize_score(None) == 0.0

    @pytest.mark.parametrize("val,expected", [
        (True, 1.0), (False, 0.0),
        ("passed", 1.0), ("PASS", 1.0), ("yes", 1.0), ("true", 1.0),
        ("failed", 0.0), ("no", 0.0), ("nope", 0.0),
        (1, 1.0), (0, 0.0), (-1, 0.0),
    ])
    def test_pass_fail_values(self, val, expected):
        assert normalize_score(val, "pass_fail") == expected

    def test_pass_fail_none_returns_zero(self):
        assert normalize_score(None, "pass_fail") == 0.0

    @given(_score_01)
    def test_deterministic_float_in_range_passes_through(self, v):
        result = normalize_score(v, "deterministic")
        assert 0.0 <= result <= 1.0

    def test_deterministic_with_choice_scores(self):
        cs = {"Yes": 1.0, "No": 0.0, "Maybe": 0.5}
        assert normalize_score("Yes", "deterministic", cs) == 1.0
        assert normalize_score("no", "deterministic", cs) == 0.0  # case-insensitive
        assert normalize_score("maybe", "deterministic", cs) == 0.5


@pytest.mark.unit
class TestDeterminePassFail:
    @given(_score_01, _score_01)
    def test_score_gte_threshold_passes(self, score, threshold):
        expected = score >= threshold
        assert determine_pass_fail(score, threshold) == expected

    def test_exactly_at_threshold_passes(self):
        assert determine_pass_fail(0.5, 0.5)
        assert determine_pass_fail(0.0, 0.0)
        assert determine_pass_fail(1.0, 1.0)

    def test_just_below_threshold_fails(self):
        assert not determine_pass_fail(0.499, 0.5)


@pytest.mark.unit
class TestApplyChoiceScores:
    def test_exact_match(self):
        cs = {"Yes": 1.0, "No": 0.0}
        assert apply_choice_scores("Yes", cs) == 1.0

    def test_case_insensitive_match(self):
        cs = {"Yes": 1.0, "No": 0.0}
        assert apply_choice_scores("yes", cs) == 1.0
        assert apply_choice_scores("YES", cs) == 1.0

    def test_missing_label_returns_none(self):
        cs = {"Yes": 1.0}
        assert apply_choice_scores("Maybe", cs) is None

    def test_empty_choice_scores_returns_none(self):
        assert apply_choice_scores("Yes", {}) is None

    def test_empty_label_returns_none(self):
        assert apply_choice_scores("", {"Yes": 1.0}) is None

    @given(_choice_scores_valid)
    def test_exact_key_always_found(self, cs):
        key = next(iter(cs))
        assert apply_choice_scores(key, cs) == cs[key]


@pytest.mark.unit
class TestValidateChoiceScores:
    @given(_choice_scores_valid)
    def test_valid_choice_scores_produce_no_errors(self, cs):
        assert validate_choice_scores(cs) == []

    def test_non_dict_returns_error(self):
        errors = validate_choice_scores("not a dict")
        assert errors  # non-empty

    def test_empty_dict_returns_error(self):
        errors = validate_choice_scores({})
        assert errors

    def test_out_of_range_value_returns_error(self):
        errors = validate_choice_scores({"Yes": 1.5})
        assert errors

    def test_non_numeric_value_returns_error(self):
        errors = validate_choice_scores({"Yes": "high"})
        assert errors

    def test_empty_key_returns_error(self):
        errors = validate_choice_scores({"": 0.5})
        assert errors


@pytest.mark.unit
class TestValidatePassThreshold:
    @given(_score_01)
    def test_valid_threshold_produces_no_errors(self, t):
        assert validate_pass_threshold(t) == []

    def test_negative_threshold_returns_error(self):
        assert validate_pass_threshold(-0.1)

    def test_over_one_returns_error(self):
        assert validate_pass_threshold(1.1)

    def test_string_returns_error(self):
        assert validate_pass_threshold("high")


# ---------------------------------------------------------------------------
# eval_validators tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestValidateEvalName:
    @given(_valid_eval_name)
    def test_valid_names_pass(self, name):
        assert validate_eval_name(name) == name

    @pytest.mark.parametrize("bad", [
        "", "   ", "-start", "end-", "_start", "end_",
        "has space", "UPPERCASE", "has!bang", "_-mixed",
    ])
    def test_invalid_names_raise(self, bad):
        with pytest.raises(ValueError):
            validate_eval_name(bad)

    def test_strips_whitespace(self):
        assert validate_eval_name("  foo  ") == "foo"


@pytest.mark.unit
class TestValidateCriteriaHasVariables:
    def test_function_template_type_skips_check(self):
        validate_criteria_has_variables("no variables here", "Function")
        validate_criteria_has_variables("no variables here", "function")

    def test_non_function_with_variable_passes(self):
        validate_criteria_has_variables("Rate {{response}} quality", "llm")

    def test_non_function_without_variable_raises(self):
        with pytest.raises(ValueError):
            validate_criteria_has_variables("no variables here", "llm")

    def test_empty_criteria_raises(self):
        with pytest.raises(ValueError):
            validate_criteria_has_variables("", "llm")

    def test_none_criteria_raises(self):
        with pytest.raises(ValueError):
            validate_criteria_has_variables(None, "llm")


@pytest.mark.unit
class TestValidateChoicesForOutputType:
    def test_choices_output_type_requires_non_empty_dict(self):
        with pytest.raises(ValueError):
            validate_choices_for_output_type("choices", {})

    def test_choices_output_type_requires_dict_not_list(self):
        with pytest.raises(ValueError):
            validate_choices_for_output_type("choices", ["Yes", "No"])

    def test_choices_output_type_requires_dict_not_none(self):
        with pytest.raises(ValueError):
            validate_choices_for_output_type("choices", None)

    def test_choices_output_type_with_valid_dict_passes(self):
        validate_choices_for_output_type("choices", {"Yes": 1.0, "No": 0.0})

    @given(st.text().filter(lambda s: s != "choices"))
    def test_non_choices_output_type_always_passes(self, ot):
        validate_choices_for_output_type(ot, None)


@pytest.mark.unit
class TestValidateLengthBetweenConfig:
    def test_none_config_passes(self):
        validate_length_between_config(None)

    def test_empty_dict_passes(self):
        validate_length_between_config({})

    def test_valid_range_passes(self):
        validate_length_between_config({"config": {"minLength": 5, "maxLength": 10}})

    def test_min_gt_max_raises(self):
        with pytest.raises(ValueError):
            validate_length_between_config({"config": {"minLength": 10, "maxLength": 5}})

    def test_equal_min_max_passes(self):
        validate_length_between_config({"config": {"minLength": 5, "maxLength": 5}})

    def test_non_numeric_lengths_skipped(self):
        validate_length_between_config({"config": {"minLength": "big", "maxLength": "small"}})


@pytest.mark.unit
class TestValidateRequiredKeyMapping:
    def test_all_keys_present_returns_empty(self):
        assert validate_required_key_mapping({"a": "1", "b": "2"}, ["a", "b"]) == []

    def test_missing_key_returned_in_list(self):
        result = validate_required_key_mapping({"a": "1"}, ["a", "b"])
        assert "b" in result

    def test_empty_required_keys_always_passes(self):
        assert validate_required_key_mapping({}, []) == []

    @given(
        st.dictionaries(st.text(min_size=1), st.text(min_size=1), min_size=1),
    )
    def test_all_present_keys_produce_empty_result(self, mapping):
        keys = list(mapping.keys())
        assert validate_required_key_mapping(mapping, keys) == []

    def test_none_value_treated_as_missing(self):
        result = validate_required_key_mapping({"a": None}, ["a"])
        assert "a" in result

    def test_empty_string_value_treated_as_missing(self):
        result = validate_required_key_mapping({"a": ""}, ["a"])
        assert "a" in result
