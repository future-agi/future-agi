import pytest

from agentic_eval.core_evals.fi_evals.grounded.similarity import (
    CosineSimilarity,
    JaccardSimilarity,
    JaroWincklerSimilarity,
    NormalisedLevenshteinSimilarity,
    PhoneticSimilarity,
    SorensenDiceSimilarity,
)


class TestCosineSimilarity:
    c = CosineSimilarity()

    def test_identical(self):
        assert self.c.compare("hello world", "hello world") == pytest.approx(1.0)

    def test_partial_overlap(self):
        result = self.c.compare("hello world", "hello there")
        assert 0 < result < 1

    def test_no_overlap(self):
        assert self.c.compare("abc", "xyz") == 0.0

    def test_both_empty(self):
        assert self.c.compare("", "") == 0.0

    def test_one_empty(self):
        assert self.c.compare("hello", "") == 0.0

    def test_whitespace_only(self):
        assert self.c.compare("   ", "   ") == 0.0

    def test_case_insensitive(self):
        assert self.c.compare("Hello World", "hello world") == pytest.approx(1.0)


class TestNormalisedLevenshteinSimilarity:
    l = NormalisedLevenshteinSimilarity()

    def test_identical(self):
        assert self.l.compare("hello", "hello") == 1.0

    def test_completely_different(self):
        assert self.l.compare("abc", "xyz") == 0.0

    def test_one_insertion(self):
        result = self.l.compare("cat", "cats")
        assert result == 0.75

    def test_one_substitution(self):
        result = self.l.compare("cat", "cut")
        assert 0.5 < result < 1.0

    def test_both_empty(self):
        assert self.l.compare("", "") == 1.0

    def test_one_empty(self):
        assert self.l.compare("hello", "") == 0.0

    def test_other_empty(self):
        assert self.l.compare("", "world") == 0.0

    def test_single_char(self):
        assert self.l.compare("a", "a") == 1.0


class TestJaroWincklerSimilarity:
    j = JaroWincklerSimilarity()

    def test_identical(self):
        assert self.j.compare("hello", "hello") == pytest.approx(1.0)

    def test_both_empty(self):
        assert self.j.compare("", "") == 0.0

    def test_one_empty(self):
        assert self.j.compare("hello", "") == 0.0

    def test_transposition(self):
        result = self.j.compare("martha", "marhta")
        assert result == pytest.approx(0.9444, abs=1e-3)

    def test_different(self):
        result = self.j.compare("abc", "xyz")
        assert result == 0.0


class TestJaccardSimilarity:
    j = JaccardSimilarity()

    def test_identical(self):
        assert self.j.compare("hello world", "hello world") == 1.0

    def test_partial_overlap(self):
        result = self.j.compare("hello world", "hello there")
        assert 0 < result < 1

    def test_no_overlap(self):
        assert self.j.compare("abc def", "ghi jkl") == 0.0

    def test_both_empty(self):
        assert self.j.compare("", "") == 1.0

    def test_one_empty(self):
        assert self.j.compare("hello world", "") == 0.0

    def test_other_empty(self):
        assert self.j.compare("", "hello world") == 0.0

    def test_whitespace_only(self):
        assert self.j.compare("   ", "   ") == 1.0

    def test_case_sensitive(self):
        result = self.j.compare("Hello World", "hello world")
        assert result < 1.0


class TestSorensenDiceSimilarity:
    d = SorensenDiceSimilarity()

    def test_identical(self):
        assert self.d.compare("hello world", "hello world") == 1.0

    def test_partial_overlap(self):
        result = self.d.compare("hello world", "hello there")
        assert 0 < result < 1

    def test_no_overlap(self):
        assert self.d.compare("abc def", "ghi jkl") == 0.0

    def test_both_empty(self):
        assert self.d.compare("", "") == 1.0

    def test_one_empty(self):
        assert self.d.compare("hello world", "") == 0.0

    def test_other_empty(self):
        assert self.d.compare("", "hello world") == 0.0

    def test_whitespace_only(self):
        assert self.d.compare("   ", "   ") == 1.0

    def test_case_sensitive(self):
        result = self.d.compare("Hello World", "hello world")
        assert result < 1.0


class TestPhoneticSimilarity:
    p = PhoneticSimilarity()

    def test_identical_sound(self):
        assert self.p.compare("Smith", "Smyth") == 1.0

    def test_totally_different(self):
        assert self.p.compare("Smith", "Johnson") == 0.0

    def test_both_empty(self):
        assert self.p.compare("", "") == 1.0

    def test_one_empty(self):
        assert self.p.compare("Smith", "") == 0.0

    def test_mixed_case(self):
        assert self.p.compare("SMITH", "smith") == 1.0

    def test_multi_word(self):
        assert self.p.compare("Robert Smith", "Rupert Smyth") == 1.0
