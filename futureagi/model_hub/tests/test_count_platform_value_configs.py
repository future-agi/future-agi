"""Counting the stored configurations that hold a reference value.

``manage.py count_platform_value_configs`` reads every provider key,
custom model and secret row (deleted ones too, reported apart), decrypts each
in memory, and prints integers only: per table the rows it scanned beside the
table's count, the rows that hold a reference value (and those not deleted),
the rows it could not decrypt, the rows that set one of the reserved
identity fields at any depth, and the rows no model may use as its Google
credential (for any provider, another kind of Google credential than a
service-account key; for Vertex AI, anything other than a service-account key
that signs in at Google's own address); overall, the organizations with a row
that holds a reference value.
With ``--names`` it also prints a named value only when the name is a plain
upper-case identifier (anything else is only counted) and whether that name
is set where the command runs, and for the other form only which of four
known issuers it names; never a value, never what follows the issuer, never
any text it could not print. It writes nothing and runs in any process.

The rows are written here straight to the table, as a row saved before the
save-time check would be. The tests use values they set themselves.
"""

import io
import json
import secrets
import uuid

import pytest
from cryptography.fernet import Fernet
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models import Organization
from model_hub.apps import guarded_management_command
from model_hub.models.api_key import ApiKey, SecretModel, SecretType
from model_hub.models.custom_models import CustomAIModel

COMMAND = "count_platform_value_configs"
AZURE_BASE = "https://fi-test.openai.azure.example.test"
AUDIENCE = "https://fi-test-audience.example.test/never-printed"
GARBAGE = "fi-test pasted\tkey-not-real"
OTHER_ISSUER = "fi-test-issuer"


def _run(*args):
    out = io.StringIO()
    call_command(COMMAND, *args, stdout=out)
    return out.getvalue()


def _provider_key(organization, provider, *, key=None, config_json=None):
    return ApiKey.no_workspace_objects.create(
        organization=organization,
        provider=provider,
        key=key,
        config_json=config_json,
        workspace=None,
    )


def _custom_model(organization, name, key_config, provider="openai"):
    return CustomAIModel.no_workspace_objects.create(
        user_model_id=name,
        provider=provider,
        input_token_cost=0.0,
        output_token_cost=0.0,
        organization=organization,
        workspace=None,
        key_config=key_config,
    )


def _secret(organization, name, key):
    return SecretModel.no_workspace_objects.create(
        name=name,
        secret_type=SecretType.API_KEY,
        key=key,
        organization=organization,
        workspace=None,
    )


class Stored:
    """What the rows below hold, for the assertions."""

    def __init__(self, canary_name, canary_value, unset_name):
        self.canary_name = canary_name
        self.canary_value = canary_value
        self.unset_name = unset_name


@pytest.fixture
def stored(monkeypatch, organization):
    canary_name = f"FI_TEST_CANARY_{uuid.uuid4().hex[:12].upper()}"
    canary_value = f"canary-{secrets.token_hex(16)}"
    unset_name = f"FI_TEST_UNSET_{uuid.uuid4().hex[:12].upper()}"
    monkeypatch.setenv(canary_name, canary_value)
    monkeypatch.delenv(unset_name, raising=False)
    first, second = organization, Organization.objects.create(name="Second Org")

    # Provider keys: a set name, an unset name in a configuration (padded,
    # upper-case form), text that is not a plain name, the set name again on
    # a deleted row, a row this deployment cannot decrypt, and a valid key.
    _provider_key(first, "openai", key=f"os.environ/{canary_name}")
    _provider_key(
        second,
        "azure",
        config_json={
            "api_key": f"  OS.ENVIRON/{unset_name} ",
            "api_base": AZURE_BASE,
            "api_version": "2024-06-01",
        },
    )
    _provider_key(first, "anthropic", key=f"os.environ/{GARBAGE}")
    deleted = _provider_key(first, "gemini", key=f"os.environ/{canary_name}")
    ApiKey._base_manager.filter(pk=deleted.pk).update(deleted=True)
    unreadable = _provider_key(first, "groq", key="sk-fi-test-replaced-not-real")
    token = Fernet(Fernet.generate_key()).encrypt(b'{"v": "x"}').decode()
    ApiKey._base_manager.filter(pk=unreadable.pk).update(key="enc::" + token)
    _provider_key(first, "mistral", key="sk-fi-test-valid-not-real")

    # Custom models: the second form with a known issuer and an audience, and
    # with an issuer that is not one of the four.
    _custom_model(second, "fi-test-model-a", {"api_key": f"oidc/google/{AUDIENCE}"})
    _custom_model(
        first, "fi-test-model-b", {"api_key": f"Oidc/{OTHER_ISSUER}/{AUDIENCE}"}
    )

    # Secrets: the set name once more, and a plain secret.
    _secret(first, "fi-test-secret-a", f"os.environ/{canary_name}")
    _secret(second, "fi-test-secret-b", "fi-test-plain-secret-not-real")

    return Stored(canary_name, canary_value, unset_name)


