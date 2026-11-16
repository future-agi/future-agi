"""Keys and codes the hosted harness's ingestion and the worker record share."""

# Where a call keeps what changed in the world during its trial, per participant, and what each actor decided.
TRIAL_STATE_KEY = "harness_trial_state"
# Failure codes of a trial the harness held as the scenario's problem, not the agent's: its checks were never graded,
# so no platform evaluation grades it either.
SCENARIO_FAILURE_CODES = frozenset({"actor_missing", "worker_limits_not_enforced"})
# The cancel reason and failure code of a run the user stopped; a fix ending or an environment delete has its own.
USER_CANCELED = "user_canceled"
