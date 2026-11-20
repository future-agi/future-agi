"""Seed the review of an Environment draft, and stand in for its validation job.

``e2e/lib/simulate-seed.ts`` copies this file into the stack's backend container and
runs it there, on an Environment ``harness_environment_seed.py`` built. The operation is
chosen by ``SEED_OPERATION``:

``chat_rule``
    A builder-chat turn answering the person's message adds a rule to the contract and
    logs it in the amendment log, as the harness does: the person's message is enqueued
    and finished, then the edited tree goes through ``record_draft`` with that message's
    id, which is what the review cites.
``chat_case``
    A builder-chat turn answering the person's message adds a scenario to the suite draft.
``prove_candidates``
    The validation job's result for the live candidate set: its proofs recorded as the
    gateway records a sandbox's proofs file, then ``certify_candidates``.
``certify_candidates``
    The pair validation job's success for the Environment's live candidate set: a ready
    bundle for that pairing with a certified sealed archive. The stack has no sandbox to
    run the job.

Prints one JSON line.
"""

import json
import os
import sys


def chat_rule(environment, user, *, suffix):
    from simulate.models import (
        HostedHarnessConversation,
        HostedHarnessConversationMessage,
    )
    from simulate.services.harness_versioning import archives, drafts
    from simulate.services.hosted_harness_conversation import enqueue_message

    conversation, message, _ = enqueue_message(
        environment,
        content="Refunds above the order total are never issued",
        client_request_id=f"e2e-review-{suffix}",
    )
    # The turn is over: no runtime picks the message up again.
    HostedHarnessConversationMessage.no_workspace_objects.filter(id=message.id).update(
        state=HostedHarnessConversationMessage.State.COMPLETED
    )
    HostedHarnessConversation.no_workspace_objects.filter(id=conversation.id).update(
        state=HostedHarnessConversation.State.COLD
    )
    rule = f"Never refund more than the order total ({suffix})"
    amendment = f"rule added \u2014 {rule}: the policy caps refunds"
    sides = drafts.edit_sides(environment)
    tree = {**sides["suite"], **sides["environment"]}
    contract = json.loads(tree["contract.json"])
    contract["hard_constraints"] = [*(contract.get("hard_constraints") or []), rule]
    contract["amendments"] = [*(contract.get("amendments") or []), amendment]
    tree["contract.json"] = json.dumps(contract).encode()
    drafts.record_draft(
        environment,
        archives.pack_archive(tree),
        origin="chat",
        user=user,
        pieces={"environment"},
        message_id=str(message.id),
    )
    return {"messageId": str(message.id), "rule": rule, "amendment": amendment}


def chat_case(environment, user, *, suffix):
    """A builder-chat turn adds a scenario to the suite draft, answering the person's message."""
    from simulate.models import (
        HostedHarnessConversation,
        HostedHarnessConversationMessage,
    )
    from simulate.services.harness_versioning import archives, drafts
    from simulate.services.hosted_harness_conversation import enqueue_message

    conversation, message, _ = enqueue_message(
        environment,
        content="Add a case where the caller asks for part of an order back",
        client_request_id=f"e2e-case-{suffix}",
    )
    HostedHarnessConversationMessage.no_workspace_objects.filter(id=message.id).update(
        state=HostedHarnessConversationMessage.State.COMPLETED
    )
    HostedHarnessConversation.no_workspace_objects.filter(id=conversation.id).update(
        state=HostedHarnessConversation.State.COLD
    )
    key = f"partial-refund-{suffix}"
    name = key.replace("-", "_")
    document = {
        "name": name,
        "scenario_key": key,
        "persona": {"name": f"caller of {key}"},
        "instruction": "ask for part of the order back",
        "solution": [{"tool": tool, "arguments": {}} for tool in ("lookup_order", "issue_refund")],
        "sub_goals": ["looked_up"],
        "max_turns": 6,
    }
    sides = drafts.edit_sides(environment)
    tree = {**sides["suite"], **sides["environment"]}
    tree["scenarios.json"] = json.dumps([*json.loads(tree["scenarios.json"]), document]).encode()
    tree[f"scenarios/{name}/scenario.json"] = json.dumps(document).encode()
    tree[f"scenarios/{name}/ready.py"] = b"def ready(world):\n    return True\n"
    drafts.record_draft(
        environment,
        archives.pack_archive(tree),
        origin="chat",
        user=user,
        pieces={"suite"},
        message_id=str(message.id),
    )
    return {"messageId": str(message.id), "scenarioKey": key, "name": name}


