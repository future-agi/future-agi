"""Packet E seed ids for the read sweep (ai_tools/tests/verify_bridges.py).

Used as the third id-resolution fallback (after binding.id_source and
sibling-list pairing) — required for detail tools whose route carries EXTRA
path kwargs (the id_source mechanism can only fill the pk field).

Values: bare id string (passed as the tool's pk_field) or a full params dict.

Harvested from ws1 (org of verify_bridges.USER_EMAIL, 2026-06-10), plus the
deterministic packet-e-writecheck fixture ids staged by live/writes_e.py —
run ``python -m ai_tools.tests.verify_writes`` once first so those rows exist.

Known NODATA (no org rows to seed; documented):
- get_ground_truth_data / get_ground_truth_status — no EvalGroundTruth rows;
  creating one requires the deferred multipart upload (GroundTruthUploadView).
- get_error_localization_results — needs a dataset eval cell that ran the
  error localizer; stage via the datasets cluster before sweeping.
- get_composite_eval — id_source returns the newest template, which may not
  be composite; seed a composite template id here once one exists.
"""

from ai_tools.tests.live.writes_e import (
    ITEM_ANNOTATE_ID,
    QUEUE_ID,
)

# Harvested ws1 ids.
_OPTIMIZATION_ID = "c2c6d138-562f-4989-a404-45082d512514"
_TRIAL_ID = "c76c1792-f37e-4fb8-ba43-a16e103c850f"
_VERSIONED_TEMPLATE_ID = "ff36cf02-77e9-4dae-bea9-577e839c79c9"

_TRIAL_PARAMS = {"optimization_id": _OPTIMIZATION_ID, "trial_id": _TRIAL_ID}
_FIXTURE_ITEM = {"queue_id": QUEUE_ID, "item_id": ITEM_ANNOTATE_ID}

SEED_IDS = {
    # Optimization trial detail tools (pk + trial_id path kwarg).
    "get_optimization_trial": dict(_TRIAL_PARAMS),
    "get_trial_prompt": dict(_TRIAL_PARAMS),
    "get_trial_scenarios": dict(_TRIAL_PARAMS),
    "get_trial_evaluations": dict(_TRIAL_PARAMS),
    # Annotator loop reads (queue_id path kwarg; fixture from writes_e).
    "get_next_queue_item": {"queue_id": QUEUE_ID},
    "get_queue_item_annotate_detail": dict(_FIXTURE_ITEM),
    "list_queue_item_annotations": dict(_FIXTURE_ITEM),
    # Version compare needs two version NUMBERS on a versioned template.
    "compare_eval_template_versions": {
        "eval_template_id": _VERSIONED_TEMPLATE_ID,
        "a": "1",
        "b": "1",
    },
}
