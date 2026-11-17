"""Count the stored phone agents configured with the partner phone target.

    manage.py count_partner_phone_target_agents

It reads ALK_CAB_GUEST_POC_TARGET_PHONE_NUMBER from this process's own
environment and compares it with every stored agent the way the hosted harness
gateway does: the agent's connector, stripped and lower-cased, is ``phone``,
and its config's ``phone_number``, stripped, equals the stripped setting. It
looks where an agent is stored:

- agent versions: the agent settings each version keeps
  (``HarnessAgentVersionRuntime.agent_payload``);
- hosted harness jobs: the agent block of each job's payload
  (``HostedHarnessJob.payload["agent"]``).

Rows are read through the plain manager, so deleted rows are counted too and
reported apart. The comparison happens in this process: the setting never goes
into a query. It prints five lines of fixed words and integers, and nothing
else:

    poc_target=set
    agent_versions_matching=<n> not_deleted=<n>
    hosted_jobs_matching=<n> not_deleted=<n>
    organizations_matching=<n>
    rows_skipped=<n>

``organizations_matching`` counts the organizations of the matching rows of
both tables, deleted rows included. ``rows_skipped`` counts the rows whose
payload, agent block or config is stored as something other than a JSON
object, where the gateway reads one; they are not compared. An absent or empty
value reads as empty, as the gateway reads it, so such a row is compared and
does not match.

With the setting unset or blank it prints ``poc_target=unset`` and every count
as 0, reads no row and exits 0. On an unexpected error it prints only
``count_failed error_class=<name>``, the exception's class name and never its
text, and exits 1: a stored payload is customer JSON.

Reads only: every query is a SELECT of the payload, organization and deleted
columns; nothing is saved, updated or locked. Admitted on every deployment
(``STARTUP_SAFE_MANAGEMENT_COMMANDS`` in ``model_hub/apps.py``).
"""

import os

from django.core.management.base import BaseCommand

from simulate.models import HarnessAgentVersionRuntime, HostedHarnessJob

TARGET_SETTING = "ALK_CAB_GUEST_POC_TARGET_PHONE_NUMBER"
FAILED = "count_failed"
CHUNK_SIZE = 500


class _Malformed(Exception):
    """A stored value that is not a JSON object where the gateway reads one."""


def _mapping(value):
    """``value`` read as the gateway reads it: empty when unset, else an object."""
    if not value:
        return {}
    if not isinstance(value, dict):
        raise _Malformed
    return value


def _version_agent(row):
    return _mapping(row.agent_payload)


def _job_agent(row):
    return _mapping(_mapping(row.payload).get("agent"))


# Each table: the printed name, the model, the payload column and how its agent
# block is read from a row.
TABLES = (
    ("agent_versions", HarnessAgentVersionRuntime, "agent_payload", _version_agent),
    ("hosted_jobs", HostedHarnessJob, "payload", _job_agent),
)


def _matches(agent, target):
    """A phone connector whose configured number is the target."""
    config = _mapping(agent.get("config"))
    return (
        str(agent.get("connector") or "").strip().lower() == "phone"
        and str(config.get("phone_number") or "").strip() == target
    )


def _count(target):
    """Per table (name, matching, not deleted); the organizations; rows skipped."""
    counts = []
    organizations = set()
    skipped = 0
    for name, model, column, agent_of in TABLES:
        matching = not_deleted = 0
        last_pk = None
        while True:
            # One bounded page at a time, by primary key: the default database
            # reads a whole result before the first row, so an unpaged scan
            # would hold every payload in memory at once.
            page = model._base_manager.only(column, "organization", "deleted")
            if last_pk is not None:
                page = page.filter(pk__gt=last_pk)
            seen = 0
            for row in page.order_by("pk")[:CHUNK_SIZE].iterator(chunk_size=CHUNK_SIZE):
                seen += 1
                last_pk = row.pk
                try:
                    matched = _matches(agent_of(row), target)
                except _Malformed:
                    skipped += 1
                    continue
                if not matched:
                    continue
                matching += 1
                if not row.deleted:
                    not_deleted += 1
                organizations.add(row.organization_id)
            if seen < CHUNK_SIZE:
                break
        counts.append((name, matching, not_deleted))
    return counts, len(organizations), skipped


class Command(BaseCommand):
    help = (
        "Count the agent versions and hosted harness jobs of phone agents "
        "configured with the partner phone target: fixed words and integers "
        "only, deleted rows apart. Reads only."
    )
    # The output is the lines below and nothing else: no system-check report.
    requires_system_checks = []

    def handle(self, *args, **options):
        target = str(os.environ.get(TARGET_SETTING) or "").strip()
        if not target:
            self._report("unset", [(name, 0, 0) for name, *_ in TABLES], 0, 0)
            return
        try:
            counts, organizations, skipped = _count(target)
        except Exception as exc:  # noqa: BLE001 - its text can carry a stored value
            self.stdout.write(f"{FAILED} error_class={type(exc).__name__}")
            raise SystemExit(1) from None
        self._report("set", counts, organizations, skipped)

    def _report(self, target_state, counts, organizations, skipped):
        lines = [f"poc_target={target_state}"]
        lines += [
            f"{name}_matching={matching} not_deleted={not_deleted}"
            for name, matching, not_deleted in counts
        ]
        lines.append(f"organizations_matching={organizations}")
        lines.append(f"rows_skipped={skipped}")
        self.stdout.write("\n".join(lines))
