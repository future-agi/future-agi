# Skip script-style files during pytest collection.
#
# These files are named test_*.py but are actually manual-run scripts (they do
# real work at module import time — instantiate LLM clients, run evaluators,
# etc.). Run them as:  python ee/falcon_ai/tests/integration/<file>
collect_ignore = [
    "test_non_streaming.py",
    "test_toxicity_eval.py",
]
