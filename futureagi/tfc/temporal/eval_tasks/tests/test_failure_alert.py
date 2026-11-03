"""Internal Slack alert fired when a continuous eval task flips to ``failed``."""

import asyncio
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from asgiref.sync import sync_to_async
from django.core.cache import cache
from django.db import close_old_connections
from django.utils import timezone
from temporalio.api.common.v1 import ActivityType
from temporalio.api.failure.v1 import ApplicationFailureInfo, Failure
from temporalio.api.history.v1 import (
    ActivityTaskCompletedEventAttributes,
    ActivityTaskFailedEventAttributes,
    ActivityTaskScheduledEventAttributes,
    ActivityTaskTimedOutEventAttributes,
    HistoryEvent,
)

from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
from tracer.models.observation_span import EvalEntryStatus


def _scheduled(event_id, name):
    return HistoryEvent(
        event_id=event_id,
        activity_task_scheduled_event_attributes=ActivityTaskScheduledEventAttributes(
            activity_type=ActivityType(name=name)
        ),
    )


def _failed(event_id, scheduled_id, failure):
    return HistoryEvent(
        event_id=event_id,
        activity_task_failed_event_attributes=ActivityTaskFailedEventAttributes(
            scheduled_event_id=scheduled_id, failure=failure
        ),
    )


def _completed(event_id, scheduled_id):
    return HistoryEvent(
        event_id=event_id,
        activity_task_completed_event_attributes=ActivityTaskCompletedEventAttributes(
            scheduled_event_id=scheduled_id
        ),
    )


def _app_failure(message, error_type, cause=None):
    failure = Failure(
        message=message,
        application_failure_info=ApplicationFailureInfo(type=error_type),
    )
    if cause is not None:
        failure.cause.CopyFrom(cause)
    return failure


def _patch_history(monkeypatch, events):
    import tfc.temporal.common.client as client_module

    class FakeHandle:
        async def fetch_history(self):
            return SimpleNamespace(events=events)

    class FakeClient:
        def get_workflow_handle(self, workflow_id, run_id=None):
            return FakeHandle()

    async def get_client():
        return FakeClient()

    monkeypatch.setattr(client_module, "get_client", get_client)


async def _reason(monkeypatch, events):
    from tracer.services.eval_tasks.failure_alert import fetch_failure_reason

    _patch_history(monkeypatch, events)
    reason = await fetch_failure_reason("eval-task-x", "run-1")
    return reason.text if reason else None


