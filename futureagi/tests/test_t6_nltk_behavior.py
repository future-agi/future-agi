"""Offline NLTK behavior fixtures measured with cached NLTK 3.10.3.

Run in an isolated NLTK environment, with --noconftest, to avoid Django/provider
startup. Compile the selected production definitions unchanged (apart from the
preprocessor registration decorator); do not substitute metric implementations.
NLTK_DATA must name existing local data. Missing resources skip, never download.
"""

from __future__ import annotations

import ast
import importlib.util
import logging
import os
import socket
from collections import Counter
from pathlib import Path

import nltk
import pytest

BACKEND = Path(__file__).resolve().parents[1]
FUNCTIONS = "agentic_eval/core_evals/fi_evals/function/functions.py"
PAIRS = [
    ("the cat sat on the mat", "the cat sat on the mat"),
    ("the cat sat on the mat", "the cat is on the mat"),
    ("hello world", "hello"),
    ("The cats were running quickly", "The cat runs quickly"),
]


def _definition(relative_path, name, **namespace):
    path = BACKEND / relative_path
    tree = ast.parse(path.read_text(), filename=str(path))
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name
    ]
    assert len(nodes) == 1
    nodes[0].decorator_list = []
    namespace["__file__"] = str(path)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


@pytest.fixture(autouse=True)
def offline_nltk(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Offline NLTK test attempted a download or network operation")

    data_root = os.environ.get("NLTK_DATA", str(Path.home() / "nltk_data"))
    monkeypatch.setenv("NLTK_DATA", data_root)
    monkeypatch.setenv("NLTK_DOWNLOAD_MISSING", "false")
    monkeypatch.setattr(nltk.data, "path", data_root.split(os.pathsep))
    monkeypatch.setattr(nltk, "download", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.syspath_prepend(str(BACKEND))

    from tfc.utils import nltk_data

    assert Path(nltk_data.__file__).is_relative_to(BACKEND)
    monkeypatch.setattr(nltk_data, "_present", set())


def _require(*resources):
    for resource in resources:
        try:
            nltk.data.find(resource)
        except LookupError:
            pytest.skip(f"Local NLTK_DATA lacks {resource}; downloads forbidden")


@pytest.mark.parametrize(
    "pair, expected",
    list(
        zip(
            PAIRS,
            [1.0, 0.293945703509473, 0.36787944117144233, 0.056526248548690414],
            strict=True,
        )
    ),
)
def test_production_bleu_matches_3_10_3(pair, expected):
    calculate = _definition(FUNCTIONS, "calculate_bleu")
    output = calculate(*pair)
    assert output["result"] == pytest.approx(expected, abs=1e-14, rel=1e-12)
    assert output["reason"] == f"BLEU score: {output['result']}"


@pytest.mark.parametrize(
    "pair, expected",
    list(zip(PAIRS, ["1.000", "0.833", "0.667", "0.889"], strict=True)),
)
def test_production_rouge_matches_3_10_3(pair, expected):
    calculate = _definition(FUNCTIONS, "calculate_rouge")
    assert calculate(*pair)["result"] == expected


@pytest.mark.parametrize(
    "reference, hypothesis, expected",
    [
        ("the cat sat on the mat", "the cat sat on the mat", 0.9976851851851852),
        ("the car is fast", "the automobile is quick", 0.6388888888888888),
        ("the cat sat on the mat", "the cat is on the mat", 0.8066666666666668),
    ],
)
def test_production_meteor_matches_3_10_3(reference, hypothesis, expected):
    _require("corpora/wordnet/")
    preprocess = _definition(
        "evaluations/engine/preprocessing.py",
        "_preprocess_meteor",
        logger=logging.getLogger(__name__),
    )
    result = preprocess({"reference": reference, "hypothesis": hypothesis})
    assert "_meteor_error" not in result, result
    assert result["_meteor_precomputed_score"] == pytest.approx(
        expected, abs=1e-14, rel=1e-12
    )


def test_production_annotation_tokenize_pos_and_lemmas_match_3_10_3(monkeypatch):
    _require(
        "corpora/stopwords/",
        "corpora/wordnet/",
        "tokenizers/punkt_tab/english/",
        "taggers/averaged_perceptron_tagger_eng/",
    )
    builder = _definition(
        "model_hub/utils/utils.py", "AnnotationCorpusBuilder", Counter=Counter
    )
    observed_tokens, observed_tags = [], []
    tokenize, tag = nltk.word_tokenize, nltk.pos_tag

    def record_tokens(sentence):
        result = tokenize(sentence)
        observed_tokens.append(result)
        return result

    def record_tags(tokens):
        result = tag(tokens)
        observed_tags.append(result)
        return result

    monkeypatch.setattr(nltk, "word_tokenize", record_tokens)
    monkeypatch.setattr(nltk, "pos_tag", record_tags)
    output = builder().build_annotation_corpus(
        ["The cats are running quickly.", "A dog chased the cars."]
    )
    assert output == (
        ["car", "cat", "chase", "dog", "quickly", "run"],
        ["cat", "run", "quickly", "dog", "chase", "car"],
        3,
        3,
        3.0,
    )
    assert observed_tokens == [
        ["The", "cats", "are", "running", "quickly", "."],
        ["A", "dog", "chased", "the", "cars", "."],
    ]
    assert observed_tags == [
        [
            ("The", "DT"),
            ("cats", "NNS"),
            ("are", "VBP"),
            ("running", "VBG"),
            ("quickly", "RB"),
            (".", "."),
        ],
        [
            ("A", "DT"),
            ("dog", "NN"),
            ("chased", "VBD"),
            ("the", "DT"),
            ("cars", "NNS"),
            (".", "."),
        ],
    ]


def test_full_profile_multilingual_wordnet_when_available():
    _require("corpora/wordnet/", "corpora/omw-2.0/")
    assert nltk.corpus.wordnet.synsets("chien", lang="fra")


def test_pinned_minimal_corpus_format_still_matches_loaders():
    path = BACKEND / "bin/install_nltk_data.py"
    spec = importlib.util.spec_from_file_location("t6_nltk_installer", path)
    assert spec is not None and spec.loader is not None
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    assert installer.selected_packages("minimal") == {
        "corpora/stopwords": None,
        "tokenizers/punkt_tab": ("punkt_tab/english/", "punkt_tab/README"),
        "taggers/averaged_perceptron_tagger_eng": None,
        "corpora/wordnet": None,
    }
