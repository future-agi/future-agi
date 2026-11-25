"""Explicit isolated A01 cloud suite; run with python -m tracer.tests.source_intent_cloud.

Uses real PostgreSQL, the real lifecycle lock function and two real local CH
servers. Only the four new models are migrated. No source schemas are migrated
or changed: fresh synthetic traces fixtures use the repository's 015 CREATE.
This is component/transport evidence, NOT a full-app/replica/producer certificate.
"""

from __future__ import annotations

import importlib
import io
import json
import os
import subprocess
import sys
import threading
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from django.apps import AppConfig
from django.conf import settings


class SourceTestConfig(AppConfig):
    name = "tracer"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        importlib.import_module("tracer.models.metric_source_intent")


def configure():
    if os.environ.get("A01_FULL_APP") == "1":
        # Integration runner uses the migrated real application, including in
        # the crash subprocess. Original isolated assertions remain unchanged.
        import django

        django.setup()
        settings.FI_SESSIONS_SOURCE_INTENTS_ENABLED = True
        settings.FI_SESSIONS_SOURCE_INTENT_LANE = "direct-local-mergetree-v1"
        settings.CLICKHOUSE_V2 = {
            **settings.CLICKHOUSE_V2,
            "CH25_HOST": "127.0.0.1",
            "CH25_HTTP_PORT": int(os.environ.get("A01_HTTP_PORT", "8123")),
            "CH25_DATABASE": os.environ.get("A01_DATABASE", "a01"),
        }
        return
    if not settings.configured:
        settings.configure(
            INSTALLED_APPS=["tracer.tests.source_intent_cloud.SourceTestConfig"],
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.postgresql",
                    "NAME": os.environ.get("A01_DATABASE", "a01"),
                    "USER": "root",
                    "HOST": "",
                    "CONN_MAX_AGE": 0,
                }
            },
            USE_TZ=True,
            DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
            FI_SESSIONS_SOURCE_INTENTS_ENABLED=True,
            FI_SESSIONS_SOURCE_INTENT_LANE="direct-local-mergetree-v1",
            CLICKHOUSE_V2={
                "CH25_HOST": "127.0.0.1",
                "CH25_HTTP_PORT": 8123,
                "CH25_DATABASE": os.environ.get("A01_DATABASE", "a01"),
            },
        )
        import django

        django.setup()


def fixture(scope=None, rows=None):
    from tracer.services.clickhouse.v2.trace_writer import (
        inspect_trace_source_store,
        trace_rows_sha256,
    )
    from tracer.services.derived_metrics.base_list_current_source import (
        CanonicalMembership,
        MemberImage,
        MembershipChange,
        MembershipPage,
        PhysicalIdentity,
        SourceScope,
        Store,
    )

    scope = scope or SourceScope(uuid4(), uuid4())
    now = datetime(2026, 11, 5, 0, 0, 0, tzinfo=UTC)
    rows = rows or (
        (
            str(uuid4()),
            str(scope.project_id),
            None,
            "synthetic",
            str(uuid4()),
            None,
            "[]",
            "{}",
            "",
            "",
            "",
            "PENDING",
            now,
            now,
            0,
            1,
        ),
    )
    images = tuple(
        MembershipChange(
            None,
            MemberImage(
                PhysicalIdentity(Store.TRACES, (str(scope.project_id), "202611", r[0])),
                CanonicalMembership(UUID(r[4]) if r[4] else None, None),
            ),
        )
        for r in rows
    )
    return (
        scope,
        rows,
        (MembershipPage(images),),
        inspect_trace_source_store(trace_rows_sha256(rows)),
    )


class SourceIntentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        configure()
        if os.environ.get("A01_FULL_APP") != "1":
            from django.db import connection
            from django.db.migrations.autodetector import MigrationAutodetector
            from django.db.migrations.questioner import (
                NonInteractiveMigrationQuestioner,
            )
            from django.db.migrations.state import ModelState, ProjectState

            from tracer.models.metric_source_intent import (
                MetricSourceDelivery,
                MetricSourceIntent,
                MetricSourcePage,
                MetricSourceScope,
            )

            migration = importlib.import_module(
                "tracer.migrations.0129_metric_source_intent"
            ).Migration("0129", "tracer")
            before, after = ProjectState(), ProjectState()
            migration.mutate_state(after, preserve=False)
            with connection.schema_editor() as editor:
                migration.apply(before, editor)
            # Exercise rollback BEFORE any pending operation exists; never erase an
            # uncertain operation to make a test pass.
            with connection.schema_editor() as editor:
                migration.unapply(ProjectState(), editor)
            with connection.schema_editor() as editor:
                migration.apply(ProjectState(), editor)
            live = ProjectState()
            for Model in (
                MetricSourceScope,
                MetricSourceIntent,
                MetricSourcePage,
                MetricSourceDelivery,
            ):
                live.add_model(ModelState.from_model(Model))
            diff = MigrationAutodetector(
                after, live, NonInteractiveMigrationQuestioner()
            )._detect_changes()
            if diff:
                raise AssertionError("migration/model drift: " + str(diff))
        import clickhouse_connect

        cls.clients = []
        schema = (
            Path(__file__).parents[1]
            / "services/clickhouse/v2/schema/015_traces_and_trace_dict.sql"
        )
        create = schema.read_text().split("CREATE TABLE IF NOT EXISTS traces", 1)[1]
        create = (
            create.split("SETTINGS index_granularity = 8192;", 1)[0]
            + "SETTINGS index_granularity = 8192"
        )
        database = os.environ.get("A01_DATABASE", "a01")
        for port in (
            int(os.environ.get("A01_HTTP_PORT", "8123")),
            int(os.environ.get("A01_SECOND_HTTP_PORT", "8124")),
        ):
            client = clickhouse_connect.get_client(host="127.0.0.1", port=port)
            client.command("CREATE DATABASE " + database)
            client.close()
            client = clickhouse_connect.get_client(
                host="127.0.0.1", port=port, database=database
            )
            client.command("CREATE TABLE IF NOT EXISTS traces" + create)
            cls.clients.append(client)

    def setUp(self):
        from tracer.services.clickhouse.v2 import trace_writer

        self.client_patch = patch.object(
            trace_writer, "_get_client", return_value=self.clients[0]
        )
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)

    def begin(self, scope=None, targets=None):
        from tracer.services.derived_metrics.base_list_current_source import (
            begin_source_intent,
        )

        scope, rows, pages, target = fixture(scope)
        token = begin_source_intent(
            scope, uuid4(), pages, expected_stores=targets or (target,)
        )
        return token, rows, pages, target

    def test_01_normal_real_delivery_and_duplicate_ack(self):
        from tracer.services.clickhouse.v2.trace_writer import insert_trace_rows
        from tracer.services.derived_metrics.base_list_current_source import (
            ProgressState,
            acknowledge_selected_server,
            read_source_page,
            source_progress,
        )

        token, rows, _, target = self.begin()
        self.assertEqual(source_progress(token.scope).state, ProgressState.PENDING)
        frozen = read_source_page(token, 0)
        receipt = insert_trace_rows(rows, source_intent=token, selected_store=target)
        progress = acknowledge_selected_server(token, (receipt, receipt))
        self.assertEqual((progress.revision, progress.closed_revision), (1, 1))
        self.assertEqual(progress.state, ProgressState.SETTLED)
        self.assertEqual(read_source_page(token, 0), frozen)
        self.assertEqual(
            self.clients[0]
            .query(
                "SELECT count() FROM traces WHERE id={id:UUID}",
                parameters={"id": rows[0][0]},
            )
            .result_rows[0][0],
            1,
        )

    def test_02_idempotency_changed_payload_and_old_new_pages(self):
        from tracer.services.derived_metrics.base_list_current_source import (
            SourceConflict,
            begin_source_intent,
            read_source_page,
            source_progress,
        )

        token, _, pages, target = self.begin()
        self.assertEqual(
            token,
            begin_source_intent(
                token.scope, token.operation_key, pages, expected_stores=(target,)
            ),
        )
        self.assertEqual(source_progress(token.scope).revision, 1)
        changed = replace(
            pages[0],
            changes=(replace(pages[0].changes[0], old=pages[0].changes[0].new),),
        )
        with self.assertRaises(SourceConflict):
            begin_source_intent(
                token.scope, token.operation_key, (changed,), expected_stores=(target,)
            )
        next_token = begin_source_intent(
            token.scope, uuid4(), (changed,), expected_stores=(target,)
        )
        parsed = json.loads(read_source_page(next_token, 0))
        self.assertIsNotNone(parsed["changes"][0]["old"])
        self.assertIsNotNone(parsed["changes"][0]["new"])

    def test_03_reordered_operations_and_scope_isolation(self):
        from tracer.services.clickhouse.v2.trace_writer import insert_trace_rows
        from tracer.services.derived_metrics.base_list_current_source import (
            SourceScope,
            source_progress,
        )

        a, ar, _, at = self.begin()
        b, br, _, bt = self.begin(a.scope)
        c, cr, _, ct = self.begin(SourceScope(uuid4(), a.scope.project_id))
        insert_trace_rows(br, source_intent=b, selected_store=bt)
        self.assertEqual(source_progress(a.scope).closed_revision, 0)
        insert_trace_rows(cr, source_intent=c, selected_store=ct)
        self.assertEqual(source_progress(c.scope).closed_revision, 1)
        self.assertEqual(source_progress(a.scope).closed_revision, 0)
        insert_trace_rows(ar, source_intent=a, selected_store=at)
        self.assertEqual(source_progress(a.scope).closed_revision, 2)

    def test_04_partial_selected_server_visibility(self):
        from django.test import override_settings

        from tracer.services.clickhouse.v2 import trace_writer
        from tracer.services.derived_metrics.base_list_current_source import (
            begin_source_intent,
            source_progress,
        )

        scope, rows, pages, first = fixture()
        second_config = {
            **settings.CLICKHOUSE_V2,
            "CH25_HTTP_PORT": int(os.environ.get("A01_SECOND_HTTP_PORT", "8124")),
        }
        with override_settings(CLICKHOUSE_V2=second_config):
            second = trace_writer.inspect_trace_source_store(
                trace_writer.trace_rows_sha256(rows)
            )
        token = begin_source_intent(
            scope, uuid4(), pages, expected_stores=(first, second)
        )
        trace_writer.insert_trace_rows(rows, source_intent=token, selected_store=first)
        self.assertEqual(source_progress(scope).closed_revision, 0)
        self.assertEqual(
            self.clients[1]
            .query(
                "SELECT count() FROM traces WHERE id={id:UUID}",
                parameters={"id": rows[0][0]},
            )
            .result_rows[0][0],
            0,
        )
        with override_settings(CLICKHOUSE_V2=second_config):
            trace_writer.insert_trace_rows(
                rows, source_intent=token, selected_store=second
            )
        self.assertEqual(source_progress(scope).closed_revision, 1)

    def test_05_lost_response_is_durable_and_cannot_retry_away(self):
        from django.db import connections

        from tracer.services.clickhouse.v2 import trace_writer
        from tracer.services.derived_metrics.base_list_current_source import (
            SourceUnavailable,
            source_progress,
        )

        token, rows, _, target = self.begin()
        from contextlib import contextmanager

        from tracer.services.derived_metrics import (
            base_list_current_source_transport as transport,
        )

        original_lease = transport.lease_trace_source_client

        @contextmanager
        def lost_lease():
            with original_lease() as (client, pool):
                actual = client.insert

                def lost(*args, **kwargs):
                    actual(*args, **kwargs)
                    raise ConnectionError(
                        "synthetic lost response after actual source commit"
                    )

                with patch.object(client, "insert", side_effect=lost):
                    yield client, pool

        with patch.object(transport, "lease_trace_source_client", lost_lease):
            with self.assertRaises(ConnectionError):
                trace_writer.insert_trace_rows(
                    rows, source_intent=token, selected_store=target
                )
        self.assertEqual(
            self.clients[0]
            .query(
                "SELECT count() FROM traces WHERE id={id:UUID}",
                parameters={"id": rows[0][0]},
            )
            .result_rows[0][0],
            1,
        )
        connections.close_all()
        self.assertEqual(source_progress(token.scope).closed_revision, 0)
        with self.assertRaises(SourceUnavailable):
            trace_writer.insert_trace_rows(
                rows, source_intent=token, selected_store=target
            )
        self.assertEqual(source_progress(token.scope).closed_revision, 0)

    def test_06_real_process_crash_before_dispatch_and_restart(self):
        from tracer.services.derived_metrics.base_list_current_source import (
            ProgressState,
            SourceScope,
            source_progress,
        )

        scope = SourceScope(uuid4(), uuid4())
        code = """
import os
from uuid import UUID, uuid4
from tracer.tests.source_intent_cloud import configure, fixture
configure()
from tracer.services.derived_metrics.base_list_current_source import SourceScope, begin_source_intent, claim_dispatch
scope, rows, pages, target = fixture(SourceScope(UUID(os.environ['ORG']), UUID(os.environ['PROJECT'])))
token = begin_source_intent(scope, uuid4(), pages, expected_stores=(target,))
claim_dispatch(token, target)
os._exit(29)
"""
        env = dict(
            os.environ, ORG=str(scope.organization_id), PROJECT=str(scope.project_id)
        )
        result = subprocess.run(
            [sys.executable, "-c", code], env=env, capture_output=True, timeout=30
        )
        self.assertEqual(result.returncode, 29, result.stderr.decode()[-2000:])
        progress = source_progress(scope)
        self.assertEqual(progress.state, ProgressState.PENDING)
        self.assertEqual((progress.revision, progress.closed_revision), (1, 0))
        self.assertEqual(
            self.clients[0]
            .query(
                "SELECT count() FROM traces WHERE project_id={id:UUID}",
                parameters={"id": str(scope.project_id)},
            )
            .result_rows[0][0],
            0,
        )

    def test_07_transaction_failure_rolls_back_registration(self):
        from django.db import connection

        from tracer.services.derived_metrics.base_list_current_source import (
            begin_source_intent,
            source_progress,
        )

        scope, _, pages, target = fixture()

        def fail_page(execute, sql, params, many, context):
            if sql.startswith('INSERT INTO "tracer_metricsourcepage"'):
                raise RuntimeError("synthetic page commit failure")
            return execute(sql, params, many, context)

        with connection.execute_wrapper(fail_page):
            with self.assertRaises(RuntimeError):
                begin_source_intent(scope, uuid4(), pages, expected_stores=(target,))
        self.assertIsNone(source_progress(scope))

    def test_08_outer_transaction_and_repeatable_read_unavailable(self):
        from django.db import connection, transaction

        from tracer.services.derived_metrics.base_list_current_source import (
            SourceUnavailable,
            begin_source_intent,
        )

        scope, _, pages, target = fixture()
        with transaction.atomic():
            with self.assertRaises(SourceUnavailable):
                begin_source_intent(scope, uuid4(), pages, expected_stores=(target,))
        with connection.cursor() as cursor:
            cursor.execute(
                "SET SESSION CHARACTERISTICS AS TRANSACTION ISOLATION LEVEL REPEATABLE READ"
            )
        try:
            with self.assertRaises(SourceUnavailable):
                begin_source_intent(scope, uuid4(), pages, expected_stores=(target,))
        finally:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET SESSION CHARACTERISTICS AS TRANSACTION ISOLATION LEVEL READ COMMITTED"
                )

    def test_09_malformed_unrelated_stale_receipt_rejected_atomically(self):
        from tracer.services.derived_metrics.base_list_current_source import (
            ReceiptKind,
            SelectedServerReceipt,
            SourceConflict,
            acknowledge_selected_server,
            claim_dispatch,
            source_progress,
        )

        a, _, _, at = self.begin()
        b, _, _, bt = self.begin()
        ticket = claim_dispatch(a, at)
        receipt = SelectedServerReceipt(
            ticket, ReceiptKind.LOCAL_SYNCHRONOUS_INSERT, str(ticket.receipt_id)
        )
        malformed = [
            True,
            replace(receipt, query_id="unrelated"),
            replace(receipt, ticket=replace(ticket, target=bt)),
            replace(receipt, ticket=replace(ticket, receipt_id=uuid4())),
            replace(receipt, ticket=replace(ticket, token=b)),
        ]
        for bad in malformed:
            with self.assertRaises(SourceConflict):
                acknowledge_selected_server(a, (receipt, bad))
            self.assertEqual(source_progress(a.scope).closed_revision, 0)
        with self.assertRaises(SourceConflict):
            acknowledge_selected_server(replace(a, epoch=uuid4()), (receipt,))
        self.assertEqual(source_progress(b.scope).closed_revision, 0)

    def test_10_bounds_and_cross_tenant_images(self):
        from tracer.services.derived_metrics.base_list_current_source import (
            CanonicalMembership,
            MemberImage,
            MembershipChange,
            MembershipPage,
            PhysicalIdentity,
            SourceConflict,
            Store,
            begin_source_intent,
        )

        scope, _, pages, target = fixture()
        image = pages[0].changes[0].new
        wrong = replace(
            image,
            physical=replace(
                image.physical, key=(str(uuid4()), "202611", str(uuid4()))
            ),
        )
        cases = [
            (),
            (MembershipPage(()),),
            (MembershipPage(pages[0].changes * 501),),
            (MembershipPage((MembershipChange(None, wrong),)),),
        ]
        for bad in cases:
            with self.assertRaises(SourceConflict):
                begin_source_intent(scope, uuid4(), bad, expected_stores=(target,))
        # Each key scalar fits its bound; the complete immutable operation does not.
        big = MemberImage(
            PhysicalIdentity(Store.SPANS, (str(scope.project_id), *(["x" * 4096] * 5))),
            CanonicalMembership(None, None),
        )
        large = MembershipPage((MembershipChange(big, big),) * 500)
        with self.assertRaises(SourceConflict):
            begin_source_intent(
                scope,
                uuid4(),
                (large,),
                expected_stores=(replace(target, store=Store.SPANS),),
            )

    def test_11_default_off_and_missing_intent_block(self):
        from django.test import override_settings

        from tracer.services.clickhouse.v2 import trace_writer
        from tracer.services.derived_metrics.base_list_current_source import (
            SourceUnavailable,
        )

        token, rows, _, target = self.begin()
        with self.assertRaises(SourceUnavailable):
            trace_writer.insert_trace_rows(rows)
        with override_settings(FI_SESSIONS_SOURCE_INTENTS_ENABLED=False):
            with self.assertRaises(SourceUnavailable):
                trace_writer.insert_trace_rows(
                    rows, source_intent=token, selected_store=target
                )
            # Legacy route stays usable when rollout is OFF, but is not enrolled.
            trace_writer.insert_trace_rows(rows)

    def test_12_concurrent_registration_serializes_on_real_lifecycle_lock(self):
        from django.db import connections, transaction

        from tracer.services.derived_metrics.base_list_current_source import (
            begin_source_intent,
            source_progress,
        )
        from tracer.services.derived_metrics.deletion_protocol import project_lock

        scope, _, pages, target = fixture()
        started, finished = threading.Event(), threading.Event()
        errors, tokens = [], []

        def register():
            try:
                started.set()
                tokens.append(
                    begin_source_intent(
                        scope, uuid4(), pages, expected_stores=(target,)
                    )
                )
            except BaseException as exc:
                errors.append(type(exc).__name__)
            finally:
                connections.close_all()
                finished.set()

        with transaction.atomic():
            project_lock(scope.project_id)
            thread = threading.Thread(target=register)
            thread.start()
            self.assertTrue(started.wait(5))
            self.assertFalse(finished.wait(0.2))
        self.assertTrue(finished.wait(10))
        thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(source_progress(scope).revision, 1)

    def test_13_no_source_cascade_and_epoch_identity(self):
        from django.db import connections
        from django.db.models.deletion import ProtectedError

        from tracer.models.metric_source_intent import MetricSourceScope
        from tracer.services.derived_metrics.base_list_current_source import (
            source_progress,
        )

        token, _, _, _ = self.begin()
        with self.assertRaises(ProtectedError):
            MetricSourceScope.objects.get(epoch=token.epoch).delete()
        connections.close_all()
        self.assertEqual(source_progress(token.scope).epoch, token.epoch)
        self.assertEqual(source_progress(token.scope).closed_revision, 0)

    def test_14_wrong_frozen_rows_never_dispatch(self):
        from tracer.services.clickhouse.v2 import trace_writer
        from tracer.services.derived_metrics.base_list_current_source import (
            SourceConflict,
            source_progress,
        )

        token, rows, _, target = self.begin()
        changed = ((*rows[0][:3], "changed", *rows[0][4:]),)
        with self.assertRaises(SourceConflict):
            trace_writer.insert_trace_rows(
                changed, source_intent=token, selected_store=target
            )
        self.assertEqual(source_progress(token.scope).closed_revision, 0)

    def test_15_driver_hidden_retry_is_blocked_before_second_send(self):
        from http.client import RemoteDisconnected

        from urllib3 import PoolManager
        from urllib3.exceptions import ProtocolError

        from tracer.services.clickhouse.v2.trace_writer import insert_trace_rows
        from tracer.services.derived_metrics.base_list_current_source import (
            SourceUnavailable,
            source_progress,
        )

        token, rows, _, target = self.begin()
        original = PoolManager.request
        insert_calls = []

        def lose_response(pool, method, url, **kwargs):
            response = original(pool, method, url, **kwargs)
            if getattr(pool, "expected_query_id", None) is not None:
                insert_calls.append(url)
                response.read()
                response.close()
                response.release_conn()
                try:
                    raise RemoteDisconnected("synthetic loss after real insert")
                except RemoteDisconnected as exc:
                    raise ProtocolError("synthetic loss") from exc
            return response

        with patch.object(PoolManager, "request", lose_response):
            with self.assertRaises(SourceUnavailable):
                insert_trace_rows(rows, source_intent=token, selected_store=target)
        self.assertEqual(len(insert_calls), 1)
        self.assertEqual(source_progress(token.scope).closed_revision, 0)
        self.assertEqual(
            self.clients[0]
            .query(
                "SELECT count() FROM traces WHERE id={id:UUID}",
                parameters={"id": rows[0][0]},
            )
            .result_rows[0][0],
            1,
        )

    def test_16_http_200_late_exception_cannot_ack(self):
        from urllib3 import PoolManager

        from tracer.services.clickhouse.v2.trace_writer import insert_trace_rows
        from tracer.services.derived_metrics.base_list_current_source import (
            SourceUnavailable,
            source_progress,
        )

        token, rows, _, target = self.begin()
        original = PoolManager.request

        def late_body(pool, method, url, **kwargs):
            response = original(pool, method, url, **kwargs)
            if getattr(pool, "expected_query_id", None) is not None:
                response.read()  # actual source dispatch has finished
                response.read = lambda *args, **kw: (
                    b"Code: 999. synthetic late exception"
                )
            return response

        with patch.object(PoolManager, "request", late_body):
            with self.assertRaises(SourceUnavailable):
                insert_trace_rows(rows, source_intent=token, selected_store=target)
        self.assertEqual(source_progress(token.scope).closed_revision, 0)

    def test_17_explicit_tombstone_with_immutable_preimage(self):
        from tracer.services.clickhouse.v2.trace_writer import (
            insert_trace_rows,
            inspect_trace_source_store,
            trace_rows_sha256,
        )
        from tracer.services.derived_metrics.base_list_current_source import (
            MembershipChange,
            MembershipPage,
            MemberState,
            begin_source_intent,
            read_source_page,
            source_progress,
        )

        first, rows, pages, target = self.begin()
        insert_trace_rows(rows, source_intent=first, selected_store=target)
        deleted = ((*rows[0][:14], 1, 2),)
        old = pages[0].changes[0].new
        changes = (
            MembershipPage(
                (MembershipChange(old, replace(old, state=MemberState.TOMBSTONE)),)
            ),
        )
        selected = inspect_trace_source_store(trace_rows_sha256(deleted))
        token = begin_source_intent(
            first.scope, uuid4(), changes, expected_stores=(selected,)
        )
        insert_trace_rows(deleted, source_intent=token, selected_store=selected)
        self.assertEqual(source_progress(first.scope).closed_revision, 2)
        page = json.loads(read_source_page(token, 0))["changes"][0]
        self.assertEqual(
            (page["old"]["state"], page["new"]["state"]), ("live", "tombstone")
        )
        self.assertEqual(
            self.clients[0]
            .query(
                "SELECT count() FROM traces WHERE id={id:UUID} AND is_deleted=1",
                parameters={"id": rows[0][0]},
            )
            .result_rows[0][0],
            1,
        )

    def test_18_recreated_empty_control_scope_cannot_aba(self):
        from tracer.models.metric_source_intent import MetricSourceScope
        from tracer.services.derived_metrics.base_list_current_source import (
            SourceScope,
            source_progress,
        )

        scope = SourceScope(uuid4(), uuid4())
        original = MetricSourceScope.objects.create(
            organization_id=scope.organization_id, project_id=scope.project_id
        )
        before = source_progress(scope)
        # Adversarial control-state recreation, with NO intent ever registered.
        # No pending/unknown operation is deleted or reset by this test.
        original.delete()
        token, _, _, _ = self.begin(scope)
        self.assertNotEqual(before.epoch, token.epoch)
        self.assertEqual(source_progress(scope).closed_revision, 0)


def main():
    configure()
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(SourceIntentTests)
    )
    from importlib.metadata import version

    from django.db import connection

    from tracer.models.metric_source_intent import MetricSourceDelivery

    report = {
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "passed": result.wasSuccessful(),
        "test_output": stream.getvalue(),
        "postgres_version": connection.pg_version,
        "driver_version": version("clickhouse-connect"),
        "django_version": version("Django"),
        "clickhouse_versions": [
            c.query("SELECT version()").result_rows[0][0]
            for c in SourceIntentTests.clients
        ],
        "pending_preserved": MetricSourceDelivery.objects.filter(
            state="uncertain"
        ).count(),
        "prepared_preserved": MetricSourceDelivery.objects.filter(
            state="prepared"
        ).count(),
        "limitations": [
            "isolated new-model migration only; not full application migration graph",
            "two real selected local servers, not replicated topology qualification",
            "no fixture or journal progress can qualify Sessions",
        ],
    }
    Path("/tmp/a01-result.json").write_text(json.dumps(report, sort_keys=True))
    print(stream.getvalue())
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
