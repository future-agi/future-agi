"""A scenario whose generation failed records one plain reason, and the
scenario detail API returns it.

Covers the reason chosen for an error (a missing model provider, the other
known causes, anything else), the rule that a recorded reason is kept, each
generation step that marks a scenario failed, and the detail API.
"""

import sys
import types
import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from model_hub.models.choices import StatusType
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from simulate.models import Scenarios
from simulate.services.scenario_failure import (
    FAILURE_REASON_KEY,
    MODEL_RATE_LIMITED,
    MODEL_REFUSED,
    MODEL_TIMED_OUT,
    NO_MODEL_PROVIDER,
    UNEXPECTED_ERROR,
    USAGE_LIMIT_REACHED,
    apply_failure_reason,
    failure_reason_for,
    record_failure_reason,
)
from tfc.temporal.simulate.activities import (
    _create_dataset_scenario_sync,
    _create_script_scenario_sync,
    _finalize_graph_scenario_sync,
    _prepare_scenario_sync,
    _setup_graph_scenario_sync,
)

# What the model client raises when the deployment has no Vertex AI
# credentials, before any request is sent.
MISSING_VERTEX_CREDENTIALS = (
    "GOOGLE_APPLICATION_CREDENTIALS environment variable must be set"
)


class AuthenticationError(Exception):
    """Named like the litellm and openai class a rejected key raises."""


class RateLimitError(Exception):
    """Named like the litellm and openai class a throttled request raises."""


class ContentPolicyViolationError(Exception):
    """Named like the litellm class a refused prompt raises."""


@pytest.fixture
def allow_ee_feature_checks():
    """These tests cover the failure reason, not plan gates."""
    with patch("tfc.ee_gating.check_ee_feature", return_value=None):
        yield


@pytest.fixture(autouse=True)
def _allow_ee_feature_checks_for_scenario_api(allow_ee_feature_checks):
    yield


@pytest.fixture
def prompt_scenario(db, organization, workspace):
    """A prompt-source graph scenario still generating, as Create leaves it."""
    template = PromptTemplate.objects.create(
        name="Refund prompt",
        description="Answers refund questions",
        organization=organization,
        workspace=workspace,
    )
    version = PromptVersion.objects.create(
        original_template=template,
        template_version="v1",
        is_default=True,
        commit_message="Initial version",
    )
    return Scenarios.objects.create(
        name="Refund calls",
        description="",
        source="Processing...",
        scenario_type=Scenarios.ScenarioTypes.GRAPH,
        source_type=Scenarios.SourceTypes.PROMPT,
        organization=organization,
        workspace=workspace,
        prompt_template=template,
        prompt_version=version,
        status=StatusType.PROCESSING.value,
        metadata={},
    )


def _fake_module(name, **attributes):
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    return module


@contextmanager
def _replaced_modules(modules):
    """Put ``modules`` in sys.modules and restore only those names after.

    (patch.dict would also drop every module first imported inside the
    block.)
    """
    saved = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    try:
        yield
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


@contextmanager
def _no_model_runs(graph_generator=None, scenarios_agent=None):
    """Replace the ee modules scenario generation imports, so no model runs.

    ``graph_generator`` and ``scenarios_agent`` stand in for the two
    generation classes (pass one that raises to fail that step); the usage
    check allows the run.
    """
    allowed = types.SimpleNamespace(allowed=True, reason="")
    modules = {
        module.__name__: module
        for module in (
            _fake_module(
                "ee.agenthub.scenario_graph.enhanced_scenarios_agent",
                EnhancedScenariosAgent=scenarios_agent or MagicMock(),
            ),
            _fake_module(
                "ee.agenthub.scenario_graph.graph_generator",
                ConversationGraphGenerator=graph_generator or MagicMock(),
            ),
            _fake_module(
                "ee.usage.schemas.event_types",
                BillingEventType=types.SimpleNamespace(
                    SYNTHETIC_DATA_GENERATION="synthetic_data_generation"
                ),
            ),
            _fake_module(
                "ee.usage.services.metering",
                check_usage=lambda *args, **kwargs: allowed,
            ),
        )
    }
    # close_old_connections() in the activity tears down the pytest test
    # transaction; skip it under test.
    with (
        _replaced_modules(modules),
        patch("django.db.close_old_connections"),
    ):
        yield


def _raises(error):
    return MagicMock(side_effect=error)


def _set_up_graph_scenario(scenario, **validated_data):
    return _setup_graph_scenario_sync(
        str(scenario.id),
        {"source_type": "prompt", "no_of_rows": 10, **validated_data},
    )


def _finalize(scenario, dataset_id=""):
    """The graph workflow's last step; no dataset means a step failed."""
    with patch("django.db.close_old_connections"):
        return _finalize_graph_scenario_sync(str(scenario.id), dataset_id, None)


def _failed_with(scenario, reason):
    scenario.refresh_from_db()
    assert scenario.status == StatusType.FAILED.value
    assert scenario.metadata[FAILURE_REASON_KEY] == reason


