"""Seed one built, versioned Environment the way production gets one, with no sandbox.

``e2e/lib/simulate-seed.ts`` copies this file into the stack's backend container and
runs it there. The stack starts no simulation runner, so an Environment cannot be
built for real. Instead this does what a finished build leaves behind and what the
versions backfill reads, as the backend tests build one
(``simulate/tests/test_harness_versioning_backfill.py::_legacy``):

1. a hosted job, its attempt and its provisioned scenarios
   (``create_hosted_job`` -> ``register_attempt`` -> ``provision_scenarios``),
2. its authoring archive in object storage, the job marked completed,
3. ``backfill_environment``, which records the agent, environment and suite versions,
   the suite members and, for a sealed archive, the certified bundle of that pairing.

A ``certified`` archive carries the sealed bundle a build certifies: a certificate
whose checks all passed, and per-tool and per-action certification files. Without it
the pairing has no bundle, so its first check is a validation. ``production_only``
names a tool the contract says reaches production. ``failed_check`` names an action
an uncertified pairing's last validation failed on: its bundle is left failed by that
check, as a validation leaves it (``validation._settle_bundle``).

Every row belongs to the given organization and workspace. Prints one JSON line.
"""

import json
import os
import sys

TOOLS = ("lookup_order", "issue_refund")
SUB_GOALS = [
    {
        "name": "looked_up",
        "check": (
            "def check(world, calls):\n"
            "    if not any(c.name == 'lookup_order' for c in calls):\n"
            "        return 'the order was never looked up'\n"
        ),
    },
]
# The same tools as a build that read their fields writes them: inputs with types and units, what each returns, and
# where each runs (the agent's own tools service, so the world's door stands in front of both).
DETAILED_TOOLS = [
    {
        "name": "lookup_order",
        "description": "Looks an order up.",
        "args": ["order_id"],
        "arg_types": {"order_id": "string"},
        "returns": {"status": "string", "total": "integer, cents, USD"},
    },
    {
        "name": "issue_refund",
        "description": "Refunds an order.",
        "args": ["order_id", "refund_amount"],
        "arg_types": {"order_id": "string", "refund_amount": "integer"},
        "arg_units": {"refund_amount": "cents, USD"},
        "returns": {"refund_id": "string", "amount": "integer, cents, USD"},
        "effects": "adds a refund row and marks the order refunded",
    },
]
DETAILED_ENTRYPOINTS = [
    {"tool": "lookup_order", "mode": "service", "service": "tools-api", "endpoint": "orders/lookup", "effect": "read"},
    {"tool": "issue_refund", "mode": "service", "service": "tools-api", "endpoint": "orders/refund", "effect": "write"},
]
# The checks a build's certificate records, all passed.
PASSED_CHECKS = {
    "static": "passed",
    "processes": "passed",
    "chat_ingress": "passed",
    "schema_and_seed": "passed",
    "tool_contract": "2/2",
    "action_behavior": "2/2",
    "reset_equivalence": "passed",
    "world_isolation": "passed",
}
BUNDLE_DIGEST = "sha256:" + "d" * 64
# A world actor with no channel: it never speaks, it acts in the world (approves refunds).
WORLD_ACTOR = {
    "id": "claims_supervisor",
    "name": "Dana Brooks",
    "role": "claims supervisor who approves refunds",
    "kind": "approver",
    "permitted_actions": ["approvals.put"],
}
# What a finished trial left in World changes: the agent read the order, then committed one refund.
TRIAL_STATE = {
    "state_changes": [
        {
            "who": "agent",
            "name": "the agent",
            "attempted": 2,
            "committed": 1,
            "refused": 0,
            "faulted": 0,
            "duplicate": 0,
            "committed_actions": ["issue_refund"],
            "read": 1,
            "read_actions": ["lookup_order"],
            "no_change": False,
        }
    ]
}
# The same trial when the scenario's test condition took the refund down: the read went through, the refund was faulted
# and nothing was committed.
FAULTED_TRIAL_STATE = {
    "start": {"state": "seeded", "tables": []},
    "records": {"changes": [], "skipped": [], "truncated": 0},
    "state_changes": [
        {
            "who": "agent",
            "name": "the agent",
            "attempted": 2,
            "committed": 0,
            "refused": 0,
            "faulted": 1,
            "duplicate": 0,
            "committed_actions": [],
            "read": 1,
            "read_actions": ["lookup_order"],
            "no_change": True,
        }
    ],
}
# The same trial when the world actor acted during the call: the agent asked for an approval and Dana Brooks put it.
ACTOR_TRIAL_STATE = {
    "start": {"state": "seeded", "tables": []},
    "decisions": [
        {
            "play": "live",
            "actor": WORLD_ACTOR["id"],
            "action": "approvals.put",
            "parent": None,
            "status": 200,
            "outcome": "committed",
            "arguments": {"kind": "refund", "status": "approved"},
        }
    ],
    "state_changes": [
        TRIAL_STATE["state_changes"][0],
        {
            "who": WORLD_ACTOR["id"],
            "name": WORLD_ACTOR["name"],
            "attempted": 1,
            "committed": 1,
            "refused": 0,
            "faulted": 0,
            "duplicate": 0,
            "committed_actions": ["approvals.put"],
        },
    ],
}
# The same trial when the refund's answer was lost to a timeout and the agent issued it again: the world committed it
# twice, and the trial fails on it.
DUPLICATE_TRIAL_STATE = {
    "start": {"state": "seeded", "tables": []},
    "duplicates": [
        {
            "action": "issue_refund",
            "name": "issue_refund",
            "arguments": {"order_id": "A-100", "amount_cents": 2500},
            "first_action_id": "act-1",
            "repeat_action_id": "act-2",
        }
    ],
    "state_changes": [
        {
            **TRIAL_STATE["state_changes"][0],
            "attempted": 3,
            "committed": 2,
            "duplicate": 1,
            "committed_actions": ["issue_refund", "issue_refund"],
        }
    ],
}
# The same trial when the agent asked Dana Brooks to approve the refund, she refused, and the agent issued it anyway.
REFUSED_TRIAL_STATE = {
    "start": {"state": "seeded", "tables": []},
    "approvals": [
        {
            "request": {"action": "approvals.request", "by": "agent"},
            "decision": {"action": "approvals.put", "by": WORLD_ACTOR["id"]},
            "before_decision": [],
            "after_decision": [{"action": "issue_refund", "outcome": "committed"}],
            "acted_before_decision": False,
        }
    ],
    "decisions": [
        {
            "play": "live",
            "actor": WORLD_ACTOR["id"],
            "name": WORLD_ACTOR["name"],
            "action": "approvals.put",
            "status": 200,
            "outcome": "committed",
            "arguments": {"kind": "refund", "status": "refused"},
            "verdict": "refused",
            "committed_after_refusal": True,
        }
    ],
    "state_changes": [
        TRIAL_STATE["state_changes"][0],
        {
            "who": WORLD_ACTOR["id"],
            "name": WORLD_ACTOR["name"],
            "attempted": 1,
            "committed": 1,
            "refused": 0,
            "faulted": 0,
            "duplicate": 0,
            "committed_actions": ["approvals.put"],
        },
    ],
}
TRIAL_STATES = {
    True: TRIAL_STATE,
    "read_write": TRIAL_STATE,
    "fault": FAULTED_TRIAL_STATE,
    "actor": ACTOR_TRIAL_STATE,
    "duplicate": DUPLICATE_TRIAL_STATE,
    "refused": REFUSED_TRIAL_STATE,
}


