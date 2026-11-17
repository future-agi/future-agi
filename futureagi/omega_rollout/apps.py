"""Controlled rollout of the hosted Omega products (READINESS 3.13, 1.10a)."""

from django.apps import AppConfig


class OmegaRolloutConfig(AppConfig):
    name = "omega_rollout"
    label = "omega_rollout"
    verbose_name = "Omega rollout"
    default_auto_field = "django.db.models.BigAutoField"
