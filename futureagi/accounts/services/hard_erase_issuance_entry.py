"""Explicit batch-issuance entry planning, never an ORM hook.

Resolve existing people and reserve new UUIDs before any row write. Every nested
issuance reuses the complete sorted key set. A concurrent email insertion refuses
before entering the writer, rather than extending a live transaction's lock set.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from uuid import uuid4

from accounts.services.hard_erase_lifecycle import identity_keys, lifecycle_lock

_PLANNED = ContextVar("hard_erase_planned_people", default=None)


def planned_person(user):
    """Use the preallocated ID only for a new person in an explicit entry scope."""
    from accounts.services.hard_erase import _refuse

    plan = _PLANNED.get()
    if plan is not None:
        email = user.email.strip().lower()
        if email not in plan:
            _refuse("unplanned person issuance; retry the whole transaction")
        user.pk = plan[email]
    return user


def _email_people(emails, using):
    from django.db import models
    from django.db.models.functions import Lower

    from accounts.models.user import User

    rows = list(
        models.QuerySet(model=User, using=using)
        .annotate(issuance_email=Lower("email"))
        .filter(issuance_email__in=emails)
        .values_list("issuance_email", "pk")
    )
    if len(dict(rows)) != len(rows):
        from accounts.services.hard_erase import _refuse

        _refuse("ambiguous case-folded issuance identity")
    return dict(rows)


def _target_exists(value, using):
    from django.db import models

    from accounts.models.user import User

    return models.QuerySet(model=User, using=using).filter(pk=value).exists()


@contextmanager
def issuance_entry(
    *,
    organizations=(),
    people=(),
    targets=(),
    emails=(),
    new_organizations=(),
    using="default",
):
    """Plan a batch at its enclosing transaction entry, before row operations."""
    from accounts.models.organization import Organization
    from accounts.models.user import User
    from accounts.services.hard_erase import _refuse
    from accounts.services.identity_issuance import _exists

    emails = frozenset(email.strip().lower() for email in emails if email)
    before = _email_people(emails, using) if emails else {}
    existing = {email.lower(): pk for email, pk in before.items()}
    parent = _PLANNED.get()
    plan = {
        email: existing.get(email) or (parent or {}).get(email) or uuid4()
        for email in emails
    }
    organizations, people, targets = tuple(organizations), tuple(people), tuple(targets)
    keys = identity_keys(
        organizations=[*organizations, *new_organizations],
        people=[*people, *targets, *plan.values()],
    )
    with lifecycle_lock(using, keys=keys):
        if emails and _email_people(emails, using) != before:
            _refuse("issuance identities changed; retry the whole transaction")
        for person in [*people, *existing.values()]:
            _exists(User, person, using)
        for organization in organizations:
            _exists(Organization, organization, using)
        # Only target absence is a response contract. Actor/org failures above
        # remain refusals. All target keys are held before this read or response.
        missing = frozenset(
            target for target in targets if not _target_exists(target, using)
        )
        token = _PLANNED.set({**(parent or {}), **plan})
        try:
            yield missing
        finally:
            _PLANNED.reset(token)


def request_issuance_entry(method):
    """Opt-in entry wrapper for validated organization/workspace invite batches."""

    @wraps(method)
    def locked(self, request, *args, **kwargs):
        from accounts.utils import resolve_org

        organization = resolve_org(request)
        if organization is None:
            return method(self, request, *args, **kwargs)
        data = request.validated_data
        emails = list(data.get("emails", []))
        emails.extend(row.get("email", "") for row in data.get("users", []))
        emails.extend(row.get("email", "") for row in data.get("members", []))
        targets = [data["user_id"]] if data.get("user_id") else []
        with issuance_entry(
            organizations=[organization.pk],
            people=[request.user.pk],
            targets=targets,
            emails=emails,
        ):
            # Target absence belongs to the view's original validation order,
            # not an early response that skips its permission/workspace checks.
            return method(self, request, *args, **kwargs)

    return locked


def tool_issuance_entry(method):
    """Explicit opt-in for workspace creation, member addition and invite tools."""

    @wraps(method)
    def locked(self, params, context):
        target = getattr(params, "user_id", None)
        with issuance_entry(
            organizations=[context.organization.pk],
            people=[context.user.pk],
            targets=[target] if target is not None else [],
            emails=getattr(params, "emails", ()),
        ):
            # Keep the full target key reserved even when absent. The tool must
            # check permission, then workspace, then user under this same lock.
            return method(self, params, context)

    return locked
