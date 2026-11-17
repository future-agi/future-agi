"""Dated model prices, laid over the catalogue's static ones from each row's date.

A copy of agentcc-gateway/internal/modeldb/price_schedule.json: the backend image
holds only futureagi/. futureagi/tests/test_price_schedule.py keeps them equal.
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

PRICE_SCHEDULE_PATH = Path(__file__).with_name("price_schedule.json")

# strptime alone accepts "2027-1-1"; the file's dates are always zero-padded.
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_BILLED_RATE_NAMES = {
    "input_cost_per_token": "input_per_1M_tokens",
    "output_cost_per_token": "output_per_1M_tokens",
    "cache_read_input_token_cost": "cached_input_per_1M_tokens",
}
# Every price a row carries, as the gateway's loader requires them. Only the
# first three bill here; the batch prices are checked so the two loaders agree.
_PRICE_FIELDS = (
    "input_cost_per_token",
    "output_cost_per_token",
    "cache_read_input_token_cost",
    "input_cost_per_token_batches",
    "output_cost_per_token_batches",
)


class PriceScheduleError(RuntimeError):
    """The dated price file is missing or malformed."""


def load_price_schedule(
    path: Path,
) -> dict[str, tuple[tuple[datetime, dict[str, float]], ...]]:
    """Read the dated price file into per-model rows, oldest first.

    Each row is its start (midnight UTC of its date) and the per-1M rates it bills.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PriceScheduleError(
            f"The dated price file {path} is missing. It must be a copy of "
            "agentcc-gateway/internal/modeldb/price_schedule.json."
        ) from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PriceScheduleError(
            f"The dated price file {path} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(data, dict) or not data:
        raise PriceScheduleError(
            f"The dated price file {path} must hold an object of model keys"
        )

    schedule = {}
    for key, rows in data.items():
        if not isinstance(rows, list):
            raise PriceScheduleError(
                f"The dated price file {path} entry {key!r} must hold a list of rows."
            )
        if not rows:
            raise PriceScheduleError(
                f"The dated price file {path} entry {key!r} has no rows."
            )
        parsed = []
        for index, row in enumerate(rows):
            where = f"The dated price file {path} entry {key!r} row {index}"
            if not isinstance(row, dict):
                raise PriceScheduleError(f"{where} must be an object.")
            if "from" not in row:
                raise PriceScheduleError(f"{where} is missing from.")
            value = row["from"]
            try:
                if not isinstance(value, str) or not _DATE.fullmatch(value):
                    raise ValueError(value)
                start = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
            except ValueError:
                raise PriceScheduleError(
                    f"The dated price file {path} entry {key!r} row {index}: "
                    f"from {value!r} is not a YYYY-MM-DD date."
                ) from None
            if parsed and start <= parsed[-1][0]:
                raise PriceScheduleError(
                    f"The dated price file {path} entry {key!r} row {index}: "
                    "rows must start on strictly later dates."
                )
            rates = {}
            for field in _PRICE_FIELDS:
                if field not in row:
                    raise PriceScheduleError(f"{where} is missing {field}.")
                price = row[field]
                if isinstance(price, bool) or not isinstance(price, int | float):
                    raise PriceScheduleError(f"{where}: {field} must be a number.")
                if not price > 0:
                    raise PriceScheduleError(
                        f"{where}: {field} must be a positive price."
                    )
                rate_name = _BILLED_RATE_NAMES.get(field)
                if rate_name:
                    rates[rate_name] = round(price * 1_000_000, 12)
            parsed.append((start, rates))
        schedule[key] = tuple(parsed)
    return schedule


PRICE_SCHEDULE = load_price_schedule(PRICE_SCHEDULE_PATH)


def dated_rates(model_name: str, at: datetime) -> dict[str, float] | None:
    """The per-1M rates in force for a catalogue name at an aware instant.

    None when the name has no dated rows or ``at`` is before its first row.
    The dict is the schedule's own, shared by every caller: read it, never change it.
    """
    rates = None
    for start, row_rates in PRICE_SCHEDULE.get(model_name, ()):
        if start > at:
            break
        rates = row_rates
    return rates
