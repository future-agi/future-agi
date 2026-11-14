"""Render standalone catalog bodies from the shared lexical implementation.

Run from futureagi/: python -m model_hub.system_evals.render_lexical_scores
Use --check in tests/CI. Only the generated code block is replaced; metadata is
maintained in YAML. No backend imports or model calls are needed to render.
"""

import argparse
import ast
from pathlib import Path
from textwrap import indent

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parents[1] / "agentic_eval/core_evals/fi_evals/function/rag_scores.py"
NAMES = (
    "context_precision_score",
    "context_recall_score",
    "faithfulness_score",
    "noise_sensitivity",
)


def render(name):
    source = SOURCE.read_text()
    tree = ast.parse(source)
    function_name = f"calculate_{name}"
    helpers = {"lexical_contexts", "lexical_tokens", function_name}
    if name.startswith("context_"):
        helpers.add("overlap_threshold")
    statements = [
        ast.get_source_segment(source, node)
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        or isinstance(node, ast.FunctionDef)
        and node.name in helpers
    ]
    if name.startswith("context_"):
        arguments = (
            'hypothesis=kwargs.get("hypothesis", output), '
            'reference=kwargs.get("reference", expected), '
            'min_token_overlap=kwargs.get("min_token_overlap", '
            + ("0.6" if name == "context_precision_score" else "0.7")
            + ")"
        )
        # Passing aliases too preserves the Python API's None-as-absent rule.
        arguments += ", output=output, expected=expected"
        if name == "context_precision_score":
            arguments += ', k=kwargs.get("k")'
    elif name == "faithfulness_score":
        arguments = "output=output, context=context"
    else:
        arguments = "output=output, expected=expected, context=context"
    statements.append(f"result = {function_name}({arguments})")
    statements.append('return {"score": result["result"], "reason": result["reason"]}')
    return "def evaluate(input, output, expected, context, **kwargs):\n" + indent(
        "\n\n".join(statements) + "\n", "    "
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for name in NAMES:
        path = ROOT / "function" / f"{name}.yaml"
        current = path.read_text()
        metadata = current.split("  code: |\n", 1)[0]
        generated = metadata + "  code: |\n" + indent(render(name), "    ")
        if args.check:
            if current != generated:
                stale.append(name)
        else:
            path.write_text(generated)
    if stale:
        parser.exit(1, f"Stale lexical catalog bodies: {', '.join(stale)}\n")


if __name__ == "__main__":
    main()
