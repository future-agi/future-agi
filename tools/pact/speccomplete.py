"""
pact spec complete -- fill in the TODO stubs in a generated TLA+ skeleton.

Uses the Anthropic API (claude-haiku-4-5 by default) to:
  1. Understand the Python source semantics
  2. Complete the TODO sections in the skeleton
  3. Add liveness properties (SF vs WF) and domain invariants

The result is a spec that's ready for TLC model checking.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .specgen import synthesize as _spec_synthesize


_SYSTEM = (
    "You are a TLA+ specification expert. Your job is to complete a partially"
    " generated TLA+ spec by filling in every TODO comment.\n\n"
    "Rules:\n"
    "- Preserve VARIABLES, TypeInvariant, and UniqueConstraint invariants exactly.\n"
    "- For each Action, replace the TODO precondition stub with a realistic"
    " ENABLED guard derived from the Python source semantics.\n"
    "- Replace TODO in Init with a realistic initial state.\n"
    "- Replace WF_vars(Next) with per-action WF/SF as appropriate:\n"
    "    WF (weak fairness) for tasks that run when continuously enabled;\n"
    "    SF (strong fairness) for retry loops or transiently-enabled tasks.\n"
    "- Add a PROPERTY section after INVARIANT with at least one temporal property,\n"
    "  e.g. liveness: every submitted item is eventually processed.\n"
    "- Output ONLY the completed TLA+ spec. No explanation, no markdown fences.\n"
    "- The output must be syntactically valid TLA+.\n"
)


def _build_user_msg(filename: str, source: str, skeleton: str) -> str:
    """Build the LLM prompt without using str.format() on user-supplied content.

    str.format() raises KeyError when source or skeleton contain bare braces
    (dict literals, f-strings, TLA+ set expressions like {"NULL"}).
    """
    return (
        f"## Python source ({filename})\n\n"
        "```python\n"
        + source
        + "\n```\n\n"
        "## TLA+ skeleton to complete\n\n"
        "```tla\n"
        + skeleton
        + "\n```\n\n"
        "Complete the spec. Output only the TLA+ text.\n"
    )


def spec_complete(
    path: Path,
    output: Optional[Path] = None,
    model: str = "claude-haiku-4-5-20251001",
    api_key: Optional[str] = None,
) -> str:
    """
    Generate a TLA+ skeleton for `path` then ask the LLM to fill in the TODOs.

    Returns the completed spec; writes to `output` if provided.
    Raises RuntimeError if the API key is missing.
    """
    source = path.read_text(encoding="utf-8")
    # Drive synthesize directly so the file is read only once (spec_gen would re-read it).
    module_name = "".join(w.capitalize() for w in path.stem.split("_"))
    skeleton = _spec_synthesize(source, module_name)

    key = api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("PACT_ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. "
            "Export it or pass --api-key to `pact spec complete`."
        )

    import anthropic
    client = anthropic.Anthropic(api_key=key)

    user_msg = _build_user_msg(
        filename=path.name,
        source=source[:8000],   # stay within context; models.py rarely exceeds this
        skeleton=skeleton,
    )

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )
    if not response.content:
        raise RuntimeError("API returned empty content — check model, key, and rate limits")
    completed = response.content[0].text.strip()

    # Strip markdown code fences if the model adds them
    if completed.startswith("```"):
        lines = completed.splitlines()
        completed = "\n".join(
            line for line in lines
            if not line.startswith("```")
        ).strip()

    if output:
        output.write_text(completed, encoding="utf-8")

    return completed
