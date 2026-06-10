# Suppress Hypothesis health-check database scan that inflates startup time.
from hypothesis import settings, HealthCheck
settings.register_profile("pact", suppress_health_check=list(HealthCheck), deadline=None)
settings.load_profile("pact")