class TestFetchFailureReason:
    async def test_last_failed_activity_with_type_and_message(self, monkeypatch):
        reason = await _reason(
            monkeypatch,
            [
                _scheduled(5, "claim_eval_batch_activity"),
                _scheduled(9, "reconcile_eval_task_activity"),
                _failed(
                    11,
                    9,
                    _app_failure("row overflow: 2M rows", "EvalTaskSelectionRejected"),
                ),
            ],
        )
        assert reason == (
            "reconcile_eval_task_activity · EvalTaskSelectionRejected · "
            "row overflow: 2M rows"
        )

    async def test_outermost_application_failure_keeps_its_type(self, monkeypatch):
        # ``raise ContinuousCandidateReadError(...) from exc``: the alert names
        # the activity's own error, not the chained driver error below it.
        failure = _app_failure(
            "candidate read failed",
            "ContinuousCandidateReadError",
            cause=_app_failure("Code: 241. Memory limit", "ServerException"),
        )
        reason = await _reason(
            monkeypatch,
            [_scheduled(3, "reconcile_eval_task_activity"), _failed(4, 3, failure)],
        )
        assert reason == (
            "reconcile_eval_task_activity · ContinuousCandidateReadError · "
            "candidate read failed"
        )

    async def test_wrapper_without_type_falls_through_to_cause(self, monkeypatch):
        wrapped = Failure(
            message="activity error",
            cause=_app_failure("connection refused", "OperationalError"),
        )
        reason = await _reason(
            monkeypatch,
            [_scheduled(3, "claim_eval_batch_activity"), _failed(4, 3, wrapped)],
        )
        assert reason == (
            "claim_eval_batch_activity · OperationalError · connection refused"
        )

    async def test_timed_out_activity(self, monkeypatch):
        timed_out = HistoryEvent(
            event_id=4,
            activity_task_timed_out_event_attributes=ActivityTaskTimedOutEventAttributes(
                scheduled_event_id=3,
                failure=Failure(message="activity StartToClose timeout"),
            ),
        )
        reason = await _reason(
            monkeypatch, [_scheduled(3, "reap_eval_entries_activity"), timed_out]
        )
        assert reason == "reap_eval_entries_activity · activity StartToClose timeout"

    async def test_handled_entry_failures_are_ignored(self, monkeypatch):
        # Entry evals fail all the time and the workflow survives them; when
        # the run then dies in workflow code there is no activity to blame.
        reason = await _reason(
            monkeypatch,
            [
                _scheduled(3, "run_eval_entry_activity"),
                _failed(4, 3, _app_failure("LLM 429", "RateLimitError")),
                _scheduled(5, "fail_eval_entry_activity"),
                _failed(6, 5, _app_failure("db blip", "OperationalError")),
            ],
        )
        assert reason is None

    async def test_failure_cleared_by_later_success_of_same_activity(self, monkeypatch):
        # A deferred reconcile (read budget) that succeeded on the next poll.
        reason = await _reason(
            monkeypatch,
            [
                _scheduled(3, "reconcile_eval_task_activity"),
                _failed(4, 3, _app_failure("budget", "EvalTaskReadBudgetExceeded")),
                _scheduled(5, "reconcile_eval_task_activity"),
                _completed(6, 5),
            ],
        )
        assert reason is None

    async def test_unrelated_success_does_not_clear_the_failure(self, monkeypatch):
        reason = await _reason(
            monkeypatch,
            [
                _scheduled(3, "reconcile_eval_task_activity"),
                _scheduled(4, "run_eval_entry_activity"),
                _failed(5, 3, _app_failure("rejected", "EvalTaskSelectionRejected")),
                _completed(6, 4),
            ],
        )
        assert reason == (
            "reconcile_eval_task_activity · EvalTaskSelectionRejected · rejected"
        )

    async def test_no_failed_activity_returns_none(self, monkeypatch):
        assert (
            await _reason(monkeypatch, [_scheduled(3, "claim_eval_batch_activity")])
            is None
        )

    async def test_reason_is_truncated(self, monkeypatch):
        reason = await _reason(
            monkeypatch,
            [
                _scheduled(3, "reconcile_eval_task_activity"),
                _failed(4, 3, _app_failure("x" * 5000, "Boom")),
            ],
        )
        assert len(reason) == 1500


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestFailedContinuousFlag:
    def _set(self, task, status, expected_status=None):
        from tfc.temporal.eval_tasks.activities import _set_task_status_sync

        return _set_task_status_sync(str(task.id), status, expected_status)

    def _make(self, task, *, run_type, status):
        EvalTask.objects.filter(id=task.id).update(run_type=run_type, status=status)

    def test_continuous_to_failed_alerts(self, eval_task):
        self._make(
            eval_task, run_type=RunType.CONTINUOUS, status=EvalTaskStatus.RUNNING
        )
        assert self._set(eval_task, EvalTaskStatus.FAILED)["failed_continuous"]

    def test_historical_failure_does_not_alert(self, eval_task):
        self._make(
            eval_task, run_type=RunType.HISTORICAL, status=EvalTaskStatus.RUNNING
        )
        assert not self._set(eval_task, EvalTaskStatus.FAILED)["failed_continuous"]

    @pytest.mark.parametrize(
        "status", [EvalTaskStatus.PAUSED, EvalTaskStatus.COMPLETED]
    )
    def test_non_failed_transition_does_not_alert(self, eval_task, status):
        self._make(
            eval_task, run_type=RunType.CONTINUOUS, status=EvalTaskStatus.RUNNING
        )
        assert not self._set(eval_task, status)["failed_continuous"]

    def test_guarded_noop_does_not_alert(self, eval_task):
        self._make(eval_task, run_type=RunType.CONTINUOUS, status=EvalTaskStatus.PAUSED)
        out = self._set(
            eval_task, EvalTaskStatus.FAILED, expected_status=EvalTaskStatus.RUNNING
        )
        assert out["changed"] is False
        assert not out["failed_continuous"]

    @pytest.mark.parametrize("status", [EvalTaskStatus.RUNNING, EvalTaskStatus.FAILED])
    def test_status_write_adds_no_query(
        self, eval_task, django_assert_num_queries, status
    ):
        # update + reload, exactly as before the alert hook existed.
        self._make(
            eval_task, run_type=RunType.CONTINUOUS, status=EvalTaskStatus.PENDING
        )
        with django_assert_num_queries(2):
            self._set(eval_task, status)


