"""The two keys that mark a simulated span.

A simulated call's span carries the span attribute ``SIMULATION_MARK_KEY`` (the call
execution's id), and every span a code agent's sandboxed process exports carries the
resource attribute ``RESOURCE_MARK_KEY``. Simulated traces are stored in the agent's
hidden simulation project, so these keys group a run's traces by call; they no longer
decide what a project hides. Both are matched case-sensitively.
"""

SIMULATION_MARK_KEY = "fi.simulator.call_execution_id"
RESOURCE_MARK_KEY = "fi.simulation.origin"
