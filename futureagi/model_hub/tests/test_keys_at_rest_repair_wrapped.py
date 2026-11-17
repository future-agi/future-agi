"""Model-hub keys an earlier release wrapped: named for their organizations,
never unwrapped.

A process of an earlier release that saves a row this release encrypted
wraps the stored ``enc::`` token in the old encoding, and the app then
refuses that key. A person typing another row's stored token as a key into
such a process leaves the same stored form. The token holds only
``{"v": key}``, so nothing proves a wrapped token is its own row's: the
command refuses every wrapped key, leaves it as it is, and names it by model,
row id, organization id and field, never by a token or a key, so its
organization can enter the key again. ``--check`` names them too, and still
fails only on keys left unencrypted.

Every key here is a generated test value, encrypted under the test settings'
key or a key made up in the test.
"""

import base64
import json
from io import StringIO

import pytest
from cryptography.fernet import Fernet
from django.conf import settings
from django.core.management import CommandError, call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models import Organization
from model_hub.models.api_key import ApiKey, SecretModel, SecretType
from model_hub.models.custom_models import CustomAIModel
from model_hub.services import key_at_rest

PREFIX = "enc::"
COMMAND = "model_hub_keys_at_rest"
NOT_PROVEN = "cannot be proven to be this row's own"
NOT_DECRYPTABLE = "is not one this deployment can decrypt"
TABLES = (
    ApiKey._meta.db_table,
    CustomAIModel._meta.db_table,
    SecretModel._meta.db_table,
)

pytestmark = pytest.mark.django_db


# --- helpers -----------------------------------------------------------------


def _old_encoding(text):
    """How a process of the earlier release stores a key it saves."""
    return base64.b64encode(settings.SECRET_KEY[:16].encode() + text.encode()).decode()


def _raw(model, pk, column):
    """The column exactly as the database holds it."""
    table = connection.ops.quote_name(model._meta.db_table)
    col = connection.ops.quote_name(column)
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {col}::text FROM {table} WHERE id = %s", [str(pk)])
        text = cursor.fetchone()[0]
    return json.loads(text) if column != "key" and text is not None else text


def _saved_key(organization, user, provider, clear):
    """A key saved by this release: its row's own ciphertext."""
    row = ApiKey.no_workspace_objects.create(
        organization=organization, user=user, provider=provider, key=clear
    )
    stored = _raw(ApiKey, row.pk, "key")
    assert stored.startswith(PREFIX)
    return row, stored


def _key_row(organization, user, provider, *, key=None, config=None, deleted=False):
    """A row whose stored columns are set as given."""
    row = ApiKey.no_workspace_objects.create(
        organization=organization, user=user, provider=provider, key="placeholder"
    )
    ApiKey._base_manager.filter(pk=row.pk).update(
        key=key, config_json=config, deleted=deleted
    )
    return row


def _foreign_token(clear):
    """An ``enc::`` token made under a key this deployment does not have."""
    token = Fernet(Fernet.generate_key()).encrypt(json.dumps({"v": clear}).encode())
    return PREFIX + token.decode()


def _run(*args):
    out = StringIO()
    call_command(COMMAND, *args, stdout=out)
    return out.getvalue()


def _refused(model, row, field):
    return f"refused {model} {row.pk} (organization {row.organization_id}) {field}:"


def _found(text, values):
    """The positions in ``values`` of every value ``text`` carries (positions
    only, so a failure never prints a key)."""
    return [i for i, value in enumerate(values) if value and value in text]


def _writes(queries):
    """Statements that would change a key table."""
    return [
        q["sql"]
        for q in queries
        if q["sql"].lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE"))
        and any(table in q["sql"] for table in TABLES)
    ]


@pytest.fixture
def other_organization():
    return Organization.objects.create(name="Second synthetic organization")


@pytest.fixture
def own_wrapped(organization, user):
    """A key this release encrypted, then saved by an old process: its own
    token, wrapped in the old encoding."""
    row, stored = _saved_key(
        organization, user, "openai", "sk-test-wrapped-own-not-real"
    )
    ApiKey._base_manager.filter(pk=row.pk).update(key=_old_encoding(stored))
    return row, stored


@pytest.fixture
def borrowed(organization, user, other_organization):
    """Another organization's row carrying the token of a row that still
    holds it: the stored form an old process leaves when someone types that
    token as a key."""
    owner, token = _saved_key(
        organization, user, "anthropic", "sk-test-token-owner-not-real"
    )
    carrier = _key_row(other_organization, user, "groq", key=_old_encoding(token))
    return owner, token, carrier


