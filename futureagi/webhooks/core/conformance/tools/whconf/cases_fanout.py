"""The fan-out scenario of payloads.json: deliveries and per-endpoint sequences (DESIGN.md sections 7.6, 7.7;
contract section 5.5)."""

from __future__ import annotations

from . import reference as R
from .common import P1, WS, WS2


def fan_out_case() -> dict:
    fan = {
        "endpoints": [
            {
                "id": "whep_A",
                "workspace_id": WS,
                "status": "enabled",
                "subscriptions": [{"event_type": "prompt.*"}],
            },
            {
                "id": "whep_B",
                "workspace_id": WS,
                "status": "enabled",
                "subscriptions": [
                    {
                        "event_type": "prompt.label.changed",
                        "filters": {
                            "label_names": ["Production"],
                            "actions": ["moved", "assigned"],
                        },
                    }
                ],
            },
            {
                "id": "whep_C",
                "workspace_id": None,
                "status": "enabled",
                "accepts_run_targeting": True,
                "subscriptions": [{"event_type": "deployment.run.*"}],
            },
            {
                "id": "whep_D",
                "workspace_id": WS,
                "status": "disabled_auto",
                "next_seq": 41,
                "paused_count": 9999,
                "subscriptions": [{"event_type": "prompt.*"}],
            },
            {
                "id": "whep_E",
                "workspace_id": WS,
                "status": "enabled",
                "accepts_run_targeting": True,
                "subscriptions": [{"event_type": "prompt.version.created"}],
            },
            {
                "id": "whep_F",
                "workspace_id": WS2,
                "status": "enabled",
                "subscriptions": [{"event_type": "prompt.*"}],
            },
            {
                "id": "whep_G",
                "workspace_id": WS,
                "status": "enabled",
                "subscriptions": [
                    {
                        "event_type": "deployment.run.failed",
                        "filters": {"statuses": ["cancelled"]},
                    },
                    {"event_type": "deployment.run.completed"},
                ],
            },
            {
                "id": "whep_H",
                "workspace_id": WS,
                "status": "enabled",
                "accepts_run_targeting": True,
                "subscriptions": [
                    {
                        "event_type": "deployment.run.*",
                        "filters": {
                            "deployment_ids": ["dep-0123456789abcdef"],
                            "environments": ["production"],
                        },
                    }
                ],
            },
        ],
        "events": [
            {
                "id": "evt_1",
                "type": "prompt.version.created",
                "workspace_id": WS,
                "prompt_id": P1,
            },
            {
                "id": "evt_2",
                "type": "prompt.label.changed",
                "workspace_id": WS,
                "prompt_id": P1,
                "label_name": "Production",
                "action": "moved",
            },
            {
                "id": "evt_3",
                "type": "prompt.label.changed",
                "workspace_id": WS,
                "prompt_id": P1,
                "label_name": "Staging",
                "action": "moved",
            },
            {
                "id": "evt_4",
                "type": "deployment.run.completed",
                "workspace_id": WS,
                "deployment_id": "dep-3f9a0c1d2e4b5a69",
                "environment": "production",
                "trigger_kind": "api",
                "status": "succeeded",
                "targeted_endpoint_id": "whep_E",
            },
            {
                "id": "evt_5",
                "type": "deployment.run.failed",
                "workspace_id": WS,
                "deployment_id": "dep-3f9a0c1d2e4b5a69",
                "environment": "production",
                "trigger_kind": "cron",
                "status": "cancelled",
            },
            {
                "id": "evt_6",
                "type": "deployment.run.failed",
                "workspace_id": WS,
                "deployment_id": "dep-3f9a0c1d2e4b5a69",
                "environment": "production",
                "trigger_kind": "api",
                "status": "failed",
                "targeted_endpoint_id": "whep_C",
            },
            {
                "id": "evt_test_7",
                "type": "prompt.label.changed",
                "workspace_id": WS,
                "test": True,
                "test_endpoint_id": "whep_A",
            },
            {
                "id": "evt_8",
                "type": "prompt.version.created",
                "workspace_id": WS2,
                "prompt_id": P1,
            },
            {
                "id": "evt_9",
                "type": "deployment.run.failed",
                "workspace_id": WS,
                "deployment_id": "dep-3f9a0c1d2e4b5a69",
                "environment": "production",
                "trigger_kind": "api",
                "status": "interrupted",
                "targeted_endpoint_id": "whep_H",
            },
            {
                "id": "evt_10",
                "type": "deployment.run.failed",
                "workspace_id": WS,
                "deployment_id": "dep-3f9a0c1d2e4b5a69",
                "environment": "preview",
                "trigger_kind": "api",
                "status": "failed",
                "targeted_endpoint_id": "whep_H",
            },
            {
                "id": "evt_11",
                "type": "prompt.label.changed",
                "workspace_id": WS,
                "prompt_id": P1,
                "label_name": "Production",
                "action": "removed",
            },
        ],
    }
    return {"input": fan, "expected": R.fan_out(fan)}
