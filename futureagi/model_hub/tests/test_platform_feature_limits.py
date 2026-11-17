"""The smart filter, the criteria helper and the AI eval writer are limited per
organization and per user.

Each of the three platform-model features has its own limit, counted after
sign-in: one budget per organization and one per user. A request past either
gets the standard 429 envelope with ``Retry-After``; another organization, and
another member while the organization's budget lasts, still get through.
Requests that are not signed in keep their existing refusal.

The 429 says, in one sentence, which budget ran out (the user's or the
organization's) for which feature, and how many seconds to wait: the
Retry-After value. Each of the four limited requests declares its 429, and its
503 for when the limit store is down, in the API contract.

The criteria helper is reached by starting an optimization run (the
knowledge-base create route and the optimize-dataset create route). Both refuse
the run today before any model call, so a request within the limit gets that
refusal, never a 429.

No database and no model call: the views are called directly with stand-in
users, the model calls are replaced, and the limits count in the default cache
(locmem under the test settings). Every test uses fresh organization and user
ids, so no test shares a budget with another.
"""

import uuid
from types import SimpleNamespace
from unittest import mock

import pytest
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.omega_rate_window import Decision, RateLimitUnavailable, take
from model_hub.views import ai_eval_writer, ai_filter, optimize_dataset

FACTORY = APIRequestFactory()
SCHEMA = [
    {
        "field": "status",
        "label": "Status",
        "type": "enum",
        "category": "system",
        "operators": ["is", "is_not"],
        "choices": ["failed", "completed"],
    }
]
NOT_SIGNED_IN = {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}


def _organization():
    return SimpleNamespace(pk=uuid.uuid4())


def _member(organization):
    return SimpleNamespace(
        is_authenticated=True, pk=uuid.uuid4(), organization=organization
    )


def _as(request, organization, user):
    """``request`` as ``user`` of ``organization``; no user leaves it signed out."""
    request.workspace = None
    if user is not None:
        request.organization = organization
        force_authenticate(request, user=user)
    return request


def _request(method, path, body):
    if method == "post":
        return FACTORY.post(path, body, format="json")
    return getattr(FACTORY, method)(path)


def _smart_filter(organization, user, method="post"):
    body = {"mode": "select_fields", "query": "show failed calls", "schema": SCHEMA}
    request = _request(method, "/model-hub/ai-filter/", body)
    with (
        mock.patch("model_hub.services.model_route.platform_llm"),
        mock.patch.object(
            ai_filter,
            "_bounded_completion_content",
            return_value='{"fields": ["status"]}',
        ),
    ):
        return ai_filter.AIFilterView.as_view()(_as(request, organization, user))


def _ai_eval_writer(organization, user, method="post"):
    body = {"description": "Check the answer is polite", "output_format": "prompt"}
    request = _request(method, "/model-hub/ai-eval-writer/", body)
    with mock.patch.object(
        ai_eval_writer,
        "generate_eval_prompt",
        return_value={"prompt": "Is the answer polite?"},
    ):
        return ai_eval_writer.AIEvalWriterView.as_view()(
            _as(request, organization, user)
        )


def _criteria_helper(organization, user, method="post"):
    body = {
        "name": "Criteria run",
        "knowledge_base_metrics": ["The answer is polite."],
    }
    request = _request(method, "/model-hub/optimize-dataset/knowledge-base/", body)
    return optimize_dataset.OptimizedDatasetKbView.as_view()(
        _as(request, organization, user)
    )


def _optimize_dataset_run(organization, user):
    # The payload's model differs from the URL's, so the view answers 400
    # before it reads anything.
    model_id = uuid.uuid4()
    body = {
        "name": "Criteria run",
        "start_date": "2026-11-01",
        "end_date": "2026-11-02",
        "model": str(uuid.uuid4()),
        "optimize_type": "PromptTemplate",
        "environment": "Production",
        "version": "v1",
        "metrics": [str(uuid.uuid4())],
    }
    request = FACTORY.post(
        f"/model-hub/optimize-dataset/{model_id}/", body, format="json"
    )
    return optimize_dataset.OptimizedDatasetView.as_view()(
        _as(request, organization, user), model_id=model_id
    )


# feature -> (send one request, its two settings, the answer within the limit)
FEATURES = {
    "smart_filter": (
        _smart_filter,
        ("SMART_FILTER_ORG_THROTTLE_RATE", "SMART_FILTER_USER_THROTTLE_RATE"),
        status.HTTP_200_OK,
    ),
    "criteria_helper": (
        _criteria_helper,
        ("CRITERIA_HELPER_ORG_THROTTLE_RATE", "CRITERIA_HELPER_USER_THROTTLE_RATE"),
        status.HTTP_400_BAD_REQUEST,
    ),
    "ai_eval_writer": (
        _ai_eval_writer,
        ("AI_EVAL_WRITER_ORG_THROTTLE_RATE", "AI_EVAL_WRITER_USER_THROTTLE_RATE"),
        status.HTTP_200_OK,
    ),
}


