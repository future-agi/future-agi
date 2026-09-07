"""Offline guards/protocol tests; no Django setup, infrastructure, or live QA.

python -m unittest discover -s futureagi/scripts/property_catalog_oss/tests/managed_smoke \
    -p test_projectless_smoke.py -v
"""

from __future__ import annotations

import ast
import copy
import json
import tempfile
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import projectless_smoke as smoke


class Row(SimpleNamespace):
    def delete(self, **kwargs):
        self.deleted = True
        self.deletes += 1


class Rows(list):
    def order_by(self, key):
        return Rows(sorted(self, key=lambda row: str(getattr(row, key))))


class Manager:
    def __init__(self):
        self.rows = {}

    def using(self, alias):
        assert alias == "default"
        return self

    def get_or_create(self, *, id, defaults):
        if id in self.rows:
            return self.rows[id], False
        self.rows[id] = Row(
            id=id,
            deleted=False,
            deletes=0,
            api_key="test-key",
            secret_key="test-secret",
            **copy.deepcopy(defaults),
        )
        return self.rows[id], True

    def filter(self, **fields):
        return Rows(
            row
            for row in self.rows.values()
            if all(
                str(getattr(row, key)) == str(value) for key, value in fields.items()
            )
        )

    def get(self, **fields):
        rows = self.filter(**fields)
        if len(rows) != 1:
            raise RuntimeError("missing exact test row")
        return rows[0]


def page(items, *, cursor=None, catalog=True):
    result = {
        "metrics" if catalog else "values": items,
        "query_complete": True,
        "query_status": "complete",
        "has_more": cursor is not None,
        "next_cursor": cursor,
    }
    if catalog:
        result.update(
            query_provenance="activated_property_catalog",
            catalog_epoch=7,
            catalog_revision=9,
            activation_fingerprint="a" * 64,
        )
    return result


class ProjectlessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.run = SimpleNamespace(
            directory=Path(temporary.name),
            owned=Mock(return_value=["owned"]),
            manifest={
                "application": True,
                "run_id": "0123456789abcdef",
                "ports": {"postgres": 15499, "native": 19099, "otlp": 14399},
            },
        )
        self.original = {
            "organization_id": "00000000-0000-4000-8000-000000000001",
            "workspace_id": "00000000-0000-4000-8000-000000000002",
            "project_id": "00000000-0000-4000-8000-000000000003",
        }
        self.write("source-fixture.json", self.original)
        (self.run.directory / "application-runtime").mkdir()
        self.write(
            "application-runtime/runtime-identity-v1.json", {"offline_identity": True}
        )
        self.plan = smoke.fixture_plan(self.run, self.original)
        self.models = {"admin_role": "workspace_admin"}
        for kind in (
            "workspace",
            "membership",
            "org_membership",
            "key",
            *self.plan["ids"],
        ):
            manager = Manager()
            self.models[kind] = SimpleNamespace(
                all_objects=manager, no_workspace_objects=manager
            )
        self.models["key"].all_objects.get_or_create(
            id="original-key",
            defaults={
                "name": "managed-smoke-api",
                "organization_id": self.original["organization_id"],
                "workspace_id": self.original["workspace_id"],
                "enabled": True,
                "type": "user",
                "user_id": "owner",
            },
        )
        self.models["org_membership"].all_objects.get_or_create(
            id="original-membership",
            defaults={
                "organization_id": self.original["organization_id"],
                "user_id": "owner",
            },
        )

    def write(self, name, value):
        (self.run.directory / name).write_text(json.dumps(value))

    def context(self):
        stack = ExitStack()
        stack.enter_context(patch.object(smoke, "_guard"))
        stack.enter_context(patch.object(smoke, "_models", return_value=self.models))
        stack.enter_context(patch.object(smoke, "_atomic", return_value=nullcontext()))
        return stack

    def seed(self):
        with self.context():
            return smoke.seed(self.run)

    def passed(self, phase, revision=9):
        self.write(
            "catalog-projectless-" + phase + ".json",
            {
                "status": "passed",
                "phase": phase,
                "fixture_sha256": smoke._digest(self.plan),
                "evidence": {"catalog_epoch": 7, "catalog_revision": revision},
            },
        )

    def add_project(self):
        self.seed()
        self.passed("seeded")
        with self.context():
            smoke.update(self.run)
        self.passed("project", revision=10)

    def test_every_action_refuses_unowned_before_import_or_network(self):
        self.run.owned.return_value = []
        actions = [
            smoke.seed,
            smoke.update,
            smoke.delete,
            smoke.ingest,
            smoke.verify,
            lambda run: smoke.verify_restart(run, phase="seeded"),
        ]
        for action in actions:
            with (
                self.subTest(action=action),
                patch.object(smoke, "_models") as models,
                self.assertRaisesRegex(RuntimeError, "owned disposable"),
            ):
                action(self.run)
            models.assert_not_called()

    def test_fixture_identity_is_stable_disjoint_and_not_a_synthetic_project(self):
        before = copy.deepcopy(self.original)
        self.assertEqual(self.plan, smoke.fixture_plan(self.run, self.original))
        ids = [
            self.plan[k] for k in ("workspace_id", "key_id", "membership_id")
        ] + list(self.plan["ids"].values())
        self.assertEqual(len(set(ids)), 10)
        self.assertFalse(set(ids) & set(self.original.values()))
        self.assertEqual(before, self.original)
        self.assertNotIn("project", smoke._specs(self.plan))
        self.assertNotIn("config", smoke._specs(self.plan))
        self.run.manifest["run_id"] = "../bad"
        with self.assertRaisesRegex(RuntimeError, "run identity"):
            smoke.fixture_plan(self.run, self.original)

    def test_seed_creates_normal_workspace_authorization_and_six_rows_no_project(self):
        before = (self.run.directory / "source-fixture.json").read_bytes()
        self.seed()
        self.seed()  # Exact retry; never duplicate keys/membership/definitions.
        self.assertEqual(self.models["project"].all_objects.rows, {})
        self.assertEqual(len(self.models["workspace"].all_objects.rows), 1)
        self.assertEqual(len(self.models["membership"].all_objects.rows), 1)
        for kind in smoke._specs(self.plan):
            self.assertEqual(len(self.models[kind].all_objects.rows), 1)
        self.assertEqual(len(self.models["key"].all_objects.rows), 2)
        self.assertFalse(
            self.models["workspace"]
            .all_objects.get(id=self.plan["workspace_id"])
            .is_default
        )
        self.assertEqual(
            (self.run.directory / "source-fixture.json").read_bytes(), before
        )

    def test_seed_wont_adopt_project_or_relabel_tampered_definition(self):
        self.seed()
        template = self.models["template"].all_objects.get(
            id=self.plan["ids"]["template"]
        )
        template.name = "foreign record"
        with self.assertRaisesRegex(RuntimeError, "refusing relabel"):
            self.seed()
        self.assertEqual(template.name, "foreign record")
        for rows in (
            [SimpleNamespace(id="unrelated", deleted=False, trace_type="experiment")],
            [
                SimpleNamespace(
                    id=self.plan["ids"]["project"], deleted=True, trace_type="observe"
                )
            ],
        ):
            with self.assertRaisesRegex(RuntimeError, "inventory"):
                smoke._check_projects(rows, self.plan, "seeded")

    def test_update_requires_qualified_projectless_report_and_preserves_ids(self):
        self.seed()
        before = copy.deepcopy(self.plan)
        with self.context(), self.assertRaises(FileNotFoundError):
            smoke.update(self.run)
        self.passed("seeded")
        with self.context():
            smoke.update(self.run)
            smoke.update(self.run)
        self.assertEqual(len(self.models["project"].all_objects.rows), 1)
        project = self.models["project"].all_objects.get(id=self.plan["ids"]["project"])
        self.assertEqual(
            (project.trace_type, project.name),
            ("observe", self.plan["prefix"] + "-observe"),
        )
        self.assertEqual(smoke._plan(self.run), before)

    def test_delete_requires_observed_readback_and_only_softdeletes_last_owned_project(
        self,
    ):
        self.add_project()
        with self.context(), self.assertRaises(FileNotFoundError):
            smoke.delete(self.run)
        self.passed("observed", revision=11)
        with self.context():
            smoke.delete(self.run)
            smoke.delete(self.run)
        project = self.models["project"].all_objects.get(id=self.plan["ids"]["project"])
        self.assertTrue(project.deleted)
        self.assertEqual(project.deletes, 1)
        for kind in smoke._specs(self.plan):
            self.assertFalse(
                self.models[kind].all_objects.get(id=self.plan["ids"][kind]).deleted
            )

    def test_delete_refuses_extra_project_even_if_not_observe(self):
        self.add_project()
        self.passed("observed")
        self.models["project"].all_objects.get_or_create(
            id="other",
            defaults={
                "organization_id": self.plan["organization_id"],
                "workspace_id": self.plan["workspace_id"],
                "trace_type": "experiment",
            },
        )
        with self.context(), self.assertRaisesRegex(RuntimeError, "inventory"):
            smoke.delete(self.run)
        self.assertTrue(
            all(
                not row.deleted
                for row in self.models["project"].all_objects.rows.values()
            )
        )

    def test_otlp_claim_binds_exact_workspace_project_trace_values_and_time(self):
        body = smoke._otlp(self.plan, 123)
        resource = body["resourceSpans"][0]
        self.assertEqual(
            resource["resource"]["attributes"][0]["value"]["stringValue"],
            self.plan["prefix"] + "-observe",
        )
        span = resource["scopeSpans"][0]["spans"][0]
        self.assertEqual(span["traceId"], self.plan["trace_id"])
        self.assertEqual(span["spanId"], self.plan["trace_id"][:16])
        self.assertEqual(span["startTimeUnixNano"], "123")
        with self.assertRaises(RuntimeError):
            smoke._otlp(self.plan, True)

    def test_ingest_checks_collector_then_one_actual_authenticated_post(self):
        self.add_project()
        session = Mock()
        session.post.return_value = SimpleNamespace(status_code=200, json=lambda: {})

        def checked_post(*args, **kwargs):
            self.assertTrue(
                (self.run.directory / "projectless-otlp-attempt.json").exists()
            )
            return SimpleNamespace(status_code=200, json=lambda: {})

        session.post.side_effect = checked_post
        with (
            self.context(),
            patch("ingestion_smoke.collector_state") as collector,
            patch("requests.Session") as factory,
        ):
            factory.return_value.__enter__.return_value = session
            smoke.ingest(self.run)
            second = smoke.ingest(self.run)
        collector.assert_called_once_with(self.run)
        session.post.assert_called_once()
        call = session.post.call_args
        self.assertEqual(call.args[0], "http://127.0.0.1:14399/v1/traces")
        self.assertEqual(
            call.kwargs["headers"],
            {"X-Api-Key": "test-key", "X-Secret-Key": "test-secret"},
        )
        self.assertFalse(call.kwargs["allow_redirects"])
        self.assertFalse(session.trust_env)
        self.assertTrue(second["already_accepted"])

    def test_uncertain_or_partial_otlp_never_reposts_and_never_writes_acceptance(self):
        self.add_project()
        session = Mock()
        session.post.side_effect = TimeoutError("uncertain")
        with (
            self.context(),
            patch("ingestion_smoke.collector_state"),
            patch("requests.Session") as factory,
        ):
            factory.return_value.__enter__.return_value = session
            with self.assertRaises(TimeoutError):
                smoke.ingest(self.run)
            with self.assertRaisesRegex(RuntimeError, "never repost"):
                smoke.ingest(self.run)
        self.assertEqual(session.post.call_count, 1)
        self.assertFalse(
            (self.run.directory / "projectless-otlp-accepted.json").exists()
        )
        self.assertEqual(
            smoke._claim(self.run, self.plan)["fixture_sha256"],
            smoke._digest(self.plan),
        )

    def test_bad_collector_blocks_before_attempt_and_post(self):
        self.add_project()
        with (
            self.context(),
            patch(
                "ingestion_smoke.collector_state",
                side_effect=RuntimeError("foreign collector"),
            ),
            patch("requests.Session") as factory,
            self.assertRaisesRegex(RuntimeError, "foreign collector"),
        ):
            smoke.ingest(self.run)
        factory.assert_not_called()
        self.assertFalse(
            (self.run.directory / "projectless-otlp-attempt.json").exists()
        )

    def test_changed_claim_is_rejected_not_adopted(self):
        claim = {
            "timestamp_ns": 123,
            "fixture_sha256": smoke._digest(self.plan),
            "payload_sha256": "wrong",
        }
        self.write("projectless-otlp-attempt.json", claim)
        with self.assertRaisesRegex(RuntimeError, "identity changed"):
            smoke._claim(self.run, self.plan)

    def test_metrics_bind_pagination_to_one_selected_identity(self):
        first = page([{"property_id": "a"}], cursor="signed")
        last = page([{"property_id": "b"}])
        with patch.object(smoke, "_page", side_effect=[first, last]) as read:
            items, metadata = smoke._metrics(object(), {}, float("inf"))
        self.assertEqual(set(items), {"a", "b"})
        self.assertEqual(read.call_args_list[1].args[2]["cursor"], "signed")
        self.assertEqual(metadata["catalog_epoch"], 7)
        last["activation_fingerprint"] = "b" * 64
        with (
            patch.object(smoke, "_page", side_effect=[first, last]),
            self.assertRaisesRegex(RuntimeError, "changed selected"),
        ):
            smoke._metrics(object(), {}, float("inf"))

    def test_metrics_reject_duplicates_missing_identity_and_unbounded_continuation(
        self,
    ):
        for result in (
            page([{"property_id": "same"}, {"property_id": "same"}]),
            {**page([]), "catalog_epoch": 0},
            page([{}]),
        ):
            with (
                patch.object(smoke, "_page", return_value=result),
                self.assertRaises(RuntimeError),
            ):
                smoke._metrics(object(), {}, float("inf"))
        with (
            patch.object(
                smoke,
                "_page",
                side_effect=[
                    page([{"property_id": str(i)}], cursor=str(i)) for i in range(8)
                ],
            ),
            self.assertRaisesRegex(RuntimeError, "eight pages"),
        ):
            smoke._metrics(object(), {}, float("inf"))

    def test_pending_requires_typed_bootstrap_and_http_errors_do_not_retry(self):
        pending = {
            "metrics": [],
            "query_provenance": "property_catalog_bootstrap",
            "query_complete": False,
            "query_status": "pending",
            "query_exact": False,
        }
        with patch.object(smoke, "request", return_value=(200, {"result": pending})):
            self.assertIsNone(
                smoke._page(object(), "metrics", {}, float("inf"), pending=True)
            )
        for status, payload in (
            (503, {"result": {}}),
            (200, {"result": "bad"}),
            (200, {"result": {**pending, "query_complete": True}}),
        ):
            with (
                patch.object(smoke, "request", return_value=(status, payload)),
                self.assertRaises(RuntimeError),
            ):
                smoke._page(object(), "metrics", {}, float("inf"), pending=True)

    def evidence_fixture(self, phase="seeded"):
        metadata = {
            "catalog_epoch": 7,
            "catalog_revision": 12,
            "activation_fingerprint": "a" * 64,
        }
        activation = SimpleNamespace(
            source_scope=SimpleNamespace(
                project_ids=()
                if phase in ("seeded", "deleted")
                else (self.plan["ids"]["project"],)
            ),
            activation_sha256="a" * 64,
            build_token="build",
        )
        specs = [
            ("span_attribute", role)
            for role in ("definitions", "values", "hot_values", "source_audit")
        ]
        specs += [
            (name, "definitions")
            for name in (
                "system_manifest",
                "eval_template",
                "eval_config",
                "simulation_eval_config",
                "annotation_label",
                "dataset_column",
            )
        ]
        streams, checkpoints = [], []
        for index, (adapter, role) in enumerate(specs):
            streams.append(
                SimpleNamespace(
                    source_adapter=adapter, role=role, producer_stream_id=str(index)
                )
            )
            checkpoints.append(
                {
                    "source_adapter": adapter,
                    "producer_stream_id": str(index),
                    "status": "complete",
                    "terminal": 1,
                    "source_count": 0 if adapter == "span_attribute" else 1,
                    "value_count": 0,
                    "delivery_count": 2 if role == "source_audit" else 1,
                }
            )
        return metadata, activation, SimpleNamespace(streams=streams), checkpoints

    def test_zero_source_has_ten_streams_and_positive_actual_deliveries(self):
        args = self.evidence_fixture()
        evidence = smoke._summary(self.plan, "seeded", *args)
        self.assertEqual(evidence["required_stream_deliveries"], 11)
        self.assertEqual(evidence["project_ids"], [])
        self.assertEqual(
            evidence["span_streams"]["source_audit"],
            {"source_count": 0, "value_count": 0, "delivery_count": 2},
        )
        args[1].source_scope.project_ids = (self.plan["ids"]["project"],)
        self.assertIsNone(smoke._summary(self.plan, "seeded", *args))

    def test_missing_extra_nonterminal_corrupt_and_fake_zero_delivery_evidence_rejected(
        self,
    ):
        for field, value in (
            ("delivery_count", 0),
            ("delivery_count", "1"),
            ("source_count", 1),
            ("value_count", 1),
            ("terminal", 0),
            ("status", "gap"),
        ):
            args = self.evidence_fixture()
            args[3][0][field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                smoke._summary(self.plan, "seeded", *args)
        args = self.evidence_fixture()
        args[3].pop()
        with self.assertRaisesRegex(RuntimeError, "ten-stream"):
            smoke._summary(self.plan, "seeded", *args)

    def test_obsolete_and_foreign_project_scopes_require_actual_rejection(self):
        first = page(
            [{"value": self.plan["values"][0], "label": self.plan["values"][0]}],
            catalog=False,
            cursor="signed",
        )
        second = page(
            [{"value": self.plan["values"][1], "label": self.plan["values"][1]}],
            catalog=False,
        )
        empty = page([], catalog=False)
        with (
            patch.object(smoke, "_page", side_effect=[first, second, empty]),
            patch.object(smoke, "request", return_value=(400, {})) as request,
        ):
            denied = smoke._checks(object(), self.plan, "deleted", float("inf"))
        self.assertEqual(len(denied), 4)
        self.assertEqual(
            {call.args[2]["project_ids"] for call in request.call_args_list},
            {self.original["project_id"], self.plan["ids"]["project"]},
        )
        with (
            patch.object(smoke, "_page", side_effect=[first, second, empty]),
            patch.object(smoke, "request", return_value=(503, {})),
            self.assertRaisesRegex(RuntimeError, "not rejected"),
        ):
            smoke._checks(object(), self.plan, "deleted", float("inf"))

    def test_verify_real_contract_polling_report_and_no_dataset_values(self):
        self.seed()
        now = [100.0]
        scope_rows = smoke._scope_rows

        def guard(*args, **kwargs):
            now[0] += 2

        def scope(*args):
            now[0] += 3
            return scope_rows(*args)

        metrics = {
            identity: {
                "property_id": identity,
                "source": source,
                "category": category,
                "display_name": smoke._specs(self.plan)[kind]["name"],
            }
            for identity, (source, category, kind) in smoke._definitions(
                self.plan
            ).items()
        }
        metadata = self.evidence_fixture()[0]
        evidence = smoke._summary(self.plan, "seeded", *self.evidence_fixture())
        with (
            self.context(),
            patch.object(smoke, "_guard", side_effect=guard),
            patch.object(smoke, "_scope_rows", side_effect=scope),
            patch.object(smoke.time, "monotonic", side_effect=lambda: now[0]),
            patch.object(smoke, "_client", return_value=object()),
            patch.object(
                smoke,
                "_metrics",
                side_effect=[None, (metrics, metadata), ({}, metadata)],
            ) as metric_reads,
            patch.object(smoke, "_evidence", return_value=evidence),
            patch.object(smoke, "_checks", return_value=[]),
            patch.object(smoke.time, "sleep"),
        ):
            report = smoke.verify(self.run)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["elapsed_seconds"], 8)
        self.assertTrue(
            all(call.args[2] == 190 for call in metric_reads.call_args_list)
        )
        self.assertEqual(report["dataset_values"], "definitions_only_not_tested")
        self.assertFalse(report["restart_probe_only"])
        self.assertEqual(
            json.loads(
                (self.run.directory / "catalog-projectless-seeded.json").read_text()
            ),
            report,
        )

    def test_restart_probe_uses_durable_prior_report_and_never_claims_restart(self):
        with patch.object(smoke, "verify", return_value={}) as verify:
            smoke.verify_restart(self.run, phase="deleted", timeout=20)
        verify.assert_called_once_with(
            self.run, phase="deleted", timeout=20, _restart=True
        )
        self.seed()
        with self.context(), self.assertRaises(FileNotFoundError):
            smoke.verify_restart(self.run, phase="seeded")

    def test_observed_report_refreshes_exact_values_activation_or_fails(self):
        self.add_project()
        metrics = {
            identity: {
                "property_id": identity,
                "source": source,
                "category": category,
                "display_name": smoke._specs(self.plan)[kind]["name"],
            }
            for identity, (source, category, kind) in smoke._definitions(
                self.plan
            ).items()
        }
        old_metadata = self.evidence_fixture("observed")[0]
        visible_metadata = {
            **old_metadata,
            "catalog_revision": 13,
            "activation_fingerprint": "b" * 64,
        }
        old_evidence = smoke._summary(
            self.plan, "observed", *self.evidence_fixture("observed")
        )
        visible_evidence = copy.deepcopy(old_evidence)
        visible_evidence.update(visible_metadata)
        visible_evidence["span_streams"]["values"].update(source_count=1, value_count=2)
        marker = {"custom_attribute:" + self.plan["marker"]: {}}
        for failure in (None, "scope", "corrupt", "timeout"):
            now, events = [0], []

            def values(*args, events=events, **kwargs):
                events.append("values")
                return {
                    **page([{"value": v} for v in self.plan["values"]], catalog=False),
                    **visible_metadata,
                }

            def evidence(
                run,
                plan,
                phase,
                metadata,
                deadline,
                *,
                events=events,
                failure=failure,
                now=now,
            ):
                events.append("evidence")
                if len(events) == 1:
                    self.assertEqual(metadata, old_metadata)
                    return old_evidence
                self.assertEqual(events, ["evidence", "values", "evidence"])
                self.assertEqual(metadata, visible_metadata)
                self.assertEqual(deadline, 90)
                if failure == "scope":
                    return None
                if failure == "corrupt":
                    raise RuntimeError("corrupt activation proof")
                if failure == "timeout":
                    now[0] = 90
                return visible_evidence

            with (
                self.subTest(failure=failure),
                self.context(),
                patch.object(smoke, "_claim"),
                patch.object(smoke, "_client", return_value=object()),
                patch.object(
                    smoke.time, "monotonic", side_effect=lambda now=now: now[0]
                ),
                patch.object(
                    smoke,
                    "_metrics",
                    side_effect=[(metrics, old_metadata), (marker, visible_metadata)],
                ),
                patch.object(smoke, "_page", side_effect=values),
                patch.object(smoke, "_evidence", side_effect=evidence) as proof,
                patch.object(smoke, "_checks", return_value=[]),
            ):
                if failure is None:
                    report = smoke.verify(self.run, phase="observed")
                    self.assertEqual(report["evidence"], visible_evidence)
                else:
                    with self.assertRaises(RuntimeError):
                        smoke.verify(self.run, phase="observed")
                self.assertEqual(proof.call_count, 2)
            saved = json.loads(
                (self.run.directory / "catalog-projectless-observed.json").read_text()
            )
            self.assertEqual(saved["status"], "passed" if failure is None else "failed")
            if failure is None:
                self.assertEqual(saved["evidence"], visible_evidence)
            else:
                self.assertNotIn("evidence", saved)

    def test_bad_phase_budget_and_deadline_fail_closed(self):
        with self.context():
            for kwargs in (
                {"phase": "fake"},
                {"timeout": 0},
                {"timeout": 90.001},
                {"timeout": 301},
                {"timeout": float("nan")},
            ):
                with self.assertRaises(ValueError):
                    smoke.verify(self.run, **kwargs)
        with self.assertRaisesRegex(RuntimeError, "bounded wall"):
            smoke._budget(0)

    def test_guard_time_exhausts_budget_before_fixture_or_api_reads(self):
        now = [0]

        def guard(*args, **kwargs):
            now[0] = 90

        with (
            patch.object(smoke.time, "monotonic", side_effect=lambda: now[0]),
            patch.object(smoke, "_guard", side_effect=guard) as checked,
            patch.object(smoke, "_plan") as plan,
            patch.object(smoke, "_client") as client,
            self.assertRaisesRegex(RuntimeError, "bounded wall"),
        ):
            smoke.verify(self.run)
        checked.assert_called_once_with(self.run, write=False)
        plan.assert_not_called()
        client.assert_not_called()

    def test_orm_fixture_and_scope_time_exhaustion_prevents_next_read(self):
        self.seed()
        for name in ("_row", "_scope_rows"):
            now = [0]
            original = getattr(smoke, name)

            def slow(*args, original=original, now=now, **kwargs):
                result = original(*args, **kwargs)
                now[0] = 90
                return result

            with (
                self.subTest(stage=name),
                self.context(),
                patch.object(
                    smoke.time, "monotonic", side_effect=lambda now=now: now[0]
                ),
                patch.object(smoke, name, side_effect=slow) as reads,
                patch.object(smoke, "_client") as client,
                self.assertRaisesRegex(RuntimeError, "bounded wall"),
            ):
                smoke.verify(self.run)
            self.assertEqual(reads.call_count, 1)
            client.assert_not_called()

    def test_verify_http_and_unqualified_errors_fail_once_without_polling(self):
        self.seed()
        for status, result in (
            (400, {}),
            (503, {}),
            (200, {**page([]), "query_complete": False}),
            (200, {**page([]), "query_provenance": "legacy"}),
        ):
            with (
                self.subTest(status=status, result=result),
                self.context(),
                patch.object(smoke, "_client", return_value=object()),
                patch.object(
                    smoke, "request", return_value=(status, {"result": result})
                ) as request,
                patch.object(smoke.time, "sleep") as sleep,
                self.assertRaises(RuntimeError),
            ):
                smoke.verify(self.run)
            self.assertEqual(request.call_count, 1)
            sleep.assert_not_called()
            report = json.loads(
                (self.run.directory / "catalog-projectless-seeded.json").read_text()
            )
            self.assertEqual(report["status"], "failed")

    def test_api_deadline_checked_before_and_after_call(self):
        for initial, expected_calls in ((90, 0), (0, 1)):
            now = [initial]

            def slow(*args, now=now):
                now[0] = 90
                return 200, {"result": page([])}

            with (
                self.subTest(initial=initial),
                patch.object(
                    smoke.time, "monotonic", side_effect=lambda now=now: now[0]
                ),
                patch.object(smoke, "request", side_effect=slow) as request,
                self.assertRaisesRegex(RuntimeError, "bounded wall"),
            ):
                smoke._page(object(), "metrics", {}, 90)
            self.assertEqual(request.call_count, expected_calls)

    def test_scope_denial_request_cannot_overrun_and_continue(self):
        pages = [
            page(
                [{"value": value, "label": value}],
                catalog=False,
                cursor="signed" if index == 0 else None,
            )
            for index, value in enumerate(self.plan["values"])
        ] + [page([], catalog=False)]
        now = [0]

        def slow(*args):
            now[0] = 90
            return 400, {}

        with (
            patch.object(smoke, "_page", side_effect=pages),
            patch.object(smoke.time, "monotonic", side_effect=lambda: now[0]),
            patch.object(smoke, "request", side_effect=slow) as request,
            self.assertRaisesRegex(RuntimeError, "bounded wall"),
        ):
            smoke._checks(object(), self.plan, "deleted", 90)
        self.assertEqual(request.call_count, 1)

    def test_final_orm_scope_overrun_never_records_success(self):
        self.seed()
        now, calls = [0], []
        scope_rows = smoke._scope_rows

        def scope(*args):
            calls.append(args)
            if len(calls) == 2:
                now[0] = 90
            return scope_rows(*args)

        metrics = {
            identity: {
                "property_id": identity,
                "source": source,
                "category": category,
                "display_name": smoke._specs(self.plan)[kind]["name"],
            }
            for identity, (source, category, kind) in smoke._definitions(
                self.plan
            ).items()
        }
        metadata = self.evidence_fixture()[0]
        evidence = smoke._summary(self.plan, "seeded", *self.evidence_fixture())
        with (
            self.context(),
            patch.object(smoke, "_scope_rows", side_effect=scope),
            patch.object(smoke.time, "monotonic", side_effect=lambda: now[0]),
            patch.object(smoke, "_client", return_value=object()),
            patch.object(
                smoke, "_metrics", side_effect=[(metrics, metadata), ({}, metadata)]
            ),
            patch.object(smoke, "_evidence", return_value=evidence),
            patch.object(smoke, "_checks", return_value=[]),
            self.assertRaisesRegex(RuntimeError, "bounded wall"),
        ):
            smoke.verify(self.run)
        report = json.loads(
            (self.run.directory / "catalog-projectless-seeded.json").read_text()
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["elapsed_seconds"], 90)

    def test_readonly_endpoint_is_exact_not_merely_an_allowed_database_prefix(self):
        config = SimpleNamespace(
            host="127.0.0.1",
            port=19099,
            database="property_catalog_dev_app_0123456789abcdef",
            user="property_catalog_oss_api",
        )
        smoke._check_read_config(self.run, config)
        for bad in (
            {"host": "production"},
            {"port": 9000},
            {"database": "property_catalog"},
            {"database": "property_catalog_dev_app_foreign"},
            {"user": "default"},
        ):
            with (
                self.subTest(bad=bad),
                self.assertRaisesRegex(RuntimeError, "readonly scope"),
            ):
                smoke._check_read_config(
                    self.run, SimpleNamespace(**{**vars(config), **bad})
                )

    def test_checkpoint_query_uses_actual_schema_names_and_exact_selected_scope(self):
        sql = smoke._checkpoint_sql("property_catalog_dev_app_0123456789abcdef")
        self.assertIn("source_rows AS source_count", sql)
        self.assertIn("value_rows AS value_count", sql)
        self.assertIn("toString(producer_stream_id)", sql)
        self.assertIn("_version=latest_version", sql)
        self.assertIn("SELECT DISTINCT", sql)
        self.assertNotIn("LIMIT 1 BY", sql)
        self.assertIn("LIMIT 11", sql)
        for key in (
            "catalog_organization_id",
            "catalog_workspace_id",
            "catalog_epoch",
            "catalog_revision",
            "build_token",
        ):
            self.assertIn("%(" + key + ")s", sql)
        for database in (
            "property_catalog",
            "default",
            "property_catalog_dev_app_bad`",
        ):
            with self.assertRaises(RuntimeError):
                smoke._checkpoint_sql(database)

    def test_fsync_failure_blocks_post_and_future_repost(self):
        self.add_project()
        with (
            self.context(),
            patch("ingestion_smoke.collector_state"),
            patch("requests.Session") as factory,
            patch("os.fsync", side_effect=OSError("disk failure")),
        ):
            with self.assertRaises(OSError):
                smoke.ingest(self.run)
            with self.assertRaisesRegex(RuntimeError, "never repost"):
                smoke.ingest(self.run)
        factory.assert_not_called()

    def test_project_field_drift_blocks_delete_even_with_same_uuid(self):
        self.add_project()
        self.passed("observed")
        project = self.models["project"].all_objects.get(id=self.plan["ids"]["project"])
        project.name = "renamed outside fixture"
        with (
            self.context(),
            self.assertRaisesRegex(RuntimeError, "project fields changed"),
        ):
            smoke.delete(self.run)
        self.assertFalse(project.deleted)

    def test_restart_readback_preserves_epoch_identity_and_does_not_overwrite_baseline(
        self,
    ):
        self.seed()
        baseline = {
            "status": "passed",
            "phase": "seeded",
            "fixture_sha256": smoke._digest(self.plan),
            "runtime_identity_sha256": smoke._identity_digest(self.run),
            "evidence": {"catalog_epoch": 7, "catalog_revision": 12},
        }
        self.write("catalog-projectless-seeded.json", baseline)
        metrics = {
            identity: {
                "property_id": identity,
                "source": source,
                "category": category,
                "display_name": smoke._specs(self.plan)[kind]["name"],
            }
            for identity, (source, category, kind) in smoke._definitions(
                self.plan
            ).items()
        }
        metadata = self.evidence_fixture()[0]
        original_evidence = smoke._summary(
            self.plan, "seeded", *self.evidence_fixture()
        )
        for changes, succeeds in (
            ({}, True),
            ({"catalog_epoch": 8}, False),
            ({"catalog_revision": 11}, False),
        ):
            evidence = {**original_evidence, **changes}
            with (
                self.context(),
                patch.object(smoke, "_client", return_value=object()),
                patch.object(
                    smoke, "_metrics", side_effect=[(metrics, metadata), ({}, metadata)]
                ),
                patch.object(smoke, "_evidence", return_value=evidence),
                patch.object(smoke, "_checks", return_value=[]),
            ):
                if succeeds:
                    report = smoke.verify_restart(self.run, phase="seeded")
                    self.assertTrue(report["restart_probe_only"])
                else:
                    with self.assertRaisesRegex(RuntimeError, "restart changed epoch"):
                        smoke.verify_restart(self.run, phase="seeded")
            self.assertEqual(
                json.loads(
                    (self.run.directory / "catalog-projectless-seeded.json").read_text()
                ),
                baseline,
            )

    def test_runtime_identity_refuses_fifo_symlink_and_oversize(self):
        import os

        path = self.run.directory / "application-runtime/runtime-identity-v1.json"
        self.assertEqual(len(smoke._identity_digest(self.run)), 64)
        path.unlink()
        os.mkfifo(path)
        with self.assertRaisesRegex(RuntimeError, "regular file"):
            smoke._identity_digest(self.run)
        path.unlink()
        path.symlink_to(self.run.directory / "source-fixture.json")
        with self.assertRaises(OSError):
            smoke._identity_digest(self.run)
        path.unlink()
        path.write_bytes(b"x" * 65537)
        with self.assertRaisesRegex(RuntimeError, "size"):
            smoke._identity_digest(self.run)

    def test_post_rejection_is_uncertain_and_not_retried(self):
        self.add_project()
        session = Mock()
        session.post.return_value = SimpleNamespace(
            status_code=200, json=lambda: {"partialSuccess": {"rejectedSpans": "1"}}
        )
        with (
            self.context(),
            patch("ingestion_smoke.collector_state"),
            patch("requests.Session") as factory,
        ):
            factory.return_value.__enter__.return_value = session
            with self.assertRaisesRegex(RuntimeError, "not fully accepted"):
                smoke.ingest(self.run)
            with self.assertRaisesRegex(RuntimeError, "never repost"):
                smoke.ingest(self.run)
        self.assertEqual(session.post.call_count, 1)

    def test_source_has_only_normal_orm_and_one_otlp_writer_no_lifecycle_hooks(self):
        tree = ast.parse(Path(smoke.__file__).read_text())
        calls = [
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        self.assertEqual(calls.count("post"), 1)
        self.assertFalse(
            set(calls)
            & {
                "command",
                "bulk_create",
                "bulk_update",
                "force_authenticate",
                "execute_ch_query",
                "invalidate_source_snapshot",
            }
        )
        literal_strings = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        self.assertFalse(
            any(
                value.startswith(
                    ("INSERT ", "CREATE TABLE ", "ALTER ", "GRANT ", "DELETE ")
                )
                for value in literal_strings
            )
        )


if __name__ == "__main__":
    unittest.main()
