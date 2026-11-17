"""The dev shortcut (SPEC §14.1): approve every `in_review` submission as maintainer B, recorded as a
seed. Runs only in a local environment (ENV_TYPE local or test) with DEBUG (or the test
settings) AND OMEGA_HUB_ALLOW_DEV_APPROVE=1.

It still goes through review.decide: approver != submitter, every flag acknowledged by listing it,
the current checklist ticked, and each manifest's draft disclosure confirmed unchanged. Every
approval writes `review.approved` with `data.via = "dev-seed"`, never as a human review. It approves
only for the maintainer organisation omega_hub_seed_dev made, and never confirms a placeholder draft.
"""

import json

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from accounts.models.organization import Organization
from omega_hub import conf, rules
from omega_hub.errors import HubError
from omega_hub.models import Submission
from omega_hub.permissions import is_maintainer
from omega_hub.services import review
from omega_hub.services.checks import latest_job
from omega_hub.services.publish import flag_ids

# Every part of the placeholder the import's old `--fill-missing` wrote began with these words.
PLACEHOLDER_PREFIX = "not written yet"


def is_placeholder(draft) -> bool:
    if not isinstance(draft, dict):
        return False
    return any(isinstance(value, str) and value.strip().lower().startswith(PLACEHOLDER_PREFIX) for value in draft.values())


def _is_dev_maintainer_org(org_id) -> bool:
    if not org_id:
        return False
    try:
        org = Organization.objects.filter(pk=org_id).first()
    except (ValueError, ValidationError):
        return False
    return org is not None and org.name == conf.DEV_MAINTAINER_ORG_NAME


class Command(BaseCommand):
    help = "Approve in_review submissions as a dev seed (local, DEBUG and OMEGA_HUB_ALLOW_DEV_APPROVE=1 only)."

    def add_arguments(self, parser):
        parser.add_argument("--as", dest="as_email", required=True)
        parser.add_argument("--all-in-review", action="store_true")
        parser.add_argument("--ids", default="", help="Comma-separated submission ids instead of --all-in-review.")

    def handle(self, *args, as_email, all_in_review, ids, **options):
        if not conf.is_local_dev() or not conf.allow_dev_approve():
            raise CommandError("omega_hub_dev_approve needs a local environment (ENV_TYPE local or test), DEBUG and OMEGA_HUB_ALLOW_DEV_APPROVE=1")
        if not _is_dev_maintainer_org(conf.maintainer_org_id()):
            raise CommandError("omega_hub_dev_approve approves only for the maintainer organisation omega_hub_seed_dev made; "
                               "OMEGA_HUB_MAINTAINER_ORG_ID names another")
        user = get_user_model().objects.filter(email=as_email).first()
        if user is None or not is_maintainer(user):
            raise CommandError(f"{as_email} is not a Hub maintainer")
        rows = Submission.objects.filter(status=Submission.Status.IN_REVIEW).order_by("created_at")
        if ids:
            rows = rows.filter(pk__in=[item for item in ids.split(",") if item])
        elif not all_in_review:
            raise CommandError("pass --all-in-review or --ids")
        approved, skipped = [], []
        for submission in rows:
            job = latest_job(submission)
            draft = (submission.manifest or {}).get("disclosure")
            if is_placeholder(draft):
                # A confirmed disclosure is shown as "Confirmed in review"; a seed never confirms
                # text that says it was not written (imports made before the seed disclosures).
                skipped.append({"submission": str(submission.pk), "name": submission.name,
                                "code": "OMEGA_HUB_DISCLOSURE_PLACEHOLDER",
                                "message": "the draft disclosure is placeholder text; a maintainer must write it"})
                continue
            payload = {
                "decision": "approve", "rulesVersion": rules.current_version(),
                "checklist": {key: True for key in rules.checklist_keys()},
                "acknowledgedFlags": flag_ids(job.report if job else None), "disclosure": draft,
            }
            try:
                review.decide(user, submission, payload, via="dev-seed")
                approved.append(f"{submission.publisher.handle}/{submission.name}@{submission.version}")
            except HubError as error:
                skipped.append({"submission": str(submission.pk), "name": submission.name, "code": error.code,
                                "message": error.message})
        self.stdout.write(json.dumps({"approved": approved, "skipped": skipped}, indent=2))