def prove_candidates(environment, *, suffix):
    """The validation job's result for the live candidate set: every case proved, recorded as the gateway records a
    sandbox's proofs file (keyed by scenario folder), then the pairing certified."""
    from simulate.models import HarnessEnvironmentVersion, HarnessSuiteVersion
    from simulate.services.harness_versioning.candidate_sets import live_set
    from simulate.services.harness_versioning.versions import record_proofs, suite_cases

    entry = live_set(environment)
    if entry is None:
        raise RuntimeError("the environment has no candidate set to prove")
    suite = HarnessSuiteVersion._base_manager.get(id=entry["suite_version_id"])
    proved = {"format": 2, "holds": True, "proved": True, "assumed": [], "blocked_reason": None}
    record_proofs(
        suite,
        HarnessEnvironmentVersion._base_manager.get(id=entry["environment_version_id"]),
        {key.replace("-", "_"): proved for key, _, _ in suite_cases(suite)},
        None,
    )
    return certify_candidates(environment, suffix=suffix)


def certify_candidates(environment, *, suffix):
    from simulate.models import HarnessAssembledBundle
    from simulate.services.harness_versioning import archives
    from simulate.services.harness_versioning.assembly import (
        CERTIFICATE,
        SEALED_MANIFEST,
    )
    from simulate.services.harness_versioning.candidate_sets import live_set

    entry = live_set(environment)
    if entry is None:
        raise RuntimeError("the environment has no candidate set to certify")
    key = f"bundles/e2e-review-{suffix}.tar.gz"
    archives.put_archive(
        key,
        archives.pack_archive(
            {
                SEALED_MANIFEST: json.dumps({"files": []}).encode(),
                CERTIFICATE: json.dumps({"status": "passed"}).encode(),
            }
        ),
    )
    bundle, _ = HarnessAssembledBundle._base_manager.update_or_create(
        job_id=environment.id,
        assembly_key=entry["assembly_key"],
        defaults={
            "organization_id": environment.organization_id,
            "agent_version_id": entry["agent_version_id"],
            "environment_version_id": entry["environment_version_id"],
            "suite_version_id": entry["suite_version_id"],
            "state": HarnessAssembledBundle.State.READY,
            "object_key": key,
            "error": None,
        },
    )
    return {"bundleId": str(bundle.id)}


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
    import django

    django.setup()
    from django.contrib.auth import get_user_model

    from simulate.models import HostedHarnessJob

    environment = HostedHarnessJob.no_workspace_objects.get(
        id=os.environ["SEED_ENVIRONMENT_ID"]
    )
    suffix = os.environ["SEED_SUFFIX"]
    operation = os.environ["SEED_OPERATION"]
    if operation == "chat_rule":
        user = get_user_model().objects.get(email=os.environ["SEED_USER_EMAIL"])
        seeded = chat_rule(environment, user, suffix=suffix)
    elif operation == "chat_case":
        user = get_user_model().objects.get(email=os.environ["SEED_USER_EMAIL"])
        seeded = chat_case(environment, user, suffix=suffix)
    elif operation == "prove_candidates":
        seeded = prove_candidates(environment, suffix=suffix)
    elif operation == "certify_candidates":
        seeded = certify_candidates(environment, suffix=suffix)
    else:
        raise ValueError(f"unknown SEED_OPERATION {operation!r}")
    sys.stdout.write(json.dumps(seeded) + "\n")


if __name__ == "__main__":
    main()
