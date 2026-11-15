"""Bounded preview HTTP behavior with a fake native ClickHouse connection."""

from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.cache import cache
from django.utils import timezone

from tracer.services.derived_metrics import service, source_retention
from tracer.services.derived_metrics.queries import PREVIEW_QUERY_SETTINGS
from tracer.tests import test_derived_metric_clickhouse as metric_fixtures
from tracer.tests.test_derived_metric_spec import sentence

# Preview exercises the enabled API; unrelated tests keep the rollout default.
pytestmark = pytest.mark.usefixtures("enabled_metrics")

ROOT = "/tracer/derived-metrics/"


@pytest.fixture(autouse=True)
def clear_preview_cache(monkeypatch):
    # Compiler/transport fixtures use an explicit current 30-day source policy.
    # The maintained uncached policy adapter has separate real-PG regressions.
    monkeypatch.setattr(
        source_retention,
        "current_cutoff",
        lambda *args, **kwargs: timezone.now() - timedelta(days=30),
    )
    cache.clear()
    yield
    cache.clear()


class FakeClickHouse:
    server_enforced_readonly = False

    def __init__(self, *, error=None, on_query=None, on_acquire=None):
        self.calls = []
        self.error = error
        self.on_query = on_query
        self.on_acquire = on_acquire

    @contextmanager
    def connection(self):
        if self.on_acquire:
            callback, self.on_acquire = self.on_acquire, None
            callback()
        yield self

    def execute(self, query, params=None, **kwargs):
        self.calls.append((query, params, kwargs))
        if query.startswith("KILL QUERY"):
            return []
        if self.on_query:
            callback, self.on_query = self.on_query, None
            callback()
        if self.error:
            raise self.error
        row = {
            "group_key": "",
            "value": 0.5,
            "numerator": 1.0,
            "denominator": 2.0,
            "status": "ok",
            "unit_count": 2,
            "valid_count": 2,
            "missing_count": 0,
            "invalid_probability_count": 0,
            "ci_status": "not_estimable",
            "ci_reason": "unstable_variance",
            "ci_method": "iid_units",
            "ci_interval": "",
            "std_error": None,
            "ci_low": None,
            "ci_high": None,
            "effective_n": 2.0,
            "ci_df": 1,
        }
        return [tuple(row.values())], [(key, "test") for key in row]


def window(**overrides):
    end = timezone.now() - timedelta(seconds=1)
    return {
        "window_start": (end - timedelta(days=7)).isoformat(),
        "window_end": end.isoformat(),
        "request_id": str(uuid4()),
        **overrides,
    }


@pytest.fixture
def preview_url(auth_client, observe_project, enabled_metrics):
    response = auth_client.post(ROOT, sentence(observe_project.id), format="json")
    assert response.status_code == 201, response.data
    return f"{ROOT}{response.data['id']}/preview/"


def client_patch(client):
    return patch.object(service, "get_clickhouse_client", return_value=client)


@pytest.mark.django_db
def test_locale_preview_uses_refresh_compiler_full_membership_settle_and_budgets(
    auth_client, preview_url
):
    fake = FakeClickHouse()
    with client_patch(fake):
        response = auth_client.post(preview_url, window(), format="json")
    assert response.status_code == 200, response.data
    query, params, kwargs = fake.calls[0]
    assert kwargs["settings"] == PREVIEW_QUERY_SETTINGS
    assert kwargs["query_id"].startswith("metric-preview-")
    assert "s.start_time >=" not in query
    assert "unit_start >=" in query and "last_arrival_ns" in query
    assert params["preview_settle_minutes"] == 30
    assert "fr" in params.values() and "'fr'" not in query
    assert "inputs_text" in query and "field_condition" in query
    assert "scope_key" in query and "mixed_scope" in query
    assert response.data["results"][0]["value"] == 0.5
    assert response.data["unit_noun"] == "conversations"


@pytest.mark.django_db
@pytest.mark.parametrize("code", sorted(service.BUDGET_ERROR_CODES))
def test_budget_error_is_explicit_and_never_returns_a_partial_value(
    auth_client, preview_url, code
):
    error = RuntimeError("private query details")
    error.code = code
    fake = FakeClickHouse(error=error)
    with client_patch(fake):
        response = auth_client.post(preview_url, window(), format="json")
    assert response.status_code == 400
    assert "interactive read budget" in str(response.data)
    assert "results" not in response.data and "private query" not in str(response.data)


