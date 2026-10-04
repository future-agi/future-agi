"""Attempt-stable usage events for completed Jev evaluations."""

from ee.jev.mapping import mapping_revision
from ee.jev.models import JEV_PROVIDER, JEV_SERVICE
from ee.usage.schemas.events import UsageEvent


def build_usage_events(evaluator, *, org_id, cell_or_run_id, amount, attempt_offset=0):
    """Preserve token units for each attempt; apply the existing fee once."""
    actual = evaluator.last_jev or {}
    events = []
    for record in evaluator.client.attempts:
        attempt = attempt_offset + record["attempt"]
        success = record["outcome"] == "success"
        usage = record.get("usage") or {}
        events.append(
            UsageEvent(
                event_id=f"jev:{cell_or_run_id}:{attempt}",
                org_id=str(org_id),
                event_type="managed_ai_credits_monthly",
                amount=amount if success else 0,
                properties={
                    "provider": JEV_PROVIDER,
                    "service": JEV_SERVICE,
                    "requested_model": evaluator._model,
                    "actual_model": actual.get("actual_model") if success else None,
                    "mapping_revision": mapping_revision(evaluator.mapping),
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "attempt": attempt,
                    "outcome": record["outcome"],
                },
            )
        )
    return events