def _rates(feature, *, organization, user):
    org_setting, user_setting = FEATURES[feature][1]
    return override_settings(**{org_setting: organization, user_setting: user})


def _assert_limited(response):
    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert 1 <= int(response["Retry-After"]) <= 60
    assert response.data["status"] is False
    assert response.data["code"] == "throttled"


@pytest.mark.parametrize("feature", sorted(FEATURES))
def test_a_feature_is_limited_per_organization_and_not_for_another(feature):
    send, _, allowed = FEATURES[feature]
    organization = _organization()

    with _rates(feature, organization="3/min", user="100/min"):
        for _ in range(3):
            assert send(organization, _member(organization)).status_code == allowed
        _assert_limited(send(organization, _member(organization)))

        other = _organization()
        assert send(other, _member(other)).status_code == allowed


@pytest.mark.parametrize("feature", sorted(FEATURES))
def test_a_member_is_limited_apart_from_the_organization(feature):
    send, _, allowed = FEATURES[feature]
    organization = _organization()
    first, second = _member(organization), _member(organization)

    with _rates(feature, organization="100/min", user="2/min"):
        for _ in range(2):
            assert send(organization, first).status_code == allowed
        _assert_limited(send(organization, first))

        assert send(organization, second).status_code == allowed


def test_a_refused_request_does_not_spend_the_organizations_budget():
    organization = _organization()
    first = _member(organization)

    with _rates("ai_eval_writer", organization="3/min", user="1/min"):
        assert _ai_eval_writer(organization, first).status_code == status.HTTP_200_OK
        for _ in range(3):
            _assert_limited(_ai_eval_writer(organization, first))

        for _ in range(2):
            member = _member(organization)
            assert (
                _ai_eval_writer(organization, member).status_code == status.HTTP_200_OK
            )
        _assert_limited(_ai_eval_writer(organization, _member(organization)))


def test_each_feature_has_its_own_budget():
    organization = _organization()
    user = _member(organization)
    every_rate = {name: "1/min" for _, names, _ in FEATURES.values() for name in names}

    with override_settings(**every_rate):
        assert _smart_filter(organization, user).status_code == status.HTTP_200_OK
        _assert_limited(_smart_filter(organization, user))

        assert _ai_eval_writer(organization, user).status_code == status.HTTP_200_OK
        assert (
            _criteria_helper(organization, user).status_code
            == status.HTTP_400_BAD_REQUEST
        )


def test_both_ways_to_start_the_criteria_helper_share_one_budget():
    organization = _organization()
    user = _member(organization)

    with _rates("criteria_helper", organization="100/min", user="2/min"):
        assert (
            _criteria_helper(organization, user).status_code
            == status.HTTP_400_BAD_REQUEST
        )
        assert (
            _optimize_dataset_run(organization, user).status_code
            == status.HTTP_400_BAD_REQUEST
        )
        _assert_limited(_optimize_dataset_run(organization, user))
        _assert_limited(_criteria_helper(organization, user))


def test_only_starting_a_run_counts_against_the_criteria_helper():
    from model_hub.throttles import CriteriaHelperThrottle

    view = optimize_dataset.OptimizedDatasetView()
    counted = {}
    for method in ("GET", "POST"):
        view.request = SimpleNamespace(method=method)
        counted[method] = any(
            isinstance(throttle, CriteriaHelperThrottle)
            for throttle in view.get_throttles()
        )

    assert counted == {"GET": False, "POST": True}


@pytest.mark.parametrize("feature", sorted(FEATURES))
def test_a_method_the_feature_does_not_serve_does_not_count(feature):
    send, _, allowed = FEATURES[feature]
    organization = _organization()
    user = _member(organization)

    with _rates(feature, organization="1/min", user="1/min"):
        for _ in range(2):
            assert (
                send(organization, user, method="get").status_code
                == status.HTTP_405_METHOD_NOT_ALLOWED
            )
        assert send(organization, user).status_code == allowed


@pytest.mark.django_db
@pytest.mark.parametrize("feature", sorted(FEATURES))
def test_a_request_that_is_not_signed_in_keeps_its_refusal(feature):
    send, _, _ = FEATURES[feature]

    with _rates(feature, organization="1/min", user="1/min"):
        for _ in range(3):
            refused = send(None, None)
            assert refused.status_code in NOT_SIGNED_IN
            assert refused.data["code"] == "not_authenticated"