class _NoopHeartbeater:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


def _inline_sync_to_async(function, **_kwargs):
    async def invoke(*args, **kwargs):
        return function(*args, **kwargs)

    return invoke


class TestStatusActivityHook:
    @pytest.mark.parametrize("failed_continuous", [True, False])
    async def test_notifies_only_for_failed_continuous(
        self, monkeypatch, failed_continuous
    ):
        import tfc.temporal.eval_tasks.activities as activities
        from tfc.temporal.eval_tasks.types import SetStatusInput, SetStatusOutput

        calls = []

        async def fake_notify(*args):
            calls.append(args)

        monkeypatch.setattr(activities, "Heartbeater", _NoopHeartbeater)
        monkeypatch.setattr(activities, "otel_sync_to_async", _inline_sync_to_async)
        monkeypatch.setattr(
            activities,
            "_set_task_status_sync",
            lambda *_a: {
                "task_id": "t1",
                "changed": True,
                "status": "failed",
                "failed_continuous": failed_continuous,
            },
        )
        monkeypatch.setattr(
            activities.activity,
            "info",
            lambda: SimpleNamespace(workflow_id="eval-task-t1", workflow_run_id="r1"),
        )
        monkeypatch.setattr(activities, "notify_eval_task_failed", fake_notify)

        out = await activities.set_eval_task_status_activity(
            SetStatusInput(task_id="t1", status="failed")
        )

        assert out == SetStatusOutput(task_id="t1", changed=True, status="failed")
        assert calls == ([("t1", "eval-task-t1", "r1")] if failed_continuous else [])


class TestNotify:
    async def test_webhook_unset_skips_everything(self, monkeypatch, settings):
        import tracer.services.eval_tasks.failure_alert as fa

        settings.ERROR_LOGS_WEBHOOK = ""

        async def boom(*_a):
            raise AssertionError("history must not be fetched")

        monkeypatch.setattr(fa, "fetch_failure_reason", boom)

        await fa.notify_eval_task_failed("t1", "eval-task-t1", "r1")

    @pytest.mark.parametrize("failure", ["raises", "hangs"])
    async def test_history_unavailable_still_posts_without_reason(
        self, monkeypatch, settings, failure
    ):
        import tracer.services.eval_tasks.failure_alert as fa

        settings.ERROR_LOGS_WEBHOOK = "https://hooks.slack.test/x"
        posted = []

        async def unavailable(*_a):
            if failure == "raises":
                raise RuntimeError("temporal down")
            await asyncio.sleep(60)

        monkeypatch.setattr(fa, "fetch_failure_reason", unavailable)
        monkeypatch.setattr(fa, "_REASON_TIMEOUT_SECONDS", 0.1)
        monkeypatch.setattr(fa, "otel_sync_to_async", _inline_sync_to_async)
        monkeypatch.setattr(fa, "_post_failure_alert", lambda *a: posted.append(a))

        await fa.notify_eval_task_failed("t1", "eval-task-t1", "r1")

        assert posted == [("t1", "eval-task-t1", None)]

    async def test_post_failure_is_swallowed(self, monkeypatch, settings):
        import tracer.services.eval_tasks.failure_alert as fa

        settings.ERROR_LOGS_WEBHOOK = "https://hooks.slack.test/x"

        async def reason(*_a):
            return None

        def post(*_a):
            raise RuntimeError("db down")

        monkeypatch.setattr(fa, "fetch_failure_reason", reason)
        monkeypatch.setattr(fa, "_post_failure_alert", post)

        # Real asgiref thread hop, as in production.
        await fa.notify_eval_task_failed("t1", "eval-task-t1", "r1")