def _payload(count):
    return {
        "schema_version": "futureagi.harness-job.v1",
        "source": {
            "kind": "github",
            "repository": "acme/agent",
            "ref": "main",
            "visibility": "public",
        },
        "agent": {"connector": "auto", "config": {}, "secret_refs": {}},
        "scenario_count": count,
        "seed": 42,
        "runtime": {
            "isolation": "dedicated_vm",
            "cpu_units": 4,
            "memory_mb": 8192,
            "parallelism": 1,
            "concurrency_weight": 1,
            "max_duration_seconds": 600,
            "network_policy": "live",
        },
        "security": {
            "untrusted_source": True,
            "read_only_source": True,
            "allow_privileged": False,
            "allow_host_runtime_control": False,
            "allowed_egress_domains": ["api.example.com"],
        },
        "retry": {
            "max_infrastructure_attempts": 2,
            "initial_backoff_seconds": 1,
            "max_backoff_seconds": 15,
            "retryable_domains": ["infrastructure"],
        },
        "artifacts": {
            "level": "full",
            "retention_days": 30,
            "allow_bundle_download": False,
            "max_artifact_bytes": 1024,
        },
        "metadata": {},
    }


def _scenario(key, tools):
    return {
        "name": key,
        "scenario_key": key,
        "persona": {"name": f"caller of {key}"},
        "instruction": f"call about {key}",
        "solution": [{"tool": tool, "arguments": {}} for tool in tools],
        "sub_goals": ["looked_up"],
        "max_turns": 6,
    }


