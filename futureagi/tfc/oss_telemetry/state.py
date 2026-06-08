from __future__ import annotations

import uuid

from django.db import IntegrityError, transaction

from tfc.oss_telemetry.models import OSSTelemetryState


def get_or_create_telemetry_state() -> OSSTelemetryState:
    try:
        with transaction.atomic():
            state, _ = OSSTelemetryState.objects.select_for_update().get_or_create(
                singleton_key=1,
                defaults={"instance_id": uuid.uuid4()},
            )
            return state
    except IntegrityError:
        with transaction.atomic():
            return OSSTelemetryState.objects.select_for_update().get(singleton_key=1)