# ============================================================================
# Which reason an error gets
# ============================================================================


@pytest.mark.unit
class TestFailureReasonFor:
    def test_missing_model_credentials_say_no_provider_is_set_up(self):
        error = ValueError(MISSING_VERTEX_CREDENTIALS)

        assert failure_reason_for(error) == NO_MODEL_PROVIDER

    def test_the_cause_is_found_behind_the_error_that_wraps_it(self):
        try:
            try:
                raise ValueError(MISSING_VERTEX_CREDENTIALS)
            except ValueError:
                raise Exception("Failed to generate graph")  # noqa: B904
        except Exception as wrapped:
            error = wrapped

        assert failure_reason_for(error) == NO_MODEL_PROVIDER

    @pytest.mark.parametrize(
        ("error", "reason"),
        [
            (AuthenticationError("401"), NO_MODEL_PROVIDER),
            (RateLimitError("slow down"), MODEL_RATE_LIMITED),
            (ContentPolicyViolationError("blocked"), MODEL_REFUSED),
            (TimeoutError(), MODEL_TIMED_OUT),
            (
                Exception("Usage paused — you set a budget limit for org"),
                USAGE_LIMIT_REACHED,
            ),
            ("Usage limit exceeded", USAGE_LIMIT_REACHED),
        ],
    )
    def test_known_causes_get_their_reason(self, error, reason):
        assert failure_reason_for(error) == reason

    def test_an_unknown_error_gets_the_calm_generic_sentence(self):
        error = RuntimeError("connection reset by 10.2.3.4 (token=abc123)")

        reason = failure_reason_for(error)

        assert reason == UNEXPECTED_ERROR
        assert "10.2.3.4" not in reason
        assert "abc123" not in reason

    def test_no_error_gets_the_generic_sentence(self):
        assert failure_reason_for(None) == UNEXPECTED_ERROR

    def test_the_reason_never_repeats_the_error_text(self):
        reason = failure_reason_for(ValueError(MISSING_VERTEX_CREDENTIALS))

        assert "GOOGLE_APPLICATION_CREDENTIALS" not in reason


# ============================================================================
# Recording a reason
# ============================================================================


@pytest.mark.integration
class TestRecordingAReason:
    def test_a_recorded_reason_is_kept_over_a_later_one(self, prompt_scenario):
        prompt_scenario.metadata = {FAILURE_REASON_KEY: NO_MODEL_PROVIDER}

        added = apply_failure_reason(prompt_scenario, RuntimeError("later"))

        assert added is False
        assert prompt_scenario.metadata[FAILURE_REASON_KEY] == NO_MODEL_PROVIDER

    def test_other_metadata_is_kept_including_a_json_string(self, prompt_scenario):
        prompt_scenario.metadata = '{"persona_ids": ["p-1"]}'

        added = apply_failure_reason(
            prompt_scenario, ValueError(MISSING_VERTEX_CREDENTIALS)
        )

        assert added is True
        assert prompt_scenario.metadata == {
            "persona_ids": ["p-1"],
            FAILURE_REASON_KEY: NO_MODEL_PROVIDER,
        }

    def test_recording_by_id_saves_the_reason_and_leaves_the_status(
        self, prompt_scenario
    ):
        record_failure_reason(
            prompt_scenario.id, ValueError(MISSING_VERTEX_CREDENTIALS)
        )

        prompt_scenario.refresh_from_db()
        assert prompt_scenario.metadata[FAILURE_REASON_KEY] == NO_MODEL_PROVIDER
        assert prompt_scenario.status == StatusType.PROCESSING.value


# ============================================================================
# Each generation step that marks a scenario failed records a reason
# ============================================================================


