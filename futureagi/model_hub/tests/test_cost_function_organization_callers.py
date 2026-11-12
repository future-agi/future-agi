"""Only the places that SHOW a cost may price a custom model at its saved price.

calculate_total_cost uses a custom model's saved price only when its caller
passes organization_id=. That is allowed for the displayed cost (prompt
cells, Run Prompt, the playground, the dataset grid and its prompt summary)
and nowhere else: credit holds and settles, the gateway, evaluation charges
(dataset, playground, SDK, Observe) and the hosted harness keep today's
price. A new caller that passes organization_id= fails here until it is
added on purpose. No product code calls judge_model_cost (evaluation charges
keep the judge's reported cost).
"""

import ast
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {"tests", "migrations", "__pycache__", "node_modules", ".venv"}

ALLOWED_ORGANIZATION_CALLERS = {
    # The rule itself (judge_model_cost, used by no charge).
    "agentic_eval/core_evals/fi_utils/token_count_helper.py",
    # Displayed cell cost: chat handler, custom HTTP endpoint, legacy path.
    "agentic_eval/core_evals/run_prompt/runprompt_handlers/utils/response_formatter.py",
    "agentic_eval/core_evals/run_prompt/runprompt_handlers/handlers/custom_model_handler.py",
    "agentic_eval/core_evals/run_prompt/litellm_response.py",
    # Displayed totals: the grid's column header and the prompt summary.
    "model_hub/views/develop_dataset.py",
    "tfc/utils/functions.py",
}


def product_files(needle):
    for directory, subdirs, files in os.walk(ROOT):
        subdirs[:] = [d for d in subdirs if d not in SKIP_DIRS]
        for name in files:
            if not name.endswith(".py") or name.startswith("test_"):
                continue
            if name == "conftest.py":
                continue
            path = Path(directory) / name
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if needle in text:
                yield path, text


def called_name(call):
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def callers(needle, *, keyword=None):
    found = set()
    for path, text in product_files(needle):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or called_name(node) != needle:
                continue
            if keyword is None or any(k.arg == keyword for k in node.keywords):
                found.add(path.relative_to(ROOT).as_posix())
    return found


def test_only_display_callers_price_a_custom_model_at_its_saved_price():
    assert (
        callers("calculate_total_cost", keyword="organization_id")
        == ALLOWED_ORGANIZATION_CALLERS
    )


def test_no_charge_calls_the_judge_price_rule():
    assert callers("judge_model_cost") == set()


def test_only_the_cost_function_reads_a_custom_models_saved_price():
    assert callers("custom_model_pricing") == {
        "agentic_eval/core_evals/fi_utils/token_count_helper.py"
    }