# --- listing -------------------------------------------------------------------


def test_a_key_an_old_process_wrapped_is_listed_and_left_as_it_is(own_wrapped):
    row, stored = own_wrapped
    before = _raw(ApiKey, row.pk, "key")

    said = _run("--repair-wrapped")

    assert "1 row(s) hold a key an earlier release wrapped" in said
    assert _refused("ApiKey", row, "key") in said
    assert NOT_PROVEN in said
    assert "its organization must enter the key again" in said
    assert "listed only; nothing was written" in said
    assert _raw(ApiKey, row.pk, "key") == before


def test_a_key_carrying_another_rows_token_is_refused_and_left_unchanged(borrowed):
    owner, token, carrier = borrowed
    carried = _raw(ApiKey, carrier.pk, "key")

    said = _run("--repair-wrapped", "--confirm")

    assert _refused("ApiKey", carrier, "key") in said
    assert "0 row(s) repaired, 1 refused; nothing was written" in said
    assert _raw(ApiKey, carrier.pk, "key") == carried
    # The carrier still cannot be read; the owner keeps its token and key.
    with pytest.raises(ValueError, match="cannot be decrypted"):
        _ = ApiKey._base_manager.get(pk=carrier.pk).actual_key
    assert _raw(ApiKey, owner.pk, "key") == token
    owner_key = ApiKey._base_manager.get(pk=owner.pk).actual_key
    assert owner_key == "sk-test-token-owner-not-real"
    # The owner is not a wrapped row and is not named.
    assert str(owner.pk) not in said


def test_every_key_column_is_covered_soft_deleted_rows_included(
    organization, user, workspace, own_wrapped
):
    # A JSON key's string, a custom model's key and a dataset secret, each
    # wrapped by an old process, and a soft-deleted wrapped key.
    azure = ApiKey.no_workspace_objects.create(
        organization=organization,
        user=user,
        provider="azure",
        config_json={
            "api_key": "sk-test-wrapped-azure-not-real",
            "api_base": "https://wrapped.openai.azure.example.test",
        },
    )
    stored = _raw(ApiKey, azure.pk, "config_json")
    ApiKey._base_manager.filter(pk=azure.pk).update(
        config_json={**stored, "api_key": _old_encoding(stored["api_key"])}
    )
    custom = CustomAIModel.objects.create(
        user_model_id="wrapped-custom",
        provider="openai",
        input_token_cost=0.0,
        output_token_cost=0.0,
        organization=organization,
        workspace=workspace,
        user=user,
        key_config={"key": "sk-test-wrapped-custom-not-real"},
    )
    stored = _raw(CustomAIModel, custom.pk, "key_config")
    CustomAIModel._base_manager.filter(pk=custom.pk).update(
        key_config={"key": _old_encoding(stored["key"])}
    )
    secret = SecretModel.no_workspace_objects.create(
        name="wrapped-secret",
        organization=organization,
        workspace=workspace,
        secret_type=SecretType.API_KEY,
        key="sk-test-wrapped-secret-not-real",
    )
    SecretModel._base_manager.filter(pk=secret.pk).update(
        key=_old_encoding(_raw(SecretModel, secret.pk, "key"))
    )
    _, token = _saved_key(organization, user, "mistral", "sk-test-gone-not-real")
    deleted = _key_row(
        organization, user, "cohere", key=_old_encoding(token), deleted=True
    )
    # Not wrapped, so not named: a key this release encrypted, and one still
    # in the old encoding (that one is for --encrypt).
    plain, _ = _saved_key(organization, user, "gemini", "sk-test-plain-not-real")
    legacy = _key_row(
        organization, user, "together_ai", key=_old_encoding("sk-test-legacy")
    )

    said = _run("--repair-wrapped")

    row, _ = own_wrapped
    for name, wrapped_row, field in (
        ("ApiKey", row, "key"),
        ("ApiKey", azure, "config_json"),
        ("CustomAIModel", custom, "key_config"),
        ("SecretModel", secret, "key"),
        ("ApiKey", deleted, "key"),
    ):
        assert _refused(name, wrapped_row, field) in said
    assert "5 row(s) hold a key an earlier release wrapped" in said
    assert str(plain.pk) not in said
    assert str(legacy.pk) not in said


def test_a_wrapped_token_from_another_deployment_gives_its_own_reason(
    organization, user
):
    row = _key_row(
        organization,
        user,
        "openai",
        key=_old_encoding(_foreign_token("sk-test-elsewhere-not-real")),
    )

    said = _run("--repair-wrapped")

    assert _refused("ApiKey", row, "key") in said
    assert NOT_DECRYPTABLE in said
    assert NOT_PROVEN not in said