EXPECTED_COUNTS = {
    "tables": {
        "ApiKey": {
            "rows_scanned": 6,
            "count_star": 6,
            "rows_with_reference": 4,
            "rows_with_reference_not_deleted": 3,
            "rows_unreadable": 1,
            "rows_with_identity_fields": 0,
            "rows_with_external_identity": 0,
        },
        "CustomAIModel": {
            "rows_scanned": 2,
            "count_star": 2,
            "rows_with_reference": 2,
            "rows_with_reference_not_deleted": 2,
            "rows_unreadable": 0,
            "rows_with_identity_fields": 0,
            "rows_with_external_identity": 0,
        },
        "SecretModel": {
            "rows_scanned": 2,
            "count_star": 2,
            "rows_with_reference": 1,
            "rows_with_reference_not_deleted": 1,
            "rows_unreadable": 0,
            "rows_with_identity_fields": 0,
            "rows_with_external_identity": 0,
        },
    },
    "orgs_with_reference": 2,
}


def _integers_only(value):
    if isinstance(value, dict):
        return all(_integers_only(v) for v in value.values())
    return isinstance(value, int) and not isinstance(value, bool)


def _assert_nothing_shown(output, stored):
    assert stored.canary_value not in output
    assert AUDIENCE not in output
    assert "fi-test-audience" not in output
    assert GARBAGE not in output
    assert "pasted" not in output
    assert OTHER_ISSUER not in output
    assert "sk-fi-test" not in output
    lowered = output.lower()
    assert "environ" not in lowered
    assert "oidc" not in lowered


@pytest.mark.django_db
def test_the_count_prints_integers_only_per_table_and_overall(stored):
    output = _run()

    report = json.loads(output)
    assert report == EXPECTED_COUNTS
    assert _integers_only(report)
    for table, model in (
        ("ApiKey", ApiKey),
        ("CustomAIModel", CustomAIModel),
        ("SecretModel", SecretModel),
    ):
        counts = report["tables"][table]
        assert counts["rows_scanned"] == counts["count_star"]
        assert counts["count_star"] == model._base_manager.count()
    assert stored.canary_name not in output
    assert stored.unset_name not in output
    _assert_nothing_shown(output, stored)


TENANT_AWS_KEYS = {
    "aws_access_key_id": "fi-test-access-key-not-real",
    "aws_secret_access_key": "fi-test-synthetic-secret-not-real",
    "aws_region_name": "us-east-1",
}
IDENTITY_VALUE = "fi-test-identity-value-not-real"


@pytest.fixture
def rows_setting_identity_fields(organization):
    """Rows stored before such configurations were refused: one of the
    reserved identity fields at the top level, nested in a dict, in a
    list, as JSON text, padded and in upper case, in a deleted row; and rows
    with none."""
    _provider_key(
        organization,
        "bedrock",
        config_json={**TENANT_AWS_KEYS, "aws_role_name": IDENTITY_VALUE},
    )
    _provider_key(
        organization,
        "vertex_ai",
        config_json={
            "type": "service_account",
            "options": {"aws_sts_endpoint": IDENTITY_VALUE},
        },
    )
    _provider_key(
        organization,
        "sagemaker",
        key=json.dumps({**TENANT_AWS_KEYS, " AWS_PROFILE_NAME ": IDENTITY_VALUE}),
    )
    _provider_key(
        organization, "azure", config_json={"api_key": "fi-test-key-not-real"}
    )
    _custom_model(
        organization,
        "fi-test-listed-model",
        {
            "key": "sk-fi-test-not-real",
            "more": [{"aws_external_id": IDENTITY_VALUE}],
        },
    )
    deleted = _custom_model(
        organization, "fi-test-deleted-model", {"aws_session_name": IDENTITY_VALUE}
    )
    CustomAIModel._base_manager.filter(pk=deleted.pk).update(deleted=True)
    _custom_model(organization, "fi-test-plain-model", {"key": "sk-fi-test-not-real"})
    _secret(organization, "fi-test-plain-secret", "fi-test-plain-secret-not-real")


