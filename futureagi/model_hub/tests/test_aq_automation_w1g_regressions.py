"""W1g RED-first source/export regressions; only ClickHouse I/O is replaced.

F3 + AQ-XWS-W1E-RULE: unavailable and foreign sources have the same withheld
shape. W1F-CONFIGLESS-EXPORT: a config-less score needs its own persisted PG
source FK in the authorized project, not merely a colliding collector id.
"""

import uuid
from types import SimpleNamespace

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.annotation_queues import AnnotationQueue, QueueItem
from model_hub.tests.test_queue_item_source_copy_scope import _Copies, _copy
from model_hub.utils.annotation_queue_helpers import (
    CollectorSourceCache,
    QueueSourceAuthorization,
    resolve_source_preview,
)
from model_hub.views.annotation_queues import _eval_metrics_for_queue_items
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]


@pytest.fixture
def w1g_world(organization, workspace, user):
    foreign_org = Organization.objects.create(name="W1G foreign organization")
    foreign_workspace = Workspace.no_workspace_objects.create(
        name="W1G foreign workspace",
        organization=foreign_org,
        created_by=user,
        is_active=True,
        is_default=True,
    )
    sibling_workspace = Workspace.no_workspace_objects.create(
        name="W1G sibling workspace",
        organization=organization,
        created_by=user,
        is_active=True,
        is_default=False,
    )
    projects = {}
    for name, org, ws in (
        ("local", organization, workspace),
        ("foreign", foreign_org, foreign_workspace),
        ("sibling", organization, sibling_workspace),
        ("null_workspace", organization, None),
    ):
        projects[name] = Project.no_workspace_objects.create(
            name=f"W1G {name} project",
            organization=org,
            workspace=ws,
            model_type="GenerativeLLM",
            trace_type="observe",
        )
    queue = AnnotationQueue.no_workspace_objects.create(
        name="W1G queue",
        status="active",
        organization=organization,
        workspace=workspace,
        project=projects["local"],
        created_by=user,
    )
    assert user.can_access_workspace(workspace), "SETUP_AQ-XWS-W1G: local actor"
    assert not user.can_access_workspace(foreign_workspace), (
        "SETUP_AQ-XWS-W1G: actor cannot access foreign organization"
    )
    request = SimpleNamespace(user=user, organization=organization, workspace=workspace)
    clear_workspace_context()
    return SimpleNamespace(queue=queue, projects=projects, request=request)


def _item(world, span, source_type, *, string_queue_id=False, pinned=True):
    return QueueItem.no_workspace_objects.create(
        queue_id=str(world.queue.pk) if string_queue_id else world.queue.pk,
        organization_id=world.queue.organization_id,
        workspace_id=world.queue.workspace_id,
        project=world.projects["local"] if pinned else None,
        source_type=source_type,
        **{f"{source_type}_id": (span.trace_id if source_type == "trace" else span.id)},
    )