def _with_actor(documents):
    """Each refund includes the world actor, brought in once a refund approval is asked for."""
    for document in documents:
        if document["scenario_key"].startswith("refund-"):
            document["actors"] = [
                {
                    "id": WORLD_ACTOR["id"],
                    "instruction": "Approve the refund when the agent asks.",
                    "trigger": {"kind": "conditional", "collection": "approvals", "where": {"kind": "refund"}},
                }
            ]
    return documents


def _documents(refunds, lookups, suffix, *, refund_worker_budget=False):
    """``refunds`` cases that issue a refund on their only path, then ``lookups`` that
    only look the order up; numbered in that order. ``refund_worker_budget`` gives each
    refund case a budget for the refund worker, so it relies on that worker."""
    budget = {"worker_budget_seconds": {"refund": 30}} if refund_worker_budget else {}
    return [
        {**_scenario(f"refund-{suffix}-{n:03d}", TOOLS), **budget} for n in range(1, refunds + 1)
    ] + [
        _scenario(f"status-{suffix}-{n:03d}", TOOLS[:1]) for n in range(1, lookups + 1)
    ]


# Two declared workers wired separately, each reaching the world through its own channel. ``workers`` picks what ALK
# sealed for them, as it writes it: ``"sealed"`` a tool proxy holding per-worker limits; ``"not_fronted"`` no door,
# the tool service not behind it, so ALK holds each refund case that relies on the refund worker; ``"old_seal"``
# nothing, as a bundle sealed before worker limits.
WORKERS = [
    {
        "id": "order_lookup",
        "name": "Order-lookup worker",
        "tools": ["lookup_order", "issue_refund"],
        "allowed_tools": ["lookup_order"],
        "binding": "separate",
        "address_env": "ORDER_LOOKUP_API_URL",
    },
    {
        "id": "refund",
        "name": "Refund worker",
        "tools": ["issue_refund"],
        "allowed_tools": ["issue_refund"],
        "binding": "separate",
        "address_env": "REFUND_API_URL",
    },
]
DOOR_OUTCOMES = {
    "sealed": {"present": True, "surface": "tool_proxy", "worker_deadlines": "per_worker"},
    "not_fronted": {"present": False, "reason": "tool_service_not_fronted"},
}
TOOL_PROXY_FILES = {
    "environment-bundle/generated/tool-proxy/door.json": {"worker_deadlines": "per_worker"},
    "environment-bundle/generated/tool-proxy/permissions.json": {
        "worker:order_lookup": {"allow": ["POST orders.lookup"]},
        "worker:refund": {"allow": ["POST refunds.issue"]},
    },
    "environment-bundle/generated/tool-proxy/actions.json": {
        "POST orders.lookup": "lookup_order",
        "POST refunds.issue": "issue_refund",
    },
}