@pytest.mark.integration
class TestGraphScenarioFailureRecordsAReason:
    def test_graph_generation_without_a_model_provider_records_why(
        self, prompt_scenario
    ):
        generator = _raises(ValueError(MISSING_VERTEX_CREDENTIALS))

        with _no_model_runs(graph_generator=generator):
            result = _set_up_graph_scenario(prompt_scenario, generate_graph=True)

        assert result["status"] == "FAILED"
        _failed_with(prompt_scenario, NO_MODEL_PROVIDER)

    def test_an_unexpected_graph_error_records_only_the_generic_reason(
        self, prompt_scenario
    ):
        generator = _raises(RuntimeError("socket closed by 10.2.3.4"))

        with _no_model_runs(graph_generator=generator):
            _set_up_graph_scenario(prompt_scenario, generate_graph=True)

        _failed_with(prompt_scenario, UNEXPECTED_ERROR)
        assert "10.2.3.4" not in prompt_scenario.metadata[FAILURE_REASON_KEY]

    def test_a_provided_graph_without_a_model_provider_records_why(
        self, prompt_scenario
    ):
        agent = _raises(ValueError(MISSING_VERTEX_CREDENTIALS))

        with _no_model_runs(scenarios_agent=agent):
            result = _set_up_graph_scenario(
                prompt_scenario, graph={"nodes": [], "edges": []}
            )

        assert result["status"] == "FAILED"
        _failed_with(prompt_scenario, NO_MODEL_PROVIDER)

    def test_a_preparation_error_is_recorded_and_finalize_keeps_it(
        self, prompt_scenario
    ):
        with (
            _no_model_runs(),
            patch(
                "tfc.temporal.simulate.activities._setup_graph_scenario_sync",
                side_effect=ValueError(MISSING_VERTEX_CREDENTIALS),
            ),
        ):
            result = _prepare_scenario_sync(
                str(prompt_scenario.id), {"source_type": "prompt"}
            )

        assert result["status"] == "FAILED"
        prompt_scenario.refresh_from_db()
        assert prompt_scenario.status == StatusType.PROCESSING.value
        assert prompt_scenario.metadata[FAILURE_REASON_KEY] == NO_MODEL_PROVIDER

        assert _finalize(prompt_scenario)["status"] == "FAILED"
        _failed_with(prompt_scenario, NO_MODEL_PROVIDER)

    def test_finalize_keeps_the_reason_setup_recorded(self, prompt_scenario):
        generator = _raises(ValueError(MISSING_VERTEX_CREDENTIALS))
        with _no_model_runs(graph_generator=generator):
            _set_up_graph_scenario(prompt_scenario, generate_graph=True)

        result = _finalize(prompt_scenario)

        assert result["status"] == "FAILED"
        _failed_with(prompt_scenario, NO_MODEL_PROVIDER)

    def test_finalize_records_the_generic_reason_when_none_was_found(
        self, prompt_scenario
    ):
        result = _finalize(prompt_scenario)

        assert result["status"] == "FAILED"
        _failed_with(prompt_scenario, UNEXPECTED_ERROR)

    def test_finalize_failing_itself_records_the_generic_reason(self, prompt_scenario):
        result = _finalize(prompt_scenario, dataset_id=str(uuid.uuid4()))

        assert result["status"] == "FAILED"
        _failed_with(prompt_scenario, UNEXPECTED_ERROR)


@pytest.mark.integration
class TestDatasetAndScriptScenarioFailureRecordsAReason:
    def test_dataset_scenario_without_a_model_provider_records_why(
        self, prompt_scenario, user
    ):
        with (
            _no_model_runs(),
            patch(
                "tfc.temporal.simulate.activities._resolve_scenario_agent",
                side_effect=ValueError(MISSING_VERTEX_CREDENTIALS),
            ),
        ):
            result = _create_dataset_scenario_sync(
                str(user.id), str(prompt_scenario.id), {"source_type": "prompt"}
            )

        assert result["status"] == "FAILED"
        _failed_with(prompt_scenario, NO_MODEL_PROVIDER)

    def test_script_scenario_graph_failure_records_why(self, prompt_scenario):
        generator = _raises(ValueError(MISSING_VERTEX_CREDENTIALS))

        with _no_model_runs(graph_generator=generator):
            result = _create_script_scenario_sync(
                str(prompt_scenario.id),
                {
                    "agent_definition_id": str(uuid.uuid4()),
                    "script_url": "https://example.com/script.txt",
                    "no_of_rows": 10,
                },
            )

        assert result["status"] == "FAILED"
        _failed_with(prompt_scenario, NO_MODEL_PROVIDER)
        script_url = prompt_scenario.metadata["script_url"]
        assert script_url == "https://example.com/script.txt"


# ============================================================================
# The scenario detail API returns the reason
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestScenarioDetailFailureReason:
    """GET /simulate/scenarios/<uuid>/ returns failure_reason."""

    def _detail(self, auth_client, scenario):
        response = auth_client.get(f"/simulate/scenarios/{scenario.id}/")
        assert response.status_code == status.HTTP_200_OK
        return response.json()

    def test_a_failed_scenario_returns_its_reason(self, auth_client, prompt_scenario):
        prompt_scenario.status = StatusType.FAILED.value
        prompt_scenario.metadata = {FAILURE_REASON_KEY: NO_MODEL_PROVIDER}
        prompt_scenario.save()

        data = self._detail(auth_client, prompt_scenario)

        assert data["status"] == StatusType.FAILED.value
        assert data["failure_reason"] == NO_MODEL_PROVIDER

    def test_a_failed_scenario_without_a_reason_returns_null(
        self, auth_client, prompt_scenario
    ):
        prompt_scenario.status = StatusType.FAILED.value
        prompt_scenario.save()

        data = self._detail(auth_client, prompt_scenario)

        assert "failure_reason" in data
        assert data["failure_reason"] is None

    def test_a_scenario_that_did_not_fail_returns_null(
        self, auth_client, prompt_scenario
    ):
        prompt_scenario.status = StatusType.COMPLETED.value
        prompt_scenario.metadata = {FAILURE_REASON_KEY: UNEXPECTED_ERROR}
        prompt_scenario.save()

        data = self._detail(auth_client, prompt_scenario)

        assert data["failure_reason"] is None