@pytest.mark.django_db
def test_backend_outage_returns_sanitized_503_and_releases_admission(
    auth_client, preview_url
):
    with client_patch(FakeClickHouse(error=RuntimeError("private backend"))):
        response = auth_client.post(preview_url, window(), format="json")
    assert response.status_code == 503
    assert "private backend" not in str(response.data)
    with client_patch(FakeClickHouse()):
        assert auth_client.post(preview_url, window(), format="json").status_code == 200


@pytest.mark.django_db
def test_replace_latest_cancels_the_old_query_without_actor_429(
    auth_client, preview_url
):
    nested = []
    fake = FakeClickHouse(
        on_acquire=lambda: nested.append(
            auth_client.post(preview_url, window(), format="json")
        )
    )
    with client_patch(fake):
        first = auth_client.post(preview_url, window(), format="json")
    assert first.status_code == 409 and first.data["code"] == "preview_superseded"
    assert nested[0].status_code == 200
    assert sum(query.startswith("KILL QUERY") for query, _, _ in fake.calls) == 1
    # Replacement while acquiring a connection kills the reserved old ID but
    # never submits the old aggregate after its admission has been revoked.
    assert fake.calls[0][0].startswith("KILL QUERY")
    assert len(fake.calls) == 2
    assert fake.calls[0][1]["preview_query_id"] != fake.calls[1][2]["query_id"]


@pytest.mark.django_db
def test_explicit_request_abort_does_not_return_a_stale_result(
    auth_client, preview_url
):
    data = window()
    fake = FakeClickHouse(
        on_acquire=lambda: auth_client.post(
            preview_url + "cancel/", {"request_id": data["request_id"]}, format="json"
        )
    )
    with client_patch(fake):
        response = auth_client.post(preview_url, data, format="json")
    assert response.status_code == 409
    assert len(fake.calls) == 1 and fake.calls[0][0].startswith("KILL QUERY")


@pytest.mark.django_db
def test_org_saturation_429_is_not_an_actor_throttle(auth_client, preview_url, user):
    from contextlib import ExitStack

    with ExitStack() as held:
        for index in range(service.PREVIEW_ORG_SLOTS):
            held.enter_context(
                service.preview_admission(f"other-{index}", user.organization_id)
            )
        fake = FakeClickHouse()
        with client_patch(fake):
            response = auth_client.post(preview_url, window(), format="json")
        assert response.status_code == 429 and response["Retry-After"]
        assert fake.calls == []


@pytest.mark.django_db
def test_user_preview_contract_is_unavailable_not_a_raw_span_user_aggregate(
    auth_client, observe_project
):
    response = auth_client.post(
        ROOT, sentence(observe_project.id, level="user", user_rule="any"), format="json"
    )
    assert response.status_code == 201, response.data
    fake = FakeClickHouse()
    with client_patch(fake):
        response = auth_client.post(
            f"{ROOT}{response.data['id']}/preview/", window(), format="json"
        )
    assert (
        response.status_code == 503
        and response.data["code"] == "user_preview_unavailable"
    )
    assert "results" not in response.data and fake.calls == []


@pytest.mark.django_db
def test_seven_day_scope_and_source_operator_errors_are_rejected_before_ch(
    auth_client, preview_url, observe_project
):
    fake = FakeClickHouse()
    with client_patch(fake):
        end = timezone.now() - timedelta(seconds=1)
        response = auth_client.post(
            preview_url,
            window(window_start=(end - timedelta(days=8)).isoformat()),
            format="json",
        )
        assert response.status_code == 400
        data = sentence(observe_project.id)
        data["field_spec"]["conditions"][0]["operator"] = "gt"
        assert auth_client.post(ROOT, data, format="json").status_code == 400
    assert fake.calls == []


@pytest.mark.parametrize("replace", [False, True])
def test_barrier_pool_waiter_never_executes_after_cancel_or_replacement(replace):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    actor, org, metric, request = uuid4(), uuid4(), uuid4(), uuid4()
    waiting, acquired = Event(), Event()
    submitted = []
    with service.preview_admission(
        actor, org, metric_id=metric, request_id=request
    ) as state:

        def old_worker():
            waiting.set()
            assert acquired.wait(3)
            with pytest.raises(service.PreviewSupersededError):
                with service._preview_phase(actor, org, state):
                    submitted.append(True)

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(old_worker)
            assert waiting.wait(3)
            if replace:
                with service.preview_admission(actor, org, metric_id=metric) as newer:
                    assert newer["token"] != state["token"]
            else:
                assert service.cancel_preview(actor, org, metric, request) == {
                    "cancelled": True
                }
            acquired.set()
            future.result(timeout=3)
    assert submitted == []


