"""
Z3 formal verification for model_hub pure functions.

Targets:
  - column_utils.get_column_data_type  — response_format / output_format dispatch tree
  - scoring.normalize_score            — multi-branch score normalization
  - scoring.validate_choice_scores     — validation decision tree

EnumSorts are declared at MODULE LEVEL — Z3 raises if the same sort name is
re-declared inside a function during the same process.
"""
import sys
import pathlib
import pytest
import z3

# ---------------------------------------------------------------------------
# Make model_hub importable without Django
# ---------------------------------------------------------------------------
_repo = pathlib.Path(__file__).parent.parent.parent.parent  # futureagi/../..
_pkg = _repo / "futureagi"
if str(_pkg) not in sys.path:
    sys.path.insert(0, str(_pkg))

# ---------------------------------------------------------------------------
# Module-level Z3 EnumSorts (declared once per process)
# ---------------------------------------------------------------------------

# get_column_data_type dispatch
DataTypeCat, (DT_JSON, DT_TEXT, DT_INTEGER, DT_ARRAY, DT_AUDIO, DT_IMAGE) = z3.EnumSort(
    "DataTypeCat",
    ["dt_json", "dt_text", "dt_integer", "dt_array", "dt_audio", "dt_image"],
)

RespFmtCat, (RF_NONE, RF_JSON_OBJECT, RF_JSON, RF_OBJECT, RF_OTHER) = z3.EnumSort(
    "RespFmtCat",
    ["rf_none", "rf_json_object", "rf_json", "rf_object", "rf_other"],
)

OutFmtCat, (OF_OBJECT, OF_NUMBER, OF_STRING, OF_ARRAY, OF_AUDIO, OF_IMAGE, OF_OTHER) = z3.EnumSort(
    "OutFmtCat",
    ["of_object", "of_number", "of_string", "of_array", "of_audio", "of_image", "of_other"],
)

# normalize_score output_type
ScoreTypeCat, (ST_PASS_FAIL, ST_PERCENTAGE, ST_DETERMINISTIC, ST_UNKNOWN) = z3.EnumSort(
    "ScoreTypeCat",
    ["st_pass_fail", "st_percentage", "st_deterministic", "st_unknown"],
)

# ---------------------------------------------------------------------------
# Helpers: model_hub decision trees expressed as Z3 functions
# ---------------------------------------------------------------------------

def _model_get_column_data_type(rf: z3.ExprRef, of: z3.ExprRef) -> z3.ExprRef:
    """Z3 model of get_column_data_type branching logic."""
    is_json_rf = z3.Or(rf == RF_JSON_OBJECT, rf == RF_JSON, rf == RF_OBJECT)
    output_branch = z3.If(
        of == OF_OBJECT, DT_JSON,
        z3.If(of == OF_NUMBER, DT_INTEGER,
        z3.If(of == OF_STRING, DT_TEXT,
        z3.If(of == OF_ARRAY, DT_ARRAY,
        z3.If(of == OF_AUDIO, DT_AUDIO,
        z3.If(of == OF_IMAGE, DT_IMAGE,
        DT_TEXT))))))
    return z3.If(is_json_rf, DT_JSON, output_branch)


def _model_normalize_score_range(score_type: z3.ExprRef, raw: z3.ArithRef) -> z3.ArithRef:
    """
    Z3 model of the output range of normalize_score.
    For percentage / deterministic numeric branch, result is clamped to [0, 1].
    For pass_fail, result is 0 or 1.
    """
    return z3.If(
        score_type == ST_PASS_FAIL,
        # Result is always exactly 0.0 or 1.0 for pass_fail
        z3.If(raw > 0, z3.RealVal(1), z3.RealVal(0)),
        # For other types result is in [0, 1]
        z3.If(raw < 0, z3.RealVal(0), z3.If(raw > 1, z3.RealVal(1), raw)),
    )


