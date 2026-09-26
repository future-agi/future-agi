"""Enterprise model catalog (OSS base + EE-only additions).

The merge happens once in the OSS module: when ``ee.prompts.additional_models``
is importable, ``agentic_eval.core_evals.run_prompt.available_models`` already
folds the EE delta into ``AVAILABLE_MODELS``. We simply re-export it here so
there is a single place that computes the catalog.
"""

from agentic_eval.core_evals.run_prompt.available_models import AVAILABLE_MODELS

__all__ = ["AVAILABLE_MODELS"]