def test_cancel_keeps_execution_slot_until_registration_race_is_acknowledged():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    actor, org, metric, request = uuid4(), uuid4(), uuid4(), uuid4()
    phase_started, first_kill, finish, revoked = Event(), Event(), Event(), Event()
    kills = []
    with service.preview_admission(
        actor, org, metric_id=metric, request_id=request
    ) as state:
        state["query_id"] = "test-registration-race"
        cache.set(service._actor_key(actor, org), state, timeout=30)

        def worker():
            with service._preview_phase(actor, org, state):
                phase_started.set()
                assert first_kill.wait(3)
                # The first KILL raced ahead of native registration. Execution
                # can still begin, but cancellation must not acknowledge or free
                # this org slot until the worker's finally block runs.
                assert cache.get(state["slot"]) == state["token"]
                revoked.set()
                assert finish.wait(3)

        def kill(query_id):
            kills.append(query_id)
            first_kill.set()
            if len(kills) >= 2:
                finish.set()

        with (
            patch.object(service, "_cancel_preview_query", side_effect=kill),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            future = pool.submit(worker)
            assert phase_started.wait(3)
            assert service.cancel_preview(actor, org, metric, request) == {
                "cancelled": True
            }
            future.result(timeout=3)
        assert revoked.is_set() and len(kills) >= 2
        assert cache.get(state["slot"]) is None


def test_failed_cancellation_does_not_release_an_executing_slot():
    actor, org, metric, request = uuid4(), uuid4(), uuid4(), uuid4()
    with service.preview_admission(
        actor, org, metric_id=metric, request_id=request
    ) as state:
        state["query_id"] = "test-kill-failure"
        cache.set(service._actor_key(actor, org), state, timeout=30)
        with service._preview_phase(actor, org, state):
            with patch.object(
                service,
                "_cancel_preview_query",
                side_effect=RuntimeError("unavailable"),
            ):
                with pytest.raises(RuntimeError, match="unavailable"):
                    service.cancel_preview(actor, org, metric, request)
            assert cache.get(state["slot"]) == state["token"]
            assert cache.get(service._actor_key(actor, org))["cancelled"] is True


metric_ch = metric_fixtures.metric_ch


class NativePreviewAdapter:
    server_enforced_readonly = False

    def __init__(self, inner):
        self.inner = inner

    @contextmanager
    def connection(self):
        yield self.inner

    def execute(self, *args, **kwargs):
        return self.inner.execute(*args, **kwargs)


def seed_preview_source(
    client,
    scope,
    *,
    session=None,
    locale="fr",
    number=2,
    started=None,
    arrived=None,
    ended=True,
):
    now = timezone.now() - timedelta(hours=1)
    client.execute(
        """INSERT INTO spans (id, project_id, org_id, trace_id, trace_session_id, start_time,
        end_time, created_at, updated_at, attrs_string, attrs_number)
        VALUES (%(id)s, %(project)s, %(org)s, %(trace)s, %(session)s, %(started)s,
        %(ended)s, %(started)s, %(arrived)s, %(text)s, map('score', %(number)s))""",
        {
            "id": str(uuid4()),
            "project": str(scope.project),
            "org": str(scope.organization),
            "trace": str(uuid4()),
            "session": str(session or uuid4()),
            "started": started or now,
            "ended": now if ended else None,
            "arrived": arrived or now,
            "text": {} if locale is None else {"locale": locale},
            "number": number,
        },
    )


@pytest.mark.django_db
def test_live_preview_excludes_open_and_old_anchor_and_keeps_missing_as_missing(
    auth_client, preview_url, workspace, observe_project, metric_ch
):
    from tracer.services.derived_metrics.queries import Scope

    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    seed_preview_source(metric_ch, scope, locale="fr")
    seed_preview_source(metric_ch, scope, locale="en")
    seed_preview_source(metric_ch, scope, locale=None)
    seed_preview_source(metric_ch, scope, arrived=timezone.now())
    old = uuid4()
    seed_preview_source(
        metric_ch, scope, session=old, started=timezone.now() - timedelta(days=8)
    )
    seed_preview_source(metric_ch, scope, session=old)
    with client_patch(NativePreviewAdapter(metric_ch)):
        response = auth_client.post(preview_url, window(), format="json")
    assert response.status_code == 200, response.data
    row = response.data["results"][0]
    assert row["value"] == 0.5
    assert (row["unit_count"], row["valid_count"], row["missing_count"]) == (3, 2, 1)
    assert response.data["rate_eligible"] is True


@pytest.mark.django_db
@pytest.mark.parametrize("kind,expected", [("unique_count", 2), ("percentile", 2)])
def test_live_sketch_preview_matches_reduced_source(
    auth_client, observe_project, workspace, metric_ch, kind, expected
):
    from tracer.services.derived_metrics.queries import Scope

    data = sentence(
        observe_project.id,
        kind=kind,
        inputs=[
            {
                "alias": "score",
                "source": "span",
                "field": "attribute_number",
                "attribute_key": "score",
                "value_type": "number",
                "reducer": "latest",
            }
        ],
        field_spec={"percentile": 0.5} if kind == "percentile" else {},
    )
    response = auth_client.post(ROOT, data, format="json")
    assert response.status_code == 201, response.data
    url = f"{ROOT}{response.data['id']}/preview/"
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    for value in (2, 2, 6):
        seed_preview_source(metric_ch, scope, number=value)
    with client_patch(NativePreviewAdapter(metric_ch)):
        response = auth_client.post(url, window(), format="json")
    assert response.status_code == 200, response.data
    assert response.data["results"][0]["value"] == expected
    assert response.data["rate_eligible"] is False
    assert response.data["results"][0]["uncertainty"]["reason"] == "not_computed"


@pytest.mark.django_db
def test_live_span_preview_uses_end_time_not_conversation_settle_minutes(
    auth_client, observe_project, workspace, metric_ch
):
    from tracer.services.derived_metrics.queries import Scope

    response = auth_client.post(
        ROOT, sentence(observe_project.id, level="span"), format="json"
    )
    assert response.status_code == 201, response.data
    url = f"{ROOT}{response.data['id']}/preview/"
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    seed_preview_source(
        metric_ch, scope, locale="fr", arrived=timezone.now() - timedelta(minutes=1)
    )
    seed_preview_source(metric_ch, scope, locale="en")
    seed_preview_source(metric_ch, scope, locale="fr", ended=False)
    with client_patch(NativePreviewAdapter(metric_ch)):
        response = auth_client.post(url, window(), format="json")
    assert response.status_code == 200, response.data
    assert response.data["results"][0]["value"] == 0.5
    assert response.data["results"][0]["unit_count"] == 2


@pytest.mark.parametrize("abort", [False, True])
def test_evaluated_preview_has_one_deadline_and_always_drops_temporary_evidence(abort):
    from types import SimpleNamespace

    from tracer.services.derived_metrics.errors import MetricInputError
    from tracer.services.derived_metrics.spec import normalize_spec
    from tracer.services.derived_metrics.unit_reads import field_recipe

    recipe = field_recipe(SimpleNamespace(**normalize_spec(sentence())))
    fake = FakeClickHouse()
    clock = [0, 15.01] if abort else [0, 2]
    with patch("time.monotonic", side_effect=clock):
        if abort:
            with pytest.raises(MetricInputError, match="interactive read budget"):
                service._preview_evaluated(
                    fake, "SELECT evidence", recipe, {}, "test-query", lambda: None
                )
        else:
            service._preview_evaluated(
                fake, "SELECT evidence", recipe, {}, "test-query", lambda: None
            )
    assert fake.calls[0][0].startswith("CREATE TEMPORARY TABLE metric_preview_")
    assert fake.calls[0][2]["settings"]["max_execution_time"] == 15
    assert fake.calls[-1][0].startswith(
        "DROP TEMPORARY TABLE IF EXISTS metric_preview_"
    )
    if not abort:
        assert fake.calls[1][2]["settings"]["max_execution_time"] == 13
        assert (
            fake.calls[1][2]["settings"]["max_memory_usage"]
            == PREVIEW_QUERY_SETTINGS["max_memory_usage"]
        )
    else:
        assert len(fake.calls) == 2


def test_evaluated_preview_rechecks_request_abort_between_statements():
    from types import SimpleNamespace

    from tracer.services.derived_metrics.spec import normalize_spec
    from tracer.services.derived_metrics.unit_reads import field_recipe

    recipe = field_recipe(SimpleNamespace(**normalize_spec(sentence())))
    fake = FakeClickHouse()

    checks = []

    def cancelled():
        checks.append(True)
        if len(checks) == 2:
            raise service.PreviewSupersededError("superseded")

    with pytest.raises(service.PreviewSupersededError):
        service._preview_evaluated(
            fake, "SELECT evidence", recipe, {}, "test-query", cancelled
        )
    assert len(fake.calls) == 2
    assert fake.calls[-1][0].startswith("DROP TEMPORARY TABLE")


@pytest.mark.django_db
@pytest.mark.parametrize("level", ["conversation", "span"])
def test_live_unsaved_locale_preview_and_samples_do_not_save_or_queue(
    auth_client, observe_project, workspace, metric_ch, level
):
    from tracer.models.derived_metric import (
        DerivedMetric,
        DerivedMetricVersion,
        MetricProjectRefresh,
    )
    from tracer.services.derived_metrics.queries import Scope

    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    for locale in ("fr", "fr", "en", None):
        seed_preview_source(metric_ch, scope, locale=locale)
    before = (
        DerivedMetric.objects.count(),
        DerivedMetricVersion.objects.count(),
        MetricProjectRefresh.objects.count(),
    )
    definition = sentence(level=level)
    definition.pop("name")
    definition.pop("project_id")
    request = {
        **window(),
        "project_id": str(observe_project.id),
        "definition": definition,
    }
    with client_patch(NativePreviewAdapter(metric_ch)):
        first = auth_client.post(ROOT + "preview/", request, format="json")
        definition["field_spec"]["conditions"][0]["value"] = "en"
        second = auth_client.post(
            ROOT + "preview/", {**request, "request_id": str(uuid4())}, format="json"
        )
    assert first.status_code == second.status_code == 200, (first.data, second.data)
    assert first.data["results"][0]["value"] == pytest.approx(2 / 3)
    assert second.data["results"][0]["value"] == pytest.approx(1 / 3)
    samples = second.data["sample_units"]
    assert len(samples) == 4
    assert sum(row["value_bool"] is True for row in samples) == 1
    assert sum(row["value_label"] == "Doesn't count" for row in samples) == 2
    missing = next(row for row in samples if row["status"] == "no_value")
    assert (
        missing["value_bool"] is None
        and missing["no_value_reason"] == "attribute_absent"
    )
    assert missing["inputs_text"]["locale"] is None
    assert {row["inputs_text"]["locale"] for row in samples} == {"fr", "en", None}
    assert all(row["unit_id"] for row in samples)
    assert before == (
        DerivedMetric.objects.count(),
        DerivedMetricVersion.objects.count(),
        MetricProjectRefresh.objects.count(),
    )


@pytest.mark.django_db
def test_live_draft_preview_bounds_samples_at_twenty_without_raising_result_cap(
    auth_client, observe_project, workspace, metric_ch
):
    from tracer.services.derived_metrics.queries import Scope

    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    for _ in range(21):
        seed_preview_source(metric_ch, scope)
    definition = sentence()
    definition.pop("name")
    definition.pop("project_id")
    with client_patch(NativePreviewAdapter(metric_ch)):
        response = auth_client.post(
            ROOT + "preview/",
            {
                **window(),
                "project_id": str(observe_project.id),
                "definition": definition,
            },
            format="json",
        )
    assert response.status_code == 200, response.data
    assert response.data["results"][0]["unit_count"] == 21
    assert len(response.data["sample_units"]) == 20
    assert PREVIEW_QUERY_SETTINGS["max_result_rows"] == 10


@pytest.mark.django_db
def test_unsaved_preview_cancels_by_actor_project_and_request(
    auth_client, observe_project
):
    definition = sentence()
    definition.pop("name")
    definition.pop("project_id")
    data = {**window(), "project_id": str(observe_project.id), "definition": definition}
    cancelled = []
    fake = FakeClickHouse(
        on_acquire=lambda: cancelled.append(
            auth_client.post(
                ROOT + "preview/cancel/",
                {
                    "project_id": str(observe_project.id),
                    "request_id": data["request_id"],
                },
                format="json",
            )
        )
    )
    with client_patch(fake):
        response = auth_client.post(ROOT + "preview/", data, format="json")
    assert response.status_code == 409
    assert cancelled[0].status_code == 200 and cancelled[0].data == {"cancelled": True}
    assert len(fake.calls) == 1 and fake.calls[0][0].startswith("KILL QUERY")


@pytest.mark.django_db
def test_draft_preview_project_scope_validation_and_unavailable_users(
    auth_client, observe_project, user, workspace
):
    from accounts.models.workspace import Workspace

    from tracer.models.project import Project

    definition = sentence(level="user", user_rule="any")
    definition.pop("project_id")
    definition.pop("name")
    data = {**window(), "project_id": str(observe_project.id), "definition": definition}
    fake = FakeClickHouse()
    with client_patch(fake):
        response = auth_client.post(ROOT + "preview/", data, format="json")
        assert (
            response.status_code == 503
            and response.data["code"] == "user_preview_unavailable"
        )
        other_workspace = Workspace.objects.create(
            name="Other", organization=user.organization, created_by=user
        )
        other = Project.no_workspace_objects.create(
            name="Other",
            organization=user.organization,
            workspace=other_workspace,
            model_type=observe_project.model_type,
            trace_type="observe",
        )
        assert (
            auth_client.post(
                ROOT + "preview/", {**data, "project_id": str(other.id)}, format="json"
            ).status_code
            == 404
        )
        assert (
            auth_client.post(
                ROOT + "preview/cancel/",
                {"project_id": str(other.id), "request_id": str(uuid4())},
                format="json",
            ).status_code
            == 404
        )
    assert fake.calls == []


@pytest.mark.django_db
def test_live_cancel_terminates_the_registered_native_query(metric_ch):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from conftest import _ch_test_native_client

    actor, org, metric, request = uuid4(), uuid4(), uuid4(), uuid4()
    query_id = "metric-preview-native-cancel-" + uuid4().hex
    starting = Event()
    errors = []
    with service.preview_admission(
        actor, org, metric_id=metric, request_id=request
    ) as state:
        state["query_id"] = query_id
        cache.set(service._actor_key(actor, org), state, timeout=30)

        def worker():
            with _ch_test_native_client(
                database=metric_ch.connection.database
            ) as native:
                try:
                    with service._preview_phase(actor, org, state):
                        starting.set()
                        native.execute(
                            "SELECT sleep(0.1) FROM numbers(10000)",
                            query_id=query_id,
                            settings={
                                **PREVIEW_QUERY_SETTINGS,
                                "max_block_size": 1,
                                "max_result_rows": 0,
                            },
                        )
                except Exception as exc:
                    errors.append(exc)

        def cancel(query):
            metric_ch.execute(
                "KILL QUERY WHERE query_id = %(query)s SYNC",
                {"query": query},
                settings={"max_execution_time": 2},
            )

        with (
            ThreadPoolExecutor(max_workers=1) as pool,
            patch.object(service, "_cancel_preview_query", side_effect=cancel),
        ):
            future = pool.submit(worker)
            assert starting.wait(3)
            deadline = time.monotonic() + 3
            while not metric_ch.execute(
                "SELECT count() FROM system.processes WHERE query_id = %(query)s",
                {"query": query_id},
            )[0][0]:
                assert time.monotonic() < deadline
                time.sleep(0.01)
            assert service.cancel_preview(actor, org, metric, request) == {
                "cancelled": True
            }
            future.result(timeout=3)
    assert len(errors) == 1 and getattr(errors[0], "code", None) == 394, errors
    assert metric_ch.execute(
        "SELECT count() FROM system.processes WHERE query_id = %(query)s",
        {"query": query_id},
    ) == [(0,)]


@pytest.mark.django_db
@pytest.mark.parametrize("during_query", [False, True])
def test_preview_withholds_values_when_source_policy_expires(
    auth_client,
    preview_url,
    monkeypatch,
    during_query,
):
    cutoff = {"value": timezone.now() - timedelta(days=30 if during_query else 1)}
    monkeypatch.setattr(
        source_retention, "current_cutoff", lambda *args, **kwargs: cutoff["value"]
    )

    def shorten():
        cutoff["value"] = timezone.now() - timedelta(days=1)

    fake = FakeClickHouse(on_query=shorten if during_query else None)
    with client_patch(fake):
        response = auth_client.post(preview_url, window(), format="json")
    assert response.status_code == 503 and response.data["code"] == "retention_expired"
    assert "results" not in response.data and "sample_units" not in response.data
    assert bool(fake.calls) == during_query
