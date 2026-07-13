from __future__ import annotations

import uuid

from django.db import IntegrityError, transaction

from tfc.deployment_telemetry.models import DeploymentTelemetryState


def get_or_create_telemetry_state() -> DeploymentTelemetryState:
    try:
        with transaction.atomic():
            state, _ = (
                DeploymentTelemetryState.objects.select_for_update().get_or_create(
                    singleton_key=1,
                    defaults={"instance_id": uuid.uuid4()},
                )
            )
            return state
    except IntegrityError:
        with transaction.atomic():
            return DeploymentTelemetryState.objects.select_for_update().get(
                singleton_key=1
            )
