"""Keys and codes the hosted harness's ingestion and the worker record share."""

# Where a call keeps what changed in the world during its trial, per participant, and what each actor decided.
TRIAL_STATE_KEY = "harness_trial_state"
# The trial notice ALK seals when a separately wired worker reached the world on the shared address.
WORKER_CHANNEL_BYPASSED = "worker_channel_bypassed"
# How the world holds a worker to its limits, and the words the cards show for it.
WORKER_ENFORCEMENT_LABELS = {
    "held_at_world": "Limits enforced: this participant's tools and deadline are checked on every call",
    "not_enforced": "Limits can't be enforced: this participant runs inside the app, so its calls can't be told apart",
    "bypassed": "Limits skipped in this run: its calls went through the main app",
    "unknown": "Not confirmed yet: no run has shown this participant's own calls",
}
# The trial notice ALK seals when a scenario relies on worker limits this environment cannot hold.
WORKER_LIMITS_NOT_ENFORCED = "worker_limits_not_enforced"
# The trial notice ALK seals when some of a trial's hand-offs could not be recorded.
HAND_OFFS_INCOMPLETE = "hand_offs_incomplete"
# Failure codes of a trial the harness held as the scenario's problem, not the agent's: its checks were never graded,
# so no platform evaluation grades it either.
SCENARIO_FAILURE_CODES = frozenset({"actor_missing", "worker_limits_not_enforced"})
# The cancel reason and failure code of a run the user stopped; a fix ending or an environment delete has its own.
USER_CANCELED = "user_canceled"