def _blocks_text(blocks):
    return "\n".join(
        b["text"]["text"] if "text" in b else b["elements"][0]["text"] for b in blocks
    )


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestPostAndDedupe:
    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        cache.clear()
        yield
        cache.clear()

    def _failed_task(self, eval_task):
        EvalTask.objects.filter(id=eval_task.id).update(
            run_type=RunType.CONTINUOUS,
            status=EvalTaskStatus.FAILED,
            end_time=timezone.now(),
        )

    def _capture(self, monkeypatch, ok=True):
        import tracer.services.eval_tasks.failure_alert as fa

        posts = []

        def send(text, blocks=None, timeout=30):
            posts.append({"text": text, "blocks": blocks, "timeout": timeout})
            return ok

        monkeypatch.setattr(fa, "send_critical_slack_notification", send)
        return posts

    def _reason(self, error_type="EvalTaskSelectionRejected", message="overflow"):
        from tracer.services.eval_tasks.failure_alert import FailureReason

        return FailureReason("reconcile_eval_task_activity", error_type, message)

    def test_retry_of_same_failure_posts_once(self, eval_task, monkeypatch):
        from tracer.services.eval_tasks.failure_alert import _post_failure_alert

        self._failed_task(eval_task)
        posts = self._capture(monkeypatch)

        _post_failure_alert(str(eval_task.id), "wf", self._reason(message="a"))
        # Retried status activity / sweeper restart: same failure, new details.
        _post_failure_alert(str(eval_task.id), "wf", self._reason(message="b"))

        assert len(posts) == 1
        assert posts[0]["timeout"] == 10

    def test_different_failure_posts_again(self, eval_task, monkeypatch):
        from tracer.services.eval_tasks.failure_alert import _post_failure_alert

        self._failed_task(eval_task)
        posts = self._capture(monkeypatch)

        _post_failure_alert(str(eval_task.id), "wf", self._reason())
        _post_failure_alert(
            str(eval_task.id), "wf", self._reason(error_type="OperationalError")
        )

        assert len(posts) == 2

    def test_failed_send_is_not_marked_so_retry_alerts(self, eval_task, monkeypatch):
        from tracer.services.eval_tasks.failure_alert import _post_failure_alert

        self._failed_task(eval_task)
        posts = self._capture(monkeypatch, ok=False)
        _post_failure_alert(str(eval_task.id), "wf", self._reason())
        _post_failure_alert(str(eval_task.id), "wf", self._reason())

        assert len(posts) == 2

    def test_cache_outage_fails_open(self, eval_task, monkeypatch):
        import tracer.services.eval_tasks.failure_alert as fa

        class DownCache:
            def get(self, *_a):
                raise ConnectionError("redis down")

            def set(self, *_a):
                raise ConnectionError("redis down")

        self._failed_task(eval_task)
        posts = self._capture(monkeypatch)
        monkeypatch.setattr(fa, "cache", DownCache())

        fa._post_failure_alert(str(eval_task.id), "wf", self._reason())

        assert len(posts) == 1


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestBuildFailureAlert:
    def _failed_task(self, eval_task, **fields):
        EvalTask.objects.filter(id=eval_task.id).update(
            run_type=RunType.CONTINUOUS,
            status=EvalTaskStatus.FAILED,
            end_time=timezone.now(),
            **fields,
        )
        return EvalTask.objects.select_related(
            "project__organization", "project__workspace", "project__user"
        ).get(id=eval_task.id)

    def test_message_names_customer_reason_and_impact(
        self, eval_task, user, settings, make_pending_entries
    ):
        from tracer.services.eval_tasks.failure_alert import (
            FailureReason,
            build_failure_alert,
        )

        settings.APP_BASE_URL = "https://app.futureagi.com"
        make_pending_entries(eval_task, 2)
        make_pending_entries(eval_task, 1, status=EvalEntryStatus.COMPLETED)
        task = self._failed_task(eval_task)

        text, blocks = build_failure_alert(
            task,
            f"eval-task-{task.id}",
            FailureReason(
                "reconcile_eval_task_activity", "EvalTaskSelectionRejected", "overflow"
            ),
        )
        body = _blocks_text(blocks)

        org = task.project.organization
        assert org.name in text and task.name in text
        assert f"*Org:* `{org.id}`" in body
        assert user.email in body
        assert "tier *" in body
        assert task.project.name in body
        assert (
            f"<https://app.futureagi.com/dashboard/tasks/{task.id}|{task.name}>" in body
        )
        assert "EvalTaskSelectionRejected · overflow" in body
        assert "*Entries stranded pending/running:* 2" in body
        assert "*Entries created in last 24h:* 3" in body
        assert "WF Eval" in body
        assert f"temporal.workflow_id:eval-task-{task.id}" in body

    def test_customer_controlled_text_is_escaped(self, eval_task, settings):
        from tracer.services.eval_tasks.failure_alert import (
            FailureReason,
            build_failure_alert,
        )

        settings.APP_BASE_URL = "https://app.futureagi.com"
        task = self._failed_task(eval_task, name="<!channel> a|b > c & d")

        text, blocks = build_failure_alert(
            task,
            "wf",
            FailureReason("reconcile", "Boom", "bad ``` fence <@U123>"),
        )
        body = _blocks_text(blocks)

        assert "<!channel>" not in body and "<!channel>" not in text
        assert "<@U123>" not in body
        assert "&lt;!channel&gt; a|b &gt; c &amp; d" in body
        # The only fences left are the code block's own two.
        assert body.count("```") == 2

    def test_falls_back_without_reason_owner_or_app_url(self, eval_task, settings):
        from accounts.models.organization_membership import OrganizationMembership
        from tracer.services.eval_tasks.failure_alert import build_failure_alert

        settings.APP_BASE_URL = ""
        task = self._failed_task(eval_task)
        OrganizationMembership.no_workspace_objects.filter(
            organization=task.project.organization
        ).update(is_active=False)

        _, blocks = build_failure_alert(task, "eval-task-x", None)
        body = _blocks_text(blocks)

        assert "no owner found" in body
        assert f"*Task:* *{task.name}*" in body
        assert "the workflow itself raised" in body

    def test_failing_enrichment_does_not_drop_the_alert(self, eval_task, monkeypatch):
        import tracer.services.eval_tasks.failure_alert as fa

        def boom(*_a):
            raise RuntimeError("ee app not installed")

        monkeypatch.setattr(fa, "_tier", boom)
        monkeypatch.setattr(fa, "_owner_email", boom)
        task = self._failed_task(eval_task)

        _, blocks = fa.build_failure_alert(task, "wf", None)
        body = _blocks_text(blocks)

        assert "tier *unknown*" in body
        assert "no owner found" in body