# --- writing -------------------------------------------------------------------


def test_confirm_writes_nothing_and_a_second_run_changes_nothing(own_wrapped, borrowed):
    # A wrapped token names no row, so not even a row's own token can be
    # proven to be its own: --confirm rewrites none of them.
    row, _ = own_wrapped
    _, _, carrier = borrowed
    before = (_raw(ApiKey, row.pk, "key"), _raw(ApiKey, carrier.pk, "key"))

    with CaptureQueriesContext(connection) as queries:
        first = _run("--repair-wrapped", "--confirm")
    assert _writes(queries.captured_queries) == []
    assert "0 row(s) repaired, 2 refused; nothing was written" in first
    assert (_raw(ApiKey, row.pk, "key"), _raw(ApiKey, carrier.pk, "key")) == before
    with pytest.raises(ValueError, match="cannot be decrypted"):
        _ = ApiKey._base_manager.get(pk=row.pk).actual_key

    with CaptureQueriesContext(connection) as queries:
        second = _run("--repair-wrapped", "--confirm")
    assert _writes(queries.captured_queries) == []
    assert second == first
    assert (_raw(ApiKey, row.pk, "key"), _raw(ApiKey, carrier.pk, "key")) == before


def test_without_confirm_nothing_is_written(own_wrapped, borrowed):
    row, _ = own_wrapped
    _, _, carrier = borrowed
    before = (_raw(ApiKey, row.pk, "key"), _raw(ApiKey, carrier.pk, "key"))

    with CaptureQueriesContext(connection) as queries:
        said = _run("--repair-wrapped")

    assert _writes(queries.captured_queries) == []
    assert "listed only; nothing was written" in said
    assert (_raw(ApiKey, row.pk, "key"), _raw(ApiKey, carrier.pk, "key")) == before


# --- the output ----------------------------------------------------------------


def test_the_output_carries_no_token_and_no_key(own_wrapped, borrowed, capsys, caplog):
    row, own_token = own_wrapped
    _, borrowed_token, carrier = borrowed
    secrets = [
        "sk-test-wrapped-own-not-real",
        "sk-test-token-owner-not-real",
        own_token,
        own_token[len(PREFIX) :],
        borrowed_token,
        borrowed_token[len(PREFIX) :],
        _raw(ApiKey, row.pk, "key"),
        _raw(ApiKey, carrier.pk, "key"),
    ]

    said = _run("--repair-wrapped") + _run("--repair-wrapped", "--confirm")
    said += _run("--check")

    assert str(row.pk) in said and str(carrier.pk) in said
    printed = capsys.readouterr()
    shown = said + printed.out + printed.err + caplog.text
    assert _found(shown, secrets) == []


# --- --check -------------------------------------------------------------------


def test_check_names_wrapped_keys_and_fails_only_on_unencrypted_ones(
    organization, user, own_wrapped, borrowed
):
    row, _ = own_wrapped
    _, _, carrier = borrowed

    said = _run("--check")

    assert "0 not encrypted, 2 cannot be decrypted" in said
    assert "2 row(s) hold a key an earlier release wrapped" in said
    assert _refused("ApiKey", row, "key") in said
    assert _refused("ApiKey", carrier, "key") in said

    # A key still in the old encoding fails the check, as before, and the
    # wrapped keys are still named.
    _key_row(organization, user, "fireworks_ai", key=_old_encoding("sk-test-old"))
    out = StringIO()
    with pytest.raises(CommandError, match="1 model-hub key row"):
        call_command(COMMAND, "--check", stdout=out)
    assert _refused("ApiKey", row, "key") in out.getvalue()


def test_check_says_nothing_about_wrapped_keys_when_there_are_none(organization, user):
    _saved_key(organization, user, "openai", "sk-test-only-plain-not-real")

    said = _run("--check")

    assert "0 not encrypted, 0 cannot be decrypted" in said
    assert "wrapped" not in said


# --- the reader is unchanged ---------------------------------------------------


def test_the_reader_still_refuses_a_wrapped_key(own_wrapped):
    row, _ = own_wrapped
    stored = _raw(ApiKey, row.pk, "key")

    assert key_at_rest.read(stored) == (key_at_rest.UNREADABLE, None)
    loaded = ApiKey._base_manager.get(pk=row.pk)
    with pytest.raises(ValueError, match="cannot be decrypted"):
        _ = loaded.actual_key
    assert loaded.masked_actual_key == key_at_rest.UNREADABLE_MASK