def _door_files(documents, workers):
    """ALK's door files for ``workers``: the root ``door.json``, ``worker-limits.json`` with each scenario's hold, and
    for a tool proxy its own files. A refund case with a budget relies on the refund worker."""
    if workers not in DOOR_OUTCOMES:
        return {}
    held = workers == "not_fronted"
    scenarios = {}
    for document in documents:
        relies = "worker_budget_seconds" in document
        scenarios[document["scenario_key"]] = {
            "workers": ["refund"] if relies else [],
            "relies_on_worker_limits": relies,
            "held": held and relies,
            "unheld_workers": ["refund"] if held and relies else [],
            "reason": "worker_limits_not_enforced" if held and relies else None,
            "cause": "tool_service_not_fronted" if held and relies else None,
            "limit": "all" if held and relies else None,
            "deadline": None,
        }
    files = {
        "environment-bundle/door.json": DOOR_OUTCOMES[workers],
        "environment-bundle/worker-limits.json": {
            "workers": {one["id"]: {"limited": True} for one in WORKERS},
            "scenarios": scenarios,
        },
    }
    if workers == "sealed":
        files.update(TOOL_PROXY_FILES)
    return files


def _authoring(documents, *, certified, production_only, actors=False, tool_detail=False, workers=None):
    contract = {"agent": "support", "tools": list(DETAILED_TOOLS if tool_detail else TOOLS)}
    if tool_detail:
        contract["tool_entrypoints"] = list(DETAILED_ENTRYPOINTS)
    if workers:
        # Reached on the agent's tool service, which the world's door fronts as tools-api.
        contract["tool_entrypoints"] = [
            {"tool": "lookup_order", "endpoint": "/orders/lookup", "method": "POST", "mode": "service", "service": "tools-api"},
            {"tool": "issue_refund", "endpoint": "/refunds/issue", "method": "POST", "mode": "service", "service": "tools-api"},
        ]
        contract["workers"] = WORKERS
    door_files = _door_files(documents, workers)
    if production_only:
        contract["tool_entrypoints"] = [
            *(one for one in contract.get("tool_entrypoints", []) if one["tool"] != production_only),
            {"tool": production_only, "mode": "in_process"},
        ]
        contract["dependencies"] = [
            {
                "name": "payments",
                "engine": "",
                "reached": {},
                "used_by": [production_only],
            }
        ]
    files = {
        "contract.json": contract,
        "sub_goals.json": {"sub_goals": SUB_GOALS},
        "scenarios.json": documents,
        "schema.sql": "create table orders(id int);",
        "handlers/lookup.py": "def handle(db):\n    return {}\n",
    }
    if actors:
        files["actors.json"] = {"schema": "futureagi.actors.v1", "actors": [WORLD_ACTOR]}
    for document in documents:
        files[f"scenarios/{document['name']}/scenario.json"] = document
        files[f"scenarios/{document['name']}/ready.py"] = (
            "def ready(world):\n    return True\n"
        )
    if certified:
        files.update(
            {
                "environment-bundle/manifest.json": {
                    "files": [
                        {"path": "world.sqlite", "sha256": "a" * 64, "size": 2},
                        *(
                            {
                                "path": path.removeprefix("environment-bundle/"),
                                "sha256": "a" * 64,
                                "size": 2,
                            }
                            for path in door_files
                        ),
                    ],
                    "digest": BUNDLE_DIGEST,
                    "provenance": {"source_digest": "e" * 64},
                },
                "runtime-validation.json": {
                    "status": "passed",
                    "compiler": {"bundle_digest": BUNDLE_DIGEST},
                    "checks": PASSED_CHECKS,
                },
                "generic-harness/certification.json": {"status": "certified"},
                "generic-harness/tool-certification.json": {
                    "tools": [
                        {"tool": tool, "availability": "certified", "reason": ""}
                        for tool in TOOLS
                    ]
                },
                "generic-harness/action-certification.json": {
                    "actions": [
                        {"action": tool, "status": "passed", "reason": ""}
                        for tool in TOOLS
                    ]
                },
            }
        )
        files.update(door_files)
    return {
        path: (value if isinstance(value, str) else json.dumps(value)).encode()
        for path, value in files.items()
    }