# (feature, budget) -> the sentence a refusal shows for a wait of 2 seconds or
# more; {} is the Retry-After wait.
SENTENCES = {
    ("ai_eval_writer", "organization"): (
        "Your organization has used the AI eval writer a lot in the last minute. "
        "Try again in {} seconds."
    ),
    ("ai_eval_writer", "user"): (
        "You've used the AI eval writer a lot in the last minute. "
        "Try again in {} seconds."
    ),
    ("criteria_helper", "organization"): (
        "Your organization has started a lot of Optimize runs in the last minute. "
        "Try again in {} seconds."
    ),
    ("criteria_helper", "user"): (
        "You've started a lot of Optimize runs in the last minute. "
        "Try again in {} seconds."
    ),
    ("smart_filter", "organization"): (
        "Your organization has used the smart filter a lot in the last minute. "
        "Try again in {} seconds."
    ),
    ("smart_filter", "user"): (
        "You've used the smart filter a lot in the last minute. "
        "Try again in {} seconds."
    ),
}
# Rates under which the named budget runs out after one request.
ONE_REQUEST_LEFT_IN = {
    "organization": {"organization": "1/min", "user": "100/min"},
    "user": {"organization": "100/min", "user": "1/min"},
}


@pytest.mark.parametrize("feature,budget", sorted(SENTENCES))
def test_a_refusal_says_which_budget_ran_out_and_when_to_try_again(feature, budget):
    send, _, allowed = FEATURES[feature]
    organization = _organization()
    first, second = _member(organization), _member(organization)

    with _rates(feature, **ONE_REQUEST_LEFT_IN[budget]):
        assert send(organization, first).status_code == allowed
        # Another member reaches only the organization's budget.
        refused = send(organization, second if budget == "organization" else first)

    _assert_limited(refused)
    sentence = SENTENCES[(feature, budget)].format(int(refused["Retry-After"]))
    assert refused.data["message"] == sentence
    assert refused.data["detail"] == sentence
    assert refused.data["type"] == "rate_limit"

    from tfc.utils.error_codes import PLATFORM_LIMIT_CONTRACT

    row = PLATFORM_LIMIT_CONTRACT[(refused.status_code, refused.data["code"])]
    assert row["type"] == refused.data["type"]


# How a refusal's sentence ends, by the wait in seconds: one second reads
# "1 second", and every other wait reads "<N> seconds".
WAIT_ENDINGS = {1: "Try again in 1 second.", 2: "Try again in 2 seconds."}
WAIT_CASES = [
    pytest.param(feature, budget, wait, id=f"{feature}-{budget}-{wait}s")
    for feature, budget in sorted(SENTENCES)
    for wait in sorted(WAIT_ENDINGS)
]


@pytest.mark.parametrize("feature,budget,wait", WAIT_CASES)
def test_the_wait_reads_one_second_or_n_seconds(feature, budget, wait):
    send, _, allowed = FEATURES[feature]
    organization = _organization()
    first, second = _member(organization), _member(organization)

    def take_with_the_wait(windows):
        # The real decision; a refusal's wait is pinned to ``wait`` seconds.
        decision = take(windows)
        if decision.allowed:
            return decision
        return Decision(False, wait_seconds=wait, refused_by=decision.refused_by)

    with (
        _rates(feature, **ONE_REQUEST_LEFT_IN[budget]),
        mock.patch("accounts.omega_rate_window.take", take_with_the_wait),
    ):
        assert send(organization, first).status_code == allowed
        # Another member reaches only the organization's budget.
        refused = send(organization, second if budget == "organization" else first)

    _assert_limited(refused)
    assert refused["Retry-After"] == str(wait)
    opening = SENTENCES[(feature, budget)].removesuffix("Try again in {} seconds.")
    assert refused.data["message"] == opening + WAIT_ENDINGS[wait]
    assert refused.data["detail"] == opening + WAIT_ENDINGS[wait]


@pytest.mark.parametrize("feature", sorted(FEATURES))
def test_a_limit_store_outage_refuses_the_request_and_says_so(feature):
    send, _, _ = FEATURES[feature]
    organization = _organization()

    with mock.patch(
        "accounts.omega_rate_window.take", side_effect=RateLimitUnavailable()
    ):
        refused = send(organization, _member(organization))

    assert refused.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert refused["Retry-After"] == "2"
    assert refused.data["message"] == (
        "Rate limiting is temporarily unavailable. Try again shortly."
    )


LIMITED_VIEWS = [
    ai_filter.AIFilterView,
    ai_eval_writer.AIEvalWriterView,
    optimize_dataset.OptimizedDatasetView,
    optimize_dataset.OptimizedDatasetKbView,
]


@pytest.mark.parametrize("view", LIMITED_VIEWS, ids=lambda view: view.__name__)
def test_the_limited_request_declares_its_limit_answers(view):
    # 429 past a budget; 503 while the limit store is down.
    declared = view.post._swagger_auto_schema["responses"]

    assert {
        status.HTTP_429_TOO_MANY_REQUESTS,
        status.HTTP_503_SERVICE_UNAVAILABLE,
    } <= set(declared)
