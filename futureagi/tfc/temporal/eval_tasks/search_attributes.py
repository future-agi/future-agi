"""Custom Temporal Search Attributes + workflow label/phase constants.

Search Attributes are typed, indexed, and queryable in the Temporal UI's list
filter. The keys are cluster-global and must be **registered once** before any
workflow upserts them (see ``registration.py`` + the
``register_eval_task_search_attributes`` command) — upserting an unregistered
attribute wedges the workflow task forever.

This module only imports the sandbox-safe ``SearchAttributeKey`` so it can be
imported from ``workflows.py`` inside the Temporal sandbox; the registration
enum lives in ``registration.py``.
"""

from temporalio.common import SearchAttributeKey

# Prefixed to avoid collisions in the cluster-global attribute namespace.
ORG_ID = SearchAttributeKey.for_keyword("EvalTaskOrgId")
PROJECT_ID = SearchAttributeKey.for_keyword("EvalTaskProjectId")
RUN_TYPE = SearchAttributeKey.for_keyword("EvalTaskRunType")
TASK_STATUS = SearchAttributeKey.for_keyword("EvalTaskStatus")

ALL_KEYS = [ORG_ID, PROJECT_ID, RUN_TYPE, TASK_STATUS]
SEARCH_ATTRIBUTE_NAMES = [key.name for key in ALL_KEYS]

# TaskStatus SA values — mirror tracer.models.eval_task.EvalTaskStatus (the
# workflow can't import the Django enum inside the sandbox).
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_PAUSED = "paused"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

# Phase query values.
PHASE_STARTING = "starting"
PHASE_MATERIALIZING = "materializing"
PHASE_DRAINING = "draining"
PHASE_SLEEPING = "sleeping"
PHASE_DONE = "done"
