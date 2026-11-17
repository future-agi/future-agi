"""Model id normalization shared by the gates, model config lookup and metering.

No imports, so any module can use it without an import cycle.
"""

from __future__ import annotations


def base_model_name(model_name: object, *, lower: bool = True) -> str:
    """The base model a model id names: its last path segment, so a
    provider-prefixed id ("x/turing_small") is treated as its base model
    ("turing_small"). A plain name is returned as it is.

    Lowercased unless ``lower=False``. Empty for an empty or missing name.
    """
    if not model_name:
        return ""
    name = str(model_name).rsplit("/", 1)[-1]
    return name.lower() if lower else name
