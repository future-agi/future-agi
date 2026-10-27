from agentic_eval.core_evals.fi_evals.grounded.similarity import PhoneticSimilarity

p = PhoneticSimilarity()


def test_identical_sound():
    assert p.compare("Smith", "Smyth") == 1.0


def test_totally_different():
    assert p.compare("Smith", "Johnson") == 0.0


def test_empty_strings():
    assert p.compare("", "") == 1.0


def test_one_empty():
    assert p.compare("Smith", "") == 0.0


def test_mixed_case():
    assert p.compare("SMITH", "smith") == 1.0


def test_multi_word():
    assert p.compare("Robert Smith", "Rupert Smyth") == 1.0