def seed_environment(
    organization,
    workspace,
    *,
    suffix,
    refunds=1,
    lookups=1,
    certified=True,
    production_only=None,
    failed_check=None,
    actors=False,
    trial=False,
    tool_detail=False,
    workers=None,
    refund_worker_budget=False,
):
    """A completed, backfilled Environment of ``refunds`` + ``lookups`` scenarios; ``trial`` picks a
    ``TRIAL_STATES`` variant for its finished trial."""
    from simulate.models import (
        AgentDefinition,
        AgentVersion,
        HostedHarnessJob,
        RunTest,
    )
    from simulate.services.harness_versioning import archives
    from simulate.services.harness_versioning.backfill import backfill_environment
    from simulate.services.hosted_harness import (
        create_hosted_job,
        provision_scenarios,
        register_attempt,
    )
    from simulate.services.hosted_harness_gateway import authoring_content_digest

    documents = _documents(
        refunds, lookups, suffix, refund_worker_budget=refund_worker_budget
    )
    if actors:
        documents = _with_actor(documents)
    environment, _ = create_hosted_job(
        organization,
        _payload(len(documents)),
        idempotency_key=f"e2e-harness-seed-{suffix}",
        workspace=workspace,
    )
    attempt = register_attempt(
        environment.id, endpoint_base_url="https://harness.example.test"
    ).attempt
    provision_scenarios(
        attempt,
        {
            "operation": "provision",
            "name": f"e2e suite {suffix}",
            "modality": "voice",
            "personas": [
                {
                    "scenario_key": document["scenario_key"],
                    "name": document["name"],
                    "role": "customer",
                    "situation": "s",
                    "outcome": "o",
                    "persona": document["persona"],
                }
                for document in documents
            ],
        },
    )
    if actors:
        # The scenario rows carry each case's actors, as indexing the built suite writes them.
        from simulate.models import HostedHarnessScenario
        from simulate.services.harness_scenarios import call_actor_entries

        for document in documents:
            HostedHarnessScenario.no_workspace_objects.filter(
                job=environment, scenario_key=document["scenario_key"]
            ).update(actors=call_actor_entries(document.get("actors")))
    body = archives.pack_archive(
        _authoring(
            documents,
            certified=certified,
            production_only=production_only,
            actors=actors,
            tool_detail=tool_detail,
            workers=workers,
        )
    )
    object_key = archives.pair_key(organization.id, environment.id, body)
    archives.put_archive(object_key, body)
    environment.refresh_from_db()
    payload = dict(environment.payload)
    payload["metadata"] = {
        **(payload.get("metadata") or {}),
        "authoring_object_key": object_key,
        "authoring_revision": authoring_content_digest(body),
    }
    environment.payload = payload
    environment.name = f"e2e-harness-{suffix}"
    environment.state = HostedHarnessJob.State.COMPLETED
    environment.current_stage = "completed"
    environment.save(
        update_fields=["payload", "name", "state", "current_stage", "updated_at"]
    )

    definition = AgentDefinition.objects.create(
        agent_name=f"e2e-harness-agent-{suffix}",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+12345678901",
        inbound=True,
        description="e2e harness seed",
        provider="vapi",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    AgentVersion.no_workspace_objects.create(
        agent_definition=definition,
        organization=organization,
        workspace=workspace,
        description="e2e harness seed",
        commit_message="e2e harness seed",
        status=AgentVersion.StatusChoices.ACTIVE,
    )
    RunTest.objects.filter(id=environment.run_test_id).update(
        agent_definition=definition, agent_version=None
    )
    result = backfill_environment(environment.id)
    environment.refresh_from_db()
    if environment.suite_version_id is None or environment.agent_version_id is None:
        raise RuntimeError(
            f"backfill left no versions: {result.outcome} {result.notes}"
        )
    if certified and environment.assembled_bundle_id is None:
        raise RuntimeError("backfill cached no certified bundle")
    if failed_check:
        _fail_check(environment, failed_check)
    trial_ids = (
        _finished_trial(environment, definition, documents[0], TRIAL_STATES[trial])
        if trial
        else {}
    )
    return {
        **trial_ids,
        "environmentId": str(environment.id),
        "agentVersionId": str(environment.agent_version_id),
        "environmentVersionId": str(environment.environment_version_id),
        "suiteVersionId": str(environment.suite_version_id),
        "scenarioKeys": [document["scenario_key"] for document in documents],
        "refundKeys": [d["scenario_key"] for d in documents[:refunds]],
        "lookupKeys": [d["scenario_key"] for d in documents[refunds:]],
    }


def _finished_trial(environment, definition, document, trial_state):
    """One finished run of the Environment with one completed trial of its first scenario, its World changes as the
    harness seals them (ingestion keeps them under ``harness_trial_state``)."""
    from simulate.models import HostedHarnessScenario
    from simulate.models.test_execution import CallExecution, TestExecution
    from simulate.services.hosted_harness_ingestion import TRIAL_STATE_KEY

    # A call belongs to the scenario its trial ran, as a real run's calls do.
    provisioned = HostedHarnessScenario.no_workspace_objects.get(
        job=environment, scenario_key=document["scenario_key"]
    )

    execution = TestExecution.objects.create(
        run_test_id=environment.run_test_id,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        agent_definition=definition,
    )
    call = CallExecution.objects.create(
        test_execution=execution,
        scenario_id=provisioned.scenario_id,
        status=CallExecution.CallStatus.COMPLETED,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
        call_metadata={
            "harness_scenario_key": document["scenario_key"],
            "harness_trial_index": 0,
            TRIAL_STATE_KEY: trial_state,
        },
    )
    return {
        "runTestId": str(environment.run_test_id),
        "testExecutionId": str(execution.id),
        "callExecutionId": str(call.id),
    }


def _fail_check(environment, action):
    """The pairing's bundle, keyed as a validation keys it, failed by its check."""
    from simulate.models import HarnessAgentVersionRuntime, HarnessAssembledBundle
    from simulate.services.harness_versioning import assembly
    from simulate.services.harness_versioning.backfill import effective_runtime

    runtime = effective_runtime(
        environment,
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=environment.agent_version_id
        ).first(),
    )
    key = assembly.bundle_key(
        runtime, environment.environment_version, environment.suite_version
    )
    HarnessAssembledBundle._base_manager.update_or_create(
        job_id=environment.id,
        assembly_key=key,
        defaults={
            "organization_id": environment.organization_id,
            "agent_version_id": environment.agent_version_id,
            "environment_version_id": environment.environment_version_id,
            "suite_version_id": environment.suite_version_id,
            "state": HarnessAssembledBundle.State.FAILED,
            "error": {
                "code": "authoring_runtime_validation_failed",
                "message": f"Traceback (most recent call last): {action} probe",
                "source": "validation",
                "failed_actions": [
                    {"action": action, "status": "failed", "reason": "no world"}
                ],
            },
        },
    )


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
    import django

    django.setup()
    from accounts.models import Organization
    from accounts.models.workspace import Workspace

    options = json.loads(os.environ["SEED_OPTIONS"])
    seeded = seed_environment(
        Organization.objects.get(id=os.environ["SEED_ORG_ID"]),
        Workspace.objects.get(id=os.environ["SEED_WORKSPACE_ID"]),
        suffix=os.environ["SEED_SUFFIX"],
        **options,
    )
    sys.stdout.write(json.dumps(seeded) + "\n")


if __name__ == "__main__":
    main()