@pytest.mark.parametrize("source_type", ["trace", "observation_span"])
@pytest.mark.parametrize("entrypoint", ["for_items", "resolve_source_preview"])
def test_foreign_and_missing_sources_are_identically_withheld(
    w1g_world, monkeypatch, source_type, entrypoint
):
    world = w1g_world
    spans = [
        _copy(
            world.projects[owner],
            trace_id=str(uuid.uuid4()),
            span_id=uuid.uuid4().hex[:16],
            second=index,
        )
        for index, owner in enumerate(("local", "foreign", "local"))
    ]
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.get_reader",
        lambda: _Copies([(1, spans[0]), (2, spans[1])]),
    )
    items = [
        _item(world, span, source_type, string_queue_id=True, pinned=False)
        for span in spans
    ]
    assert all(isinstance(item.queue_id, str) for item in items), (
        "SETUP_AQ-XWS-W1G: freshly saved API queue ids remain strings"
    )
    # The reloaded UUID and fresh string identify the SAME persisted queue.
    local = QueueItem.no_workspace_objects.get(pk=items[0].pk)
    assert local.queue_id == world.queue.pk, "SETUP_AQ-XWS-W1G: persisted queue"
    positive = resolve_source_preview(local, request=world.request)
    assert positive.get("name") == spans[0].name and "deleted" not in positive, (
        "AQ-XWS-W1G: same-scope persisted source remains visible"
    )

    try:
        cache = None
        if entrypoint == "for_items":
            authorization = QueueSourceAuthorization.for_items(
                items, request=world.request
            )
            cache = CollectorSourceCache.for_items(
                items,
                request=world.request,
                source_authorization=authorization,
            )
        results = [
            resolve_source_preview(item, request=world.request, ch_cache=cache)
            for item in items[1:]
        ]
    except KeyError:
        # Convert the known product crash to the fixed defect assertion, not a
        # fixture/import error. Other failures are deliberately not swallowed.
        results = None
    withheld = {
        "type": source_type,
        "deleted": True,
        "error": "queue_source_unavailable",
    }
    assert results == [withheld, withheld], (
        "AQ-XWS-W1G: foreign and missing sources must share the withheld shape without raising"
    )
    positive = resolve_source_preview(items[0], request=world.request)
    assert positive.get("name") == spans[0].name and "deleted" not in positive, (
        "AQ-XWS-W1G: fresh string queue identity must retain same-scope access"
    )
    counts = []
    for page in ([local], [local, *items]):
        with CaptureQueriesContext(connection) as queries:
            QueueSourceAuthorization.for_items(page, request=world.request)
        counts.append(len(queries.captured_queries))
    assert counts[0] == counts[1], (
        "AQ-XWS-W1G: mixed string and UUID queue identities must keep a flat batch budget"
    )


@pytest.mark.parametrize("source_type", ["observation_span", "trace"])
def test_configless_export_requires_its_own_persisted_source_project(
    w1g_world, auth_client, monkeypatch, source_type
):
    world = w1g_world
    items, copies = {}, []
    for index, owner in enumerate(
        ("local", "foreign", "missing", "sibling", "null_workspace", "deleted")
    ):
        # CH holds an authorized copy for EVERY locator. Only PG ownership may
        # authorize the config-less log; the foreign PG copy collides with CH.
        span = _copy(
            world.projects["local"],
            trace_id=str(uuid.uuid4()),
            span_id=uuid.uuid4().hex[:16],
            second=index,
        )
        copies.append((index, span))
        items[owner] = _item(world, span, source_type)
        log_fields = {"trace_id": span.trace_id, "observation_span_id": span.id}
        if owner != "missing":
            project = world.projects["local" if owner == "deleted" else owner]
            trace = Trace.no_workspace_objects.create(
                id=span.trace_id, project=project, name=f"W1G {owner} trace"
            )
            pg_span = ObservationSpan.no_workspace_objects.create(
                id=span.id,
                project=project,
                trace=trace,
                name=f"W1G {owner} span",
                observation_type="agent",
                status="OK",
            )
            # Matches langfuse_upsert: persisted trace + observation_span, no config.
            log_fields = {"trace": trace, "observation_span": pg_span}
            if owner == "foreign":
                assert pg_span.project.organization_id != world.queue.organization_id, (
                    "SETUP_AQ-XWS-W1G: colliding PG source belongs to another organization"
                )
                copies.append(
                    (
                        20,
                        _copy(
                            project,
                            trace_id=span.trace_id,
                            span_id=span.id,
                            second=index,
                        ),
                    )
                )
            if owner == "deleted":
                ObservationSpan.no_workspace_objects.filter(pk=pg_span.pk).update(
                    deleted=True
                )
                Trace.no_workspace_objects.filter(pk=trace.pk).update(deleted=True)
        else:
            assert not ObservationSpan.no_workspace_objects.filter(
                pk=span.id
            ).exists(), "SETUP_AQ-XWS-W1G: missing PG span really does not exist"
            assert not Trace.no_workspace_objects.filter(pk=span.trace_id).exists(), (
                "SETUP_AQ-XWS-W1G: missing PG trace really does not exist"
            )
        log = EvalLogger.no_workspace_objects.create(
            **log_fields,
            eval_id=f"w1g-imported-{owner}",
            eval_type_id="w1g-quality",
            output_float=0.9,
        )
        assert log.custom_eval_config_id is None, (
            "SETUP_AQ-XWS-W1G: production-shaped imported score has no config"
        )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.get_reader", lambda: _Copies(copies)
    )
    response = auth_client.get(
        f"/model-hub/annotation-queues/{world.queue.pk}/export/",
        {"export_format": "json"},
    )
    assert response.status_code == 200, "SETUP_AQ-XWS-W1G: export succeeds"
    body = response.data.get("result", response.data)
    rows = {row["item_id"]: row for row in body}
    assert set(rows) == {str(item.pk) for item in items.values()}, (
        "SETUP_AQ-XWS-W1G: export includes every requested item"
    )
    assert all("deleted" not in row["source"] for row in rows.values()), (
        "SETUP_AQ-XWS-W1G: authorized CH copies exist for all score locators"
    )
    evals = {owner: rows[str(item.pk)]["evals"] for owner, item in items.items()}
    assert (
        evals["local"].get("w1g-quality", {}).get("score") == 0.9
        and evals["foreign"] == evals["missing"] == {}
        and evals["sibling"] == evals["null_workspace"] == evals["deleted"] == {}
    ), (
        "AQ-XWS-W1G: configless export must include the authorized PG source and identically exclude foreign and missing anchors"
    )

    # Reuse the real page authorization/cache; the score selector must not
    # introduce a per-log or per-source PG read as the page grows.
    page = list(items.values())
    cache = CollectorSourceCache.for_items(page, request=world.request)
    counts = []
    for selected in ([items["local"]], page):
        with CaptureQueriesContext(connection) as queries:
            _eval_metrics_for_queue_items(
                selected, request=world.request, ch_cache=cache
            )
        counts.append(len(queries.captured_queries))
    assert counts[0] == counts[1] and counts[1] <= 2, (
        "AQ-XWS-W1G: configless score authorization must add at most one flat batched query"
    )


