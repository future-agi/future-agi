'''Model‑related API helpers.'''

from typing import Dict
from futureagi.pricing import get_model_rate, PriceStatus

def calculate_cost(model_id: str, token_count: int) -> Dict[str, object]:
    """
    Calculate the cost of a request for ``model_id`` given ``token_count``.

    The function returns a dictionary that includes the monetary amount and a
    ``price_status`` field so the frontend can display the appropriate label.
    """
    rate, status = get_model_rate(model_id)

    if rate is None:
        amount = 0.0
        price_status = PriceStatus.UNAVAILABLE.value
    else:
        # Rates are per million tokens.
        amount = rate * (token_count / 1_000_000)
        price_status = status.value

    return {
        "model_id": model_id,
        "token_count": token_count,
        "amount": round(amount, 6),
        "price_status": price_status,
    }
