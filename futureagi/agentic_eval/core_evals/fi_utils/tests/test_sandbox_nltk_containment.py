"""NLTK must not be importable inside the RestrictedPython local fallback.

The fallback (``_build_python_sandbox_script``) blocks ``open``, ``exec``,
``compile`` and ``__import__`` for user code and only lets it import the
modules in ``SAFE_MODULES``. NLTK's data and model loaders (``nltk.data.load``
and the corpus/tagger loaders) open files by path and unpickle them, so an
allowed ``nltk`` hands restricted code the file and code operations the
wrapper exists to block (T6 #5, SEC-SCAN-FA-SCA-PYPI-NLTK).

These tests run the real generated wrapper in a child interpreter, the way
the fallback does, without the rlimits (which are not what is under test).
They need RestrictedPython and nltk installed. No database, network or
code-executor service is used.
"""

import json
import subprocess
import sys
import textwrap
from unittest.mock import Mock

import pytest

from agentic_eval.core_evals.fi_utils import sandbox

CHILD_ENV = {
    "PYTHONDONTWRITEBYTECODE": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
}


def _run_fallback(tmp_path, code, input_data=None):
    script = sandbox._build_python_sandbox_script(
        textwrap.dedent(code), input_data or {}
    )
    path = tmp_path / "sandbox_case.py"
    path.write_text(script)
    proc = subprocess.run(
        [sys.executable, "-I", str(path)],
        capture_output=True,
        text=True,
        timeout=180,
        env=CHILD_ENV,
        cwd=str(tmp_path),
    )
    assert proc.stdout.strip(), proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _assert_refused_by_policy(result, module):
    assert result["status"] == "error", result
    assert f"Import of '{module}" in result["data"], result
    assert "is not allowed" in result["data"], result


NLTK_IMPORTS = {
    "import_nltk": (
        """
        import nltk

        def evaluate(**kwargs):
            return {"score": 1.0, "reason": str(nltk.data.path)}
        """,
        "nltk",
    ),
    "from_nltk_import_data": (
        """
        from nltk import data

        def evaluate(**kwargs):
            return {"score": 1.0, "reason": str(data.path)}
        """,
        "nltk",
    ),
    # The import statement calls the sandbox's __import__ hook with the
    # dotted name 'nltk.data': the same call as __import__('nltk.data').
    "import_nltk_data": (
        """
        import nltk.data

        def evaluate(**kwargs):
            return {"score": 1.0, "reason": str(nltk.data.path)}
        """,
        "nltk.data",
    ),
    "import_nltk_bleu": (
        """
        import nltk.translate.bleu_score

        def evaluate(**kwargs):
            bleu = nltk.translate.bleu_score.sentence_bleu([["a", "b"]], ["a", "b"])
            return {"score": 1.0, "reason": str(bleu)}
        """,
        "nltk.translate.bleu_score",
    ),
}


@pytest.mark.parametrize("case", sorted(NLTK_IMPORTS))
def test_fallback_refuses_nltk_imports(tmp_path, case):
    code, module = NLTK_IMPORTS[case]
    _assert_refused_by_policy(_run_fallback(tmp_path, code), module)


def test_fallback_refuses_dunder_import_of_nltk_data(tmp_path):
    result = _run_fallback(
        tmp_path,
        """
        def evaluate(**kwargs):
            mod = __import__("nltk.data")
            return {"score": 1.0, "reason": str(mod)}
        """,
    )
    # Refused either when compiling (RestrictedPython rejects names starting
    # with "_") or by the import policy; never run.
    assert result["status"] == "error", result
    assert (
        "Compilation errors" in result["data"] or "is not allowed" in result["data"]
    ), result


def test_nltk_is_not_a_permitted_module():
    permitted = [m for m in sandbox.SAFE_MODULES if m.split(".")[0] == "nltk"]
    assert permitted == []


def test_permitted_modules_do_not_load_nltk_into_the_fallback():
    """Nothing the wrapper pre-imports may bring nltk into the child."""
    probe = (
        "import json, sys\n"
        "for name in json.loads(sys.argv[1]):\n"
        "    try:\n"
        "        __import__(name)\n"
        "    except ImportError:\n"
        "        pass\n"
        "print(json.dumps(sorted(m for m in sys.modules"
        " if m == 'nltk' or m.startswith('nltk.'))))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-I", "-c", probe, json.dumps(sandbox.SAFE_MODULES)],
        capture_output=True,
        text=True,
        timeout=180,
        env=CHILD_ENV,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == []


def test_fallback_does_not_reach_nltk_through_rouge_scorer(tmp_path):
    # rouge_score.rouge_scorer does `import nltk`; reaching it would hand the
    # restricted code nltk as an attribute.
    result = _run_fallback(
        tmp_path,
        """
        def evaluate(**kwargs):
            from rouge_score import rouge_scorer
            return {"score": 1.0, "reason": str(rouge_scorer.nltk.data.path)}
        """,
    )
    assert result["status"] == "error", result
    assert (
        "cannot import name 'rouge_scorer'" in result["data"]
        or "is not allowed" in result["data"]
    ), result


def test_fallback_still_allows_ordinary_imports(tmp_path):
    result = _run_fallback(
        tmp_path,
        """
        import json
        import math
        import re
        import difflib
        import numpy
        from collections import Counter

        def evaluate(output, expected, **kwargs):
            words = re.findall(r"\\w+", output)
            counts = Counter(words)
            ratio = difflib.SequenceMatcher(None, output, expected).ratio()
            mean = float(numpy.mean([ratio, ratio]))
            return {
                "score": math.floor(mean),
                "reason": json.dumps({"words": len(counts)}),
            }
        """,
        {"output": "the cat sat", "expected": "the cat sat"},
    )
    assert result["status"] == "success", result
    assert result["data"]["result"] == 1
    assert json.loads(result["data"]["reason"]) == {"words": 3}


def test_default_execution_path_never_runs_the_local_fallback(monkeypatch):
    """Executor unreachable, even with CODE_EXECUTOR_LOCAL_FALLBACK set."""
    monkeypatch.setenv("CODE_EXECUTOR_LOCAL_FALLBACK", "true")
    monkeypatch.setattr(sandbox, "_call_executor_service", lambda *a, **k: None)
    build = Mock(side_effect=AssertionError("fallback script built"))
    run = Mock(side_effect=AssertionError("fallback subprocess started"))
    monkeypatch.setattr(sandbox, "_build_python_sandbox_script", build)
    monkeypatch.setattr(sandbox.subprocess, "run", run)

    result = sandbox.execute_sandboxed_python(
        "import nltk\ndef evaluate(**kwargs):\n    return 1\n", {}
    )

    assert result["status"] == "error"
    assert result["setup_error"] is True
    assert result["error_code"] == "EGRESS_POLICY_UNAVAILABLE"
    assert result["data"] == sandbox.EXECUTOR_UNAVAILABLE_MESSAGE
    build.assert_not_called()
    run.assert_not_called()
    assert sandbox._local_fallback_allowed("python") is False
