from agentic_eval.core_evals.fi_metrics.groundedness import GroundednessScore


class TestGroundednessScore:
    """Regression tests for GroundednessScore.compute edge cases."""

    def test_empty_input_does_not_crash(self):
        # No sentences to evaluate must not raise ZeroDivisionError.
        score, unsupported, supported = GroundednessScore.compute([])
        assert score == 1.0
        assert unsupported == []
        assert supported == []

    def test_none_sentences_excluded_from_denominator(self):
        # A malformed entry with sentence=None must not dilute the score: one
        # supported sentence plus one None entry should score 1.0, not 0.5.
        result = GroundednessScore.compute(
            [
                {
                    "sentence": "The sky is blue.",
                    "supporting_evidence": ["Sky appears blue."],
                },
                {"sentence": None, "supporting_evidence": []},
            ]
        )
        assert result[0] == 1.0

    def test_mixed_supported_and_unsupported_unchanged(self):
        # Normal case is unchanged: one supported plus one unsupported = 0.5.
        result = GroundednessScore.compute(
            [
                {"sentence": "a", "supporting_evidence": ["x"]},
                {"sentence": "b", "supporting_evidence": []},
            ]
        )
        assert result[0] == 0.5