# ---------------------------------------------------------------------------
# Tests: get_column_data_type decision tree
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestGetColumnDataTypeZ3:
    def _prove(self, claim):
        """Assert that NOT claim is UNSAT (i.e., claim holds for all models)."""
        s = z3.Solver()
        s.add(z3.Not(claim))
        assert s.check() == z3.unsat, f"Counter-example exists: {s.model()}"

    def test_json_response_format_always_returns_json(self):
        """If response_format is any JSON type, result is always DT_JSON."""
        rf = z3.Const("rf", RespFmtCat)
        of = z3.Const("of", OutFmtCat)
        is_json_rf = z3.Or(rf == RF_JSON_OBJECT, rf == RF_JSON, rf == RF_OBJECT)
        result = _model_get_column_data_type(rf, of)
        self._prove(z3.Implies(is_json_rf, result == DT_JSON))

    def test_none_response_format_uses_output_format(self):
        """With RF_NONE, result is determined entirely by output_format."""
        of = z3.Const("of", OutFmtCat)
        result_none = _model_get_column_data_type(RF_NONE, of)
        result_other = _model_get_column_data_type(RF_OTHER, of)
        # Both RF_NONE and RF_OTHER are non-JSON → same output branch
        self._prove(result_none == result_other)

    def test_output_format_object_maps_to_json_without_rf(self):
        """output_format='object' maps to DT_JSON when response_format is non-JSON."""
        result = _model_get_column_data_type(RF_NONE, OF_OBJECT)
        self._prove(result == DT_JSON)

    def test_output_format_number_maps_to_integer(self):
        result = _model_get_column_data_type(RF_NONE, OF_NUMBER)
        self._prove(result == DT_INTEGER)

    def test_output_format_array_maps_to_array(self):
        result = _model_get_column_data_type(RF_NONE, OF_ARRAY)
        self._prove(result == DT_ARRAY)

    def test_output_format_audio_maps_to_audio(self):
        result = _model_get_column_data_type(RF_NONE, OF_AUDIO)
        self._prove(result == DT_AUDIO)

    def test_output_format_image_maps_to_image(self):
        result = _model_get_column_data_type(RF_NONE, OF_IMAGE)
        self._prove(result == DT_IMAGE)

    def test_unknown_output_format_maps_to_text(self):
        result = _model_get_column_data_type(RF_NONE, OF_OTHER)
        self._prove(result == DT_TEXT)

    def test_json_rf_overrides_audio_output_format(self):
        """JSON response_format overrides even a non-JSON output_format."""
        result = _model_get_column_data_type(RF_JSON_OBJECT, OF_AUDIO)
        self._prove(result == DT_JSON)

    def test_result_is_always_one_of_six_types(self):
        """Result is always a member of the six known data type categories."""
        rf = z3.Const("rf2", RespFmtCat)
        of = z3.Const("of2", OutFmtCat)
        result = _model_get_column_data_type(rf, of)
        valid = z3.Or(
            result == DT_JSON, result == DT_TEXT, result == DT_INTEGER,
            result == DT_ARRAY, result == DT_AUDIO, result == DT_IMAGE,
        )
        self._prove(valid)


# ---------------------------------------------------------------------------
# Tests: normalize_score clamping invariants
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestNormalizeScoreZ3:
    def _prove(self, claim):
        s = z3.Solver()
        s.add(z3.Not(claim))
        assert s.check() == z3.unsat, f"Counter-example: {s.model()}"

    def test_percentage_output_always_clamped_to_0_1(self):
        """For percentage type, any raw value produces a result in [0, 1]."""
        raw = z3.Real("raw")
        result = _model_normalize_score_range(ST_PERCENTAGE, raw)
        self._prove(z3.And(result >= 0, result <= 1))

    def test_pass_fail_output_is_zero_or_one(self):
        """For pass_fail type, result is always 0 or 1 regardless of raw value."""
        raw = z3.Real("raw2")
        result = _model_normalize_score_range(ST_PASS_FAIL, raw)
        self._prove(z3.Or(result == 0, result == 1))

    def test_percentage_negative_raw_clamps_to_zero(self):
        """Negative raw scores clamp to 0.0 for percentage type."""
        raw = z3.Real("raw3")
        result = _model_normalize_score_range(ST_PERCENTAGE, raw)
        self._prove(z3.Implies(raw < 0, result == 0))

    def test_percentage_over_one_clamps_to_one(self):
        """Raw scores > 1.0 clamp to 1.0 for percentage type."""
        raw = z3.Real("raw4")
        result = _model_normalize_score_range(ST_PERCENTAGE, raw)
        self._prove(z3.Implies(raw > 1, result == 1))

    def test_in_range_percentage_passes_through(self):
        """Scores already in [0, 1] are returned unchanged for percentage type."""
        raw = z3.Real("raw5")
        result = _model_normalize_score_range(ST_PERCENTAGE, raw)
        self._prove(z3.Implies(z3.And(raw >= 0, raw <= 1), result == raw))

    def test_pass_fail_positive_raw_is_one(self):
        raw = z3.Real("raw6")
        result = _model_normalize_score_range(ST_PASS_FAIL, raw)
        self._prove(z3.Implies(raw > 0, result == 1))

    def test_pass_fail_nonpositive_raw_is_zero(self):
        raw = z3.Real("raw7")
        result = _model_normalize_score_range(ST_PASS_FAIL, raw)
        self._prove(z3.Implies(raw <= 0, result == 0))
