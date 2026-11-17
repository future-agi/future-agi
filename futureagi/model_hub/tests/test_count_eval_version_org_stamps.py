"""A read-only count of evaluation versions stamped with an organization.

``count_eval_version_org_stamps`` lists, for an operator, the versions of a
system evaluation that carry an organization and the user evaluations that
carry none: how many, how many of those are not deleted, then one line per
row with its ids and whether it is deleted. Deleted rows are read too and
shown apart. It prints no name, text, configuration, model, user or
organization, runs on every deployment and writes nothing.

Every value here is synthetic: a sentinel string the test makes itself is put
in every text and JSON field of the rows it seeds (aside from the owner, which
the count selects on, and the evaluation type, which is shorter), and in the
names and e-mail of the organizations and the user those rows point to.
"""

import re
import uuid
from io import StringIO
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models.organization import Organization
from accounts.models.user import User
from model_hub.apps import STARTUP_SAFE_MANAGEMENT_COMMANDS, guarded_management_command
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion
from tfc.constants.roles import OrganizationRoles

COMMAND = "count_eval_version_org_stamps"
VERSIONS_HEAD = "system_template_versions_with_org"
TEMPLATES_HEAD = "user_templates_without_org"
SYSTEM = OwnerChoices.SYSTEM.value
USER = OwnerChoices.USER.value
UUID_TEXT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
PRINTED_VALUES = {
    VERSIONS_HEAD: re.compile(r"\d+"),
    TEMPLATES_HEAD: re.compile(r"\d+"),
    "not_deleted": re.compile(r"\d+"),
    "version_id": UUID_TEXT,
    "template_id": UUID_TEXT,
    "deleted": re.compile(r"true|false"),
}


@pytest.fixture
def sentinel():
    """Text no real row holds. It is a valid user evaluation name, fits the
    shortest column it is put in (20 characters) and starts with letters that
    are not hex digits, so no printed id can contain it."""
    return f"zqsent{uuid.uuid4().hex[:14]}"


def _template(sentinel, name, owner, organization, deleted=False):
    template = EvalTemplate.no_workspace_objects.create(
        name=f"{sentinel}-{name}",
        description=sentinel,
        organization=organization,
        workspace=None,
        owner=owner,
        eval_tags=[sentinel],
        config={sentinel: sentinel, "output": sentinel},
        criteria=sentinel,
        choices=[sentinel],
        model=sentinel,
        template_type=sentinel,
        choice_scores={sentinel: 1.0},
    )
    # The choice fields too, stored past the checks a save runs.
    EvalTemplate._base_manager.filter(pk=template.pk).update(
        output_type_normalized=sentinel,
        aggregation_function=sentinel,
        composite_child_axis=sentinel,
        deleted=deleted,
    )
    return template


def _version(sentinel, template, number, organization, created_by, deleted=False):
    version = EvalTemplateVersion.all_objects.create(
        eval_template=template,
        version_number=number,
        prompt_messages=[{"role": "system", "content": sentinel}],
        config_snapshot={sentinel: sentinel, "criteria": sentinel},
        criteria=sentinel,
        model=sentinel,
        created_by=created_by,
        organization=organization,
        workspace=None,
        output_type_normalized=sentinel,
        choice_scores={sentinel: 1.0},
        eval_tags=[sentinel],
    )
    if deleted:
        EvalTemplateVersion._base_manager.filter(pk=version.pk).update(deleted=True)
    return version


def _stored(model, row):
    """The row as stored, read past every manager filter."""
    return model._base_manager.values("organization_id", "deleted").get(pk=row.pk)


def _run():
    out = StringIO()
    call_command(COMMAND, stdout=out)
    return out.getvalue()


def _flag(deleted):
    return "true" if deleted else "false"


@pytest.fixture
def people(db, sentinel):
    """Two organizations and a user, every name and e-mail the sentinel."""
    first = Organization.objects.create(name=sentinel, display_name=sentinel)
    second = Organization.objects.create(
        name=f"{sentinel}-b", display_name=f"{sentinel}-b"
    )
    author = User.objects.create_user(
        email=f"{sentinel}@example.com",
        password=uuid.uuid4().hex,
        name=sentinel,
        organization=first,
        organization_role=OrganizationRoles.OWNER,
    )
    return SimpleNamespace(first=first, second=second, author=author)