class TestCriticalSlackBlocks:
    def test_blocks_and_timeout_reach_the_webhook(self, monkeypatch, settings):
        import tfc.utils.slack as slack

        sent = {}

        class FakeWebhook:
            def __init__(self, url, timeout=30):
                sent["url"] = url
                sent["timeout"] = timeout

            def send(self, **kwargs):
                sent.update(kwargs)
                return SimpleNamespace(status_code=200)

        monkeypatch.setenv("ENV_TYPE", "prod")
        settings.ERROR_LOGS_WEBHOOK = "https://hooks.slack.test/x"
        monkeypatch.setattr(slack, "WebhookClient", FakeWebhook)

        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "hi"}}]
        assert slack.send_critical_slack_notification(
            "fallback", blocks=blocks, timeout=10
        )

        assert sent["timeout"] == 10
        assert sent["text"].startswith("fallback")
        assert sent["blocks"][0] == blocks[0]
        assert sent["blocks"][-1]["elements"][0]["text"] == "*Environment:* prod"
        assert len(blocks) == 1  # caller's list is not mutated

    def test_existing_callers_keep_text_only_and_default_timeout(
        self, monkeypatch, settings
    ):
        import tfc.utils.slack as slack

        sent = {}

        class FakeWebhook:
            def __init__(self, url, timeout=None):
                sent["timeout"] = timeout

            def send(self, **kwargs):
                sent.update(kwargs)
                return SimpleNamespace(status_code=200)

        monkeypatch.setenv("ENV_TYPE", "prod")
        settings.ERROR_LOGS_WEBHOOK = "https://hooks.slack.test/x"
        monkeypatch.setattr(slack, "WebhookClient", FakeWebhook)

        assert slack.send_critical_slack_notification("plain")
        assert sent["timeout"] == 30
        assert sent["blocks"] is None


