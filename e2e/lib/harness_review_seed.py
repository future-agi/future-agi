"""Seed the review of an Environment draft, and stand in for its validation job.

``e2e/lib/simulate-seed.ts`` copies this file into the stack's backend container and
runs it there, on an Environment ``harness_environment_seed.py`` built. Two operations,
chosen by ``SEED_OPERATION``:

``chat_rule``
    A builder-chat turn answering the person's message adds a rule to the contract and
    logs it in the amendment log, as the harness does: the person's message is enqueued
    and finished, then the edited tree goes through ``record_draft`` with that message's
    id, which is what the review cites.
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
    elif operation == "certify_candidates":
        seeded = certify_candidates(environment, suffix=suffix)
    else:
        raise ValueError(f"unknown SEED_OPERATION {operation!r}")
    sys.stdout.write(json.dumps(seeded) + "\n")


if __name__ == "__main__":
    main()
