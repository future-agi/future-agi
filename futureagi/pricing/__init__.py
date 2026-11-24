'''Pricing utilities for Future AGI.'''

from enum import Enum
from typing import Tuple, Optional

class PriceStatus(str, Enum):
    """Enumeration of price lookup status."""
    KNOWN = "KNOWN"
    ESTIMATE = "ESTIMATE"
    UNAVAILABLE = "UNAVAILABLE"

# Simple in‑memory pricing catalog.
# Rate is expressed in USD per million tokens.
_PRICING_CATALOG = {
    # Known model with explicit rate.
    "cohere-embed-v2": (0.10, PriceStatus.KNOWN),
    # Example of an estimated price (could be populated later).
    # "some-model": (0.05, PriceStatus.ESTIMATE),
}

def get_model_rate(model_id: str) -> Tuple[Optional[float], PriceStatus]:
    """
    Retrieve the per‑million‑token rate for a given model.

    Returns:
        (rate, status) where ``rate`` is ``None`` when the model is not present
        in the catalog and ``status`` indicates why the rate is missing.
    """
    entry = _PRICING_CATALOG.get(model_id)
    if entry is None:
        return None, PriceStatus.UNAVAILABLE
    rate, status = entry
    return rate, status