async def _fail_continuous_workflow(env, eval_task, monkeypatch):
    """Run a real continuous workflow whose reconcile is deterministically
    rejected, and return once it has failed."""
    from temporalio.client import WorkflowFailureError
    from temporalio.common import RetryPolicy
    from temporalio.worker import UnsandboxedWorkflowRunner, Worker

    import tfc.temporal.eval_tasks.workflows as wf
    from tfc.temporal.eval_tasks import get_activities, get_workflows
    from tfc.temporal.eval_tasks.types import ContinuousDrainState
    from tfc.temporal.eval_tasks.workflows import ContinuousEvalTaskWorkflow
    from tracer.selectors.eval_tasks.row_resolver import EvalTaskSelectionRejected

    def _reject(_task):
        raise EvalTaskSelectionRejected("filter cannot be resolved safely")

    monkeypatch.setattr("tracer.services.eval_tasks.reconciler.reconcile", _reject)
    monkeypatch.setattr(wf, "CONTROL_RETRY_POLICY", RetryPolicy(maximum_attempts=1))
    await sync_to_async(EvalTask.objects.filter(id=eval_task.id).update)(
        run_type=RunType.CONTINUOUS
    )

    queue = f"eval-task-test-{uuid.uuid4().hex[:8]}"
    task_id = str(eval_task.id)
    async with Worker(
        env.client,
        task_queue=queue,
        workflows=get_workflows(),
        activities=get_activities(),
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        handle = await env.client.start_workflow(
            ContinuousEvalTaskWorkflow.run,
            ContinuousDrainState(task_id=task_id, task_queue=queue),
            id=f"eval-task-{task_id}",
            task_queue=queue,
        )
        with pytest.raises(WorkflowFailureError):
            await handle.result()
    await asyncio.sleep(0.2)
    await sync_to_async(close_old_connections)()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def registered_sas(workflow_environment):
    """The workflow upserts eval-task Search Attributes; an unregistered one
    fails the workflow task, so register them on the in-memory namespace."""
    from tfc.temporal.eval_tasks.registration import register_search_attributes

    await register_search_attributes(
        workflow_environment.client, workflow_environment.client.namespace
    )
    return workflow_environment


@pytest.mark.e2e
@pytest.mark.xdist_group("temporal_eval_task_e2e")
@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("registered_sas")
class TestFailureAlertEndToEnd:
    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        cache.clear()
        yield
        cache.clear()

    def _use_test_temporal(self, monkeypatch, env):
        import tfc.temporal.common.client as client_module

        async def get_client():
            return env.client

        monkeypatch.setattr(client_module, "get_client", get_client)

    async def test_real_failure_posts_reason_from_history(
        self, workflow_environment, eval_task, monkeypatch, settings
    ):
        import tracer.services.eval_tasks.failure_alert as fa

        settings.ERROR_LOGS_WEBHOOK = "https://hooks.slack.test/x"
        self._use_test_temporal(monkeypatch, workflow_environment)
        posts = []
        monkeypatch.setattr(
            fa,
            "send_critical_slack_notification",
            lambda text, blocks=None, timeout=30: posts.append((text, blocks)) or True,
        )

        await _fail_continuous_workflow(workflow_environment, eval_task, monkeypatch)

        assert (
            await sync_to_async(lambda: EvalTask.objects.get(id=eval_task.id).status)()
            == EvalTaskStatus.FAILED
        )
        assert len(posts) == 1
        text, blocks = posts[0]
        assert "WF Task" in text
        why = blocks[2]["text"]["text"]
        assert (
            "reconcile_eval_task_activity · EvalTaskSelectionRejected · "
            "filter cannot be resolved safely"
        ) in why

    async def test_broken_slack_does_not_change_failure_handling(
        self, workflow_environment, eval_task, monkeypatch, settings
    ):
        import tracer.services.eval_tasks.failure_alert as fa

        settings.ERROR_LOGS_WEBHOOK = "https://hooks.slack.test/x"
        self._use_test_temporal(monkeypatch, workflow_environment)

        def broken(*_a, **_k):
            raise RuntimeError("slack is down")

        monkeypatch.setattr(fa, "send_critical_slack_notification", broken)

        await _fail_continuous_workflow(workflow_environment, eval_task, monkeypatch)

        task = await sync_to_async(EvalTask.objects.get)(id=eval_task.id)
        assert task.status == EvalTaskStatus.FAILED
        assert task.end_time is not None
