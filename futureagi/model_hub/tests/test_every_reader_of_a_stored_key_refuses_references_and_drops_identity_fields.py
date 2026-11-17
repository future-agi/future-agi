"""Every reader of a stored key holds it to the rule every call holds it to.

A stored key or configuration holds literal values only and sets none of the
reserved identity fields. Three more places that read one hold it to that
rule:

- the vector database column refuses an embedding key stored with a
  reference (such as os.environ/... or oidc/...) instead of a literal value,
  with the one plain sentence the other refusals use and before any
  embedding call, and gives the embedding call a stored key without the
  reserved identity fields at any depth;
- Run Prompt's custom endpoint model reads its stored configuration without
  the reserved identity fields at any depth, so its request carries none of
  them as a header;
- an evaluation's model call gives the call a stored configuration without
  the reserved identity fields at any depth.

A literal key, a custom endpoint's other headers and a configuration's own
settings reach their calls as stored.

The tests use made-up names, keys and example.test addresses; every model,
embedding and vector database call is replaced by a stand-in that sends
nothing.
"""

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import litellm
import pytest

from agentic_eval.core.llm import llm as llm_module
from agentic_eval.core_evals.run_prompt.runprompt_handlers.base_handler import (
    ModelHandlerContext,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.custom_model_handler import (
    CustomModelHandler,
)
from model_hub.models.api_key import ApiKey, SecretModel
from model_hub.models.custom_models import CustomAIModel
from model_hub.tests.test_dataset_list_tenancy import tenants as tenants_fixture
from model_hub.utils.secret_scope import request_authority
from model_hub.views import dynamic_columns
from tfc.middleware.workspace_context import clear_workspace_context

tenants = tenants_fixture

REFUSAL = (
    "This model configuration is not valid; enter the key and settings from "
    "your provider."
)
CHAT = [{"role": "user", "content": "hi"}]
LITERAL_KEY = "fi-test-embedding-key-not-real"
# A made-up name no deployment sets, written the ways the rule reads a
# reference: trimmed and in any case.
REFERENCES = {
    "a-name": "os.environ/FI_TEST_UNSET_NAME",
    "a-name-padded-and-in-capitals": "  OS.ENVIRON/FI_TEST_UNSET_NAME\t",
    "an-issuer": "oidc/google/fi-test-unset-issuer",
}
# Reserved identity fields: a model configuration may not set them,
# whatever their value.
IDENTITY_FIELDS = (
    "aws_web_identity_token",
    "aws_sts_endpoint",
    "aws_profile_name",
    "aws_role_name",
    "aws_session_name",
    "aws_bedrock_runtime_endpoint",
    "aws_external_id",
)
FIELD_IDS = [f"field-{number}" for number in range(1, len(IDENTITY_FIELDS) + 1)]
ENDPOINT_MODEL = "fi-test-endpoint-model"
ENDPOINT_URL = "https://models.example.test/v1/chat/completions"
BEDROCK_MODEL = "anthropic.claude-3-haiku-20240307-v1:0"
BEDROCK_KEYS = {
    "aws_access_key_id": "fi-test-access-key-not-real",
    "aws_secret_access_key": "fi-test-secret-key-not-real",
    "aws_region_name": "us-east-1",
}


def _names_at_any_depth(value):
    """Every dict key in ``value`` at any depth, trimmed and in lower case;
    JSON text is read as what it holds."""
    names, pending = set(), [value]
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            try:
                current = json.loads(current)
            except ValueError:
                continue
        if isinstance(current, dict):
            names.update(str(name).strip().lower() for name in current)
            pending.extend(current.values())
        elif isinstance(current, list | tuple):
            pending.extend(current)
    return names


# ------------------------------------------- the vector database column ---


def _vector_query(tenant, stored_key):
    """Store ``stored_key`` as the organization's embedding key and run the
    vector database column's query for one row. (what the query answers,
    the embedding calls made)."""
    clear_workspace_context()
    secret = SecretModel.no_workspace_objects.create(
        name="fi-test-vector-secret",
        organization=tenant["org"],
        workspace=tenant["workspace"],
        key="fi-test-vector-credential-not-real",
    )
    ApiKey.no_workspace_objects.create(
        organization=tenant["org"],
        workspace=tenant["workspace"],
        user=tenant["user"],
        provider="openai",
        key=stored_key,
    )
    config = {
        "column_id": str(tenant["column"].pk),
        "sub_type": "pinecone",
        "api_key": str(secret.id),
        "url": "https://vectors.example.test",
        "index_name": "fi-test-index",
        "collection_name": "fi-test-collection",
        "top_k": 1,
        "embedding_config": {"type": "openai", "model": "fi-test-embedding-model"},
    }
    vector_database = Mock()
    vector_database.Pinecone.return_value.Index.return_value.query.return_value = {
        "matches": []
    }
    authority = request_authority(
        SimpleNamespace(
            user=tenant["user"],
            organization=tenant["org"],
            workspace=tenant["workspace"],
        ),
        tenant["dataset"].pk,
    )
    with (
        patch("tfc.utils.lazy_extras.load_extra", return_value=vector_database),
        patch.object(dynamic_columns, "require_serving"),
        patch.object(
            dynamic_columns.model_manager, "get_embeddings", return_value=[0.1]
        ) as embeddings,
    ):
        answered = dynamic_columns.AddVectorDBColumnView()._query_vector_db(
            "fi-test text",
            config,
            tenant["org"].id,
            tenant["workspace"].id,
            authority=authority,
        )
    return answered, embeddings.call_args_list


def _embedding_key(call):
    return call.kwargs["model_params"]["api_key"]


@pytest.mark.django_db
@pytest.mark.parametrize("written", list(REFERENCES), ids=list(REFERENCES))
def test_an_embedding_key_stored_with_a_reference_is_refused_before_any_embedding_call(
    tenants, written
):
    a, _b, _clients = tenants

    answered, embedding_calls = _vector_query(a, REFERENCES[written])

    assert embedding_calls == [], "an embedding call was made"
    assert answered == REFUSAL, "the key was not refused"


@pytest.mark.django_db
@pytest.mark.parametrize("field", IDENTITY_FIELDS, ids=FIELD_IDS)
def test_an_embedding_key_reaches_the_embedding_call_without_the_identity_fields(
    tenants, field
):
    a, _b, _clients = tenants
    stored = {"api_key": LITERAL_KEY, "options": {field: "fi-test-value"}}

    _answered, embedding_calls = _vector_query(a, json.dumps(stored))

    assert len(embedding_calls) == 1, "the embedding call was not made"
    given = _embedding_key(embedding_calls[0])
    assert not _names_at_any_depth(given) & set(IDENTITY_FIELDS), (
        "an identity field reached the embedding call"
    )
    assert json.loads(given)["api_key"] == LITERAL_KEY


@pytest.mark.django_db
def test_a_literal_embedding_key_reaches_the_embedding_call_as_stored(tenants):
    a, _b, _clients = tenants

    _answered, embedding_calls = _vector_query(a, LITERAL_KEY)

    assert len(embedding_calls) == 1, "the embedding call was not made"
    assert _embedding_key(embedding_calls[0]) == LITERAL_KEY


# ------------------------------------------- a custom endpoint model ---


def _endpoint_headers(organization, user, workspace, headers):
    """The headers Run Prompt's custom endpoint model reads from a stored
    row holding ``headers``, written straight to its table."""
    CustomAIModel.objects.create(
        user_model_id=ENDPOINT_MODEL,
        provider="custom",
        input_token_cost=0.0,
        output_token_cost=0.0,
        organization=organization,
        workspace=workspace,
        user=user,
        key_config={"api_base": ENDPOINT_URL, "headers": headers},
    )
    handler = CustomModelHandler(
        ModelHandlerContext(
            model=ENDPOINT_MODEL,
            messages=CHAT,
            organization_id=organization.id,
            provider="custom",
        )
    )
    return handler._custom_model_config


@pytest.mark.django_db
@pytest.mark.parametrize("field", IDENTITY_FIELDS, ids=FIELD_IDS)
def test_a_custom_endpoint_request_carries_no_identity_field_as_a_header(
    organization, user, workspace, field
):
    config = _endpoint_headers(
        organization,
        user,
        workspace,
        {"X-Fi-Test": "fi-test", field: "fi-test-value", field.upper(): "fi-test"},
    )

    assert config["endpoint_url"] == ENDPOINT_URL
    assert config["headers"] == {"X-Fi-Test": "fi-test"}


@pytest.mark.django_db
def test_a_custom_endpoint_request_carries_its_stored_headers(
    organization, user, workspace
):
    headers = {"X-Fi-Test": "fi-test", "X-Fi-Test-Team": "fi-test-team"}

    config = _endpoint_headers(organization, user, workspace, headers)

    assert config["endpoint_url"] == ENDPOINT_URL
    assert config["headers"] == headers


# ------------------------------------------- an evaluation's model call ---


class _CallMade(Exception):
    """The model call was reached."""


class _Recorder:
    """Stands in for a model call: records what it is given, sends nothing."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise _CallMade()


@pytest.fixture
def no_model_call(monkeypatch):
    """Every model call replaced by one recorder."""
    recorder = _Recorder()
    for name in ("completion", "acompletion", "image_generation"):
        monkeypatch.setattr(litellm, name, recorder)
    return recorder


def _evaluation_call(configuration):
    """An evaluation's model call for a Bedrock model given
    ``configuration``; the error it raised, or None."""
    llm = llm_module.LLM(
        provider="bedrock", model_name=BEDROCK_MODEL, api_key=configuration
    )
    try:
        llm.call_llm(CHAT, provider="bedrock")
    except Exception as exc:  # what the stand-in raised, as the call answers it
        return exc
    return None


@pytest.mark.parametrize("field", IDENTITY_FIELDS, ids=FIELD_IDS)
def test_an_evaluation_call_carries_no_identity_field_at_any_depth(
    no_model_call, field
):
    configuration = {
        **BEDROCK_KEYS,
        "aws_session_token": {"token": "fi-test-token", field: "fi-test-value"},
    }

    _evaluation_call(configuration)

    assert no_model_call.calls, "the model call was not made"
    for _args, arguments in no_model_call.calls:
        assert not _names_at_any_depth(arguments) & set(IDENTITY_FIELDS), (
            "an identity field reached the call"
        )
        assert arguments["aws_session_token"] == {"token": "fi-test-token"}


def test_an_evaluation_call_carries_its_stored_settings(no_model_call):
    _evaluation_call(dict(BEDROCK_KEYS))

    assert len(no_model_call.calls) == 1, "the model call was not made"
    _args, arguments = no_model_call.calls[0]
    assert {name: arguments.get(name) for name in BEDROCK_KEYS} == BEDROCK_KEYS
    assert arguments["model"] == BEDROCK_MODEL