@pytest.fixture
def seeded(people, sentinel):
    """Rows each check lists (deleted ones among them) and rows it must not.

    Listed by the version check: a live and a deleted version of a system
    evaluation, each with an organization, and a live version with an
    organization of a system evaluation that is itself deleted. Listed by the
    template check: a live and a deleted user evaluation with no organization.
    Listed by neither: a system version with no organization, a user
    evaluation with an organization and its version with one, and a system
    evaluation with no organization.
    """
    first, second, author = people.first, people.second, people.author

    system = _template(sentinel, "system", SYSTEM, None)
    stamped = _version(sentinel, system, 1, first, author)
    stamped_deleted = _version(sentinel, system, 2, second, author, deleted=True)
    _version(sentinel, system, 3, None, author)

    system_deleted = _template(sentinel, "system-deleted", SYSTEM, None, deleted=True)
    stamped_under_deleted = _version(sentinel, system_deleted, 1, first, None)

    unowned = _template(sentinel, "unowned", USER, None)
    unowned_deleted = _template(sentinel, "unowned-deleted", USER, None, deleted=True)

    owned = _template(sentinel, "owned", USER, first)
    _version(sentinel, owned, 1, first, author)

    assert _stored(EvalTemplateVersion, stamped)["organization_id"] == first.id
    assert _stored(EvalTemplate, system)["organization_id"] is None
    assert _stored(EvalTemplate, unowned)["organization_id"] is None
    assert _stored(EvalTemplate, unowned_deleted) == {
        "organization_id": None,
        "deleted": True,
    }

    versions = sorted(
        (str(version.pk), str(version.eval_template_id), _flag(deleted))
        for version, deleted in (
            (stamped, False),
            (stamped_deleted, True),
            (stamped_under_deleted, False),
        )
    )
    templates = sorted(
        (str(template.pk), _flag(deleted))
        for template, deleted in ((unowned, False), (unowned_deleted, True))
    )
    return SimpleNamespace(versions=versions, templates=templates, **vars(people))


@pytest.mark.django_db
def test_the_count_lists_each_stamped_row_and_counts_deleted_ones_apart(seeded):
    output = _run()

    assert output.splitlines() == [
        f"{VERSIONS_HEAD}=3 not_deleted=2",
        *(
            f"version_id={version} template_id={template} deleted={deleted}"
            for version, template, deleted in seeded.versions
        ),
        f"{TEMPLATES_HEAD}=2 not_deleted=1",
        *(
            f"template_id={template} deleted={deleted}"
            for template, deleted in seeded.templates
        ),
    ]


@pytest.mark.django_db
def test_rows_whose_organization_matches_their_owner_are_not_listed(people, sentinel):
    first = people.first
    system = _template(sentinel, "system", SYSTEM, None)
    unstamped = _version(sentinel, system, 1, None, people.author)
    unstamped_deleted = _version(sentinel, system, 2, None, None, deleted=True)
    owned = _template(sentinel, "owned", USER, first)
    owned_deleted = _template(sentinel, "owned-deleted", USER, first, deleted=True)
    owned_version = _version(sentinel, owned, 1, first, people.author)

    assert _stored(EvalTemplateVersion, unstamped)["organization_id"] is None
    assert _stored(EvalTemplateVersion, unstamped_deleted) == {
        "organization_id": None,
        "deleted": True,
    }
    assert _stored(EvalTemplate, owned)["organization_id"] == first.id
    assert _stored(EvalTemplate, owned_deleted)["organization_id"] == first.id
    assert _stored(EvalTemplateVersion, owned_version)["organization_id"] == first.id

    assert _run().splitlines() == [
        f"{VERSIONS_HEAD}=0 not_deleted=0",
        f"{TEMPLATES_HEAD}=0 not_deleted=0",
    ]


@pytest.mark.django_db
def test_the_count_prints_only_ids_deleted_flags_and_integers(seeded, sentinel):
    output = _run()

    assert sentinel not in output.lower()
    for hidden in (
        seeded.first.id,
        seeded.second.id,
        seeded.author.id,
        seeded.author.email,
    ):
        assert str(hidden).lower() not in output.lower()
    lines = output.splitlines()
    assert len(lines) == 2 + len(seeded.versions) + len(seeded.templates)
    for line in lines:
        for word in line.split(" "):
            key, _, value = word.partition("=")
            assert key in PRINTED_VALUES, line
            assert PRINTED_VALUES[key].fullmatch(value), line


@pytest.mark.django_db
def test_the_count_writes_nothing(seeded):
    def snapshot():
        return {
            model: list(model._base_manager.order_by("pk").values())
            for model in (EvalTemplate, EvalTemplateVersion)
        }

    before = snapshot()
    with CaptureQueriesContext(connection) as queries:
        _run()
    assert queries.captured_queries
    for query in queries.captured_queries:
        assert query["sql"].lstrip().upper().startswith("SELECT"), query["sql"]
    assert snapshot() == before


@pytest.mark.parametrize(
    "argv",
    [
        ["manage.py", COMMAND],
        ["python", "-m", "django", COMMAND],
        ["django-admin", COMMAND],
    ],
)
def test_the_count_runs_on_every_deployment(argv):
    assert COMMAND in STARTUP_SAFE_MANAGEMENT_COMMANDS
    assert guarded_management_command(argv) is None
