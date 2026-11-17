"""Test support for suites that exercise an Omega product and not its rollout: admit an org to the
hosted products the way a staff entry at S0 does, without an operator or an audit trail.

Never imported by product code. The rollout itself is tested in omega_rollout/tests.
"""

from __future__ import annotations

from omega_rollout import products as p


def admit_for_tests(organization, products=p.HOSTED_PRODUCTS) -> None:
    from omega_rollout.models import ProductAccess, ProductRollout

    for product in products:
        ProductRollout.objects.get_or_create(product=product)
        ProductAccess.objects.update_or_create(
            product=product,
            organization=organization,
            workspace=None,
            defaults={
                "effect": p.EFFECT_ALLOW,
                "cohort": p.COHORT_STAFF,
                "reason": "test",
            },
        )


def declare_released(settings, parts=None) -> None:
    """Declare released parts (omega_rollout.readiness) for a suite that tests a product, not its
    readiness: every declared part by default. The readiness rules are tested in
    omega_rollout/tests/test_readiness.py and omega_hub/tests/test_readiness_gates.py."""
    from omega_rollout import readiness

    settings.OMEGA_RELEASED_PARTS = ",".join(
        readiness.DECLARED_PARTS if parts is None else parts
    )
