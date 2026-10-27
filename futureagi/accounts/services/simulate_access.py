"""Which Simulate pages an organization can use.

Orgs created at or after ``RESTRICT_SIMULATE_TO_ENVIRONMENTS_CUTOFF`` see only
Environments; Agent Definition, Scenarios, Personas and Run Simulation are
hidden from them in the UI. Their APIs stay open, since Environments uses
scenario and persona generation itself.
"""

from django.conf import settings


def restrict_simulate_to_environments(organization) -> bool:
    cutoff = settings.RESTRICT_SIMULATE_TO_ENVIRONMENTS_CUTOFF
    return cutoff is not None and organization.created_at >= cutoff