@pytest.mark.django_db
def test_the_count_prints_how_many_rows_set_the_platforms_own_identity_fields(
    rows_setting_identity_fields,
):
    output = _run()

    report = json.loads(output)
    tables = report["tables"]
    assert tables["ApiKey"]["rows_with_identity_fields"] == 3
    assert tables["CustomAIModel"]["rows_with_identity_fields"] == 2
    assert tables["SecretModel"]["rows_with_identity_fields"] == 0
    assert _integers_only(report)
    folded = output.lower()
    assert IDENTITY_VALUE not in folded
    for needle in ("aws_", "role", "sts", "profile", "session"):
        assert needle not in folded, needle


SERVICE_ACCOUNT_KEY = {
    "type": "service_account",
    "project_id": "fi-test-project",
    "private_key": "fi-test-private-key-not-real",
    "client_email": "fi-test@fi-test-project.iam.gserviceaccount.com",
}
TENANT_ADDRESS = "https://tenant.example/v1/exchange"


@pytest.fixture
def rows_of_another_kind(organization):
    """Rows stored before such configurations were refused. For Vertex AI:
    another type, keys only another kind of configuration has (at the top
    level, nested and as JSON text) and a service-account key that signs in
    elsewhere. For other providers: another kind of Google credential, as
    given and nested. Rows not counted: a plain service-account key, AWS
    keys, and a secret (no model configuration) that gives such a type."""
    _provider_key(
        organization,
        "vertex_ai",
        config_json={
            "type": "authorized_user",
            "client_id": "fi-test-client-not-real",
            "refresh_token": "fi-test-refresh-not-real",
        },
    )
    _provider_key(
        organization,
        "vertex_ai",
        key=json.dumps({**SERVICE_ACCOUNT_KEY, "token_url": TENANT_ADDRESS}),
    )
    _provider_key(organization, "vertex_ai", config_json=SERVICE_ACCOUNT_KEY)
    _provider_key(
        organization,
        "vertex_ai",
        config_json={**SERVICE_ACCOUNT_KEY, "token_uri": TENANT_ADDRESS},
    )
    _provider_key(
        organization,
        "anthropic",
        config_json={
            "api_key": "fi-test-key-not-real",
            "options": {"credential_source": {"file": "/var/run/tenant/subject"}},
        },
    )
    _provider_key(organization, "bedrock", config_json=TENANT_AWS_KEYS)
    _custom_model(
        organization,
        "vertex_ai/fi-test-of-another-kind",
        {
            "type": "external_account",
            "token_url": TENANT_ADDRESS,
            "credential_source": {"file": "/var/run/tenant/subject"},
        },
        provider="vertex_ai",
    )
    _custom_model(
        organization,
        "vertex_ai/fi-test-with-a-nested-key",
        {**SERVICE_ACCOUNT_KEY, "options": [{"aws_region": "us-east-1"}]},
        provider="vertex_ai",
    )
    _custom_model(
        organization,
        "vertex_ai/fi-test-plain",
        SERVICE_ACCOUNT_KEY,
        provider="vertex_ai",
    )
    _custom_model(
        organization,
        "fi-test-openai-model",
        {"key": "sk-fi-test-not-real", "type": "external_account"},
    )
    _secret(organization, "fi-test-a-secret", json.dumps({"type": "external_account"}))


@pytest.mark.django_db
def test_the_count_prints_how_many_rows_are_refused_as_a_google_credential(
    rows_of_another_kind,
):
    output = _run()

    report = json.loads(output)
    tables = report["tables"]
    assert tables["ApiKey"]["rows_with_external_identity"] == 4
    assert tables["CustomAIModel"]["rows_with_external_identity"] == 3
    assert tables["SecretModel"]["rows_with_external_identity"] == 0
    assert _integers_only(report)
    folded = output.lower()
    for needle in ("tenant.example", "tenant/subject", "fi-test-client", "aws_"):
        assert needle not in folded, needle


VERTEX_ROWS_WITHOUT_A_KEY = "--vertex-rows-without-a-key"