@pytest.mark.parametrize("allowed_source", ["observation_span", "trace"])
def test_configless_export_cannot_borrow_the_other_source_fk(
    w1g_world, monkeypatch, allowed_source
):
    """Both families are on the page, so one valid FK admits each log to SQL."""
    world = w1g_world
    pairs, copies = [], []
    for index, refused_owner in enumerate(("foreign", "missing", "deleted")):
        span = _copy(
            world.projects["local"],
            trace_id=str(uuid.uuid4()),
            span_id=uuid.uuid4().hex[:16],
            second=index,
        )
        copies.append((index, span))
        pair = {
            family: _item(world, span, family)
            for family in ("observation_span", "trace")
        }
        pairs.append(pair)
        for family in pair:
            allowed = family == allowed_source
            if not allowed and refused_owner == "missing":
                continue
            project = world.projects[
                "foreign" if not allowed and refused_owner == "foreign" else "local"
            ]
            if family == "trace":
                source = Trace.no_workspace_objects.create(
                    id=span.trace_id, project=project, name="W1G independent trace"
                )
            else:
                source = ObservationSpan.no_workspace_objects.create(
                    id=span.id,
                    project=project,
                    trace_id=span.trace_id,
                    name="W1G independent span",
                    observation_type="agent",
                    status="OK",
                )
            if not allowed and refused_owner == "deleted":
                type(source).no_workspace_objects.filter(pk=source.pk).update(
                    deleted=True
                )
        EvalLogger.no_workspace_objects.create(
            observation_span_id=span.id,
            trace_id=span.trace_id,
            eval_type_id="w1g-independent",
            output_float=0.9,
        )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.get_reader", lambda: _Copies(copies)
    )
    page = [item for pair in pairs for item in pair.values()]
    cache = CollectorSourceCache.for_items(page, request=world.request)
    metrics = _eval_metrics_for_queue_items(page, request=world.request, ch_cache=cache)
    refused_source = (
        "trace" if allowed_source == "observation_span" else "observation_span"
    )
    assert all(
        metrics[pair[allowed_source].pk].get("w1g-independent", [{}])[0].get("score")
        == 0.9
        and metrics[pair[refused_source].pk] == {}
        for pair in pairs
    ), (
        "AQ-XWS-W1G: each configless target needs its own PG anchor even when the other FK is authorized"
    )