@pytest.fixture
def stored_vertex_rows(organization):
    """Rows stored for Vertex AI with no service-account key of the
    customer's own: a key given as text, an empty configuration, nothing,
    a location alone, a custom model's key alone and an empty one, and a
    deleted key; rows not counted: a service-account key, as an object and
    as JSON text, and other providers' keys. Every value carries a tag the
    output must never show. (the ids of the counted rows, the tag)"""
    tag = f"fi-test-tag-{uuid.uuid4().hex[:12]}"
    key_alone = f"fi-test-vertex-key-{tag}"
    service_account_key = {
        **SERVICE_ACCOUNT_KEY,
        "private_key": f"fi-test-private-key-{tag}",
    }
    counted = {
        "ApiKey": [
            _provider_key(organization, "vertex_ai", key=key_alone),
            _provider_key(organization, "vertex_ai", config_json={}),
            _provider_key(organization, "vertex_ai"),
            _provider_key(
                organization, "vertex_ai", config_json={"location": f"loc-{tag}"}
            ),
        ],
        "CustomAIModel": [
            _custom_model(
                organization,
                "vertex_ai/fi-test-key-alone",
                {"key": key_alone},
                provider="vertex_ai",
            ),
            _custom_model(
                organization, "vertex_ai/fi-test-empty", {}, provider="vertex_ai"
            ),
        ],
    }
    deleted = _provider_key(organization, "vertex_ai", key=key_alone)
    ApiKey._base_manager.filter(pk=deleted.pk).update(deleted=True)
    counted["ApiKey"].append(deleted)

    _provider_key(organization, "vertex_ai", config_json=service_account_key)
    _provider_key(organization, "vertex_ai", key=json.dumps(service_account_key))
    _provider_key(organization, "openai", key=f"sk-{tag}")
    _custom_model(
        organization,
        "vertex_ai/fi-test-with-a-key",
        {**service_account_key, "location": f"loc-{tag}"},
        provider="vertex_ai",
    )
    _custom_model(organization, "fi-test-openai-model", {"key": key_alone})
    ids = {label: {str(row.pk) for row in rows} for label, rows in counted.items()}
    return ids, tag


@pytest.mark.django_db
def test_the_count_lists_stored_vertex_rows_without_a_key_of_their_own(
    stored_vertex_rows,
):
    ids, tag = stored_vertex_rows
    output = _run(VERTEX_ROWS_WITHOUT_A_KEY)

    listed = json.loads(output)["vertex_without_a_key"]
    assert set(listed) == {"ApiKey", "CustomAIModel"}
    for label, expected in ids.items():
        assert set(listed[label]) == {"rows", "rows_not_deleted", "ids"}
        assert set(listed[label]["ids"]) == expected
        assert listed[label]["rows"] == len(expected)
    assert listed["ApiKey"]["rows_not_deleted"] == 4
    assert listed["CustomAIModel"]["rows_not_deleted"] == 2
    folded = output.lower()
    for needle in (tag, "fi-test-vertex-key", "service_account", "private"):
        assert needle.lower() not in folded, needle


@pytest.mark.django_db
def test_without_the_option_the_count_lists_no_vertex_rows(stored_vertex_rows):
    assert "vertex_without_a_key" not in json.loads(_run())


@pytest.mark.django_db
def test_names_are_printed_only_under_the_three_constraints(stored):
    output = _run("--names")

    report = json.loads(output)
    assert report == {
        **EXPECTED_COUNTS,
        "names": [
            {
                "name": stored.canary_name,
                "exposed_candidate": True,
                "rows": 3,
                "orgs": 1,
            },
            {
                "name": stored.unset_name,
                "exposed_candidate": False,
                "rows": 1,
                "orgs": 1,
            },
        ],
        "issuers": [
            {"issuer": "google", "rows": 1, "orgs": 1},
            {"issuer": "other", "rows": 1, "orgs": 1},
        ],
        "refs_unprintable": 1,
    }
    _assert_nothing_shown(output, stored)


@pytest.mark.django_db
def test_counting_writes_nothing(stored):
    with CaptureQueriesContext(connection) as queries:
        _run("--names")

    statements = [query["sql"] for query in queries.captured_queries]
    assert statements
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements), (
        statements
    )


PREFIXES = [["manage.py"], ["python", "-m", "django"], ["django-admin"]]
PREFIX_IDS = ["manage-py", "python-m-django", "django-admin"]


@pytest.mark.parametrize("args", [[], ["--names"]], ids=["counts", "names"])
@pytest.mark.parametrize("prefix", PREFIXES, ids=PREFIX_IDS)
def test_the_startup_guard_admits_the_count_in_any_process(prefix, args):
    assert guarded_management_command([*prefix, COMMAND, *args]) is None
