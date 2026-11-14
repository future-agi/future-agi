"""Stdlib orchestration and in-memory ORM-double tests, NOT PostgreSQL proof.

The actual checked-in container programs execute against doubles here. FK,
transaction and migration behaviour still require the real local product lane.
"""

import ast
import copy
import io
import json
import unittest
import uuid
from contextlib import contextmanager, redirect_stdout
from types import SimpleNamespace
from unittest import mock

from scripts import rehearse_update_compatibility as p
from scripts import update_rehearsal_qualification as q
from scripts import update_rehearsal_shipping as s

ENTRY = {
    "database": "postgres",
    "app_label": "prism",
    "model": "AgentccWebhookEvent",
    "table": "agentcc_webhook_event",
}
ORG = "11111111-1111-4111-8111-111111111111"
OTHER_ORG = "22222222-2222-4222-8222-222222222222"
NAMESPACE = "fagi-upd-e2e-release-source-webhook-test"


class Query:
    def __init__(self, model, filters=None):
        self.model = model
        self.filters = filters or {}

    def filter(self, **kwargs):
        return Query(self.model, dict(self.filters, **kwargs))

    def matches(self, row):
        for key, expected in self.filters.items():
            value = row
            for part in key.split("__"):
                value = getattr(value, part)
            if str(value) != str(expected):
                return False
        return True

    def values(self):
        return [row for row in self.model.rows if self.matches(row)]

    def first(self):
        return next(iter(self.values()), None)

    def get(self, **kwargs):
        found = self.filter(**kwargs).values()
        if len(found) != 1:
            raise LookupError("missing or ambiguous fixture relation")
        return found[0]

    def count(self):
        return len(self.values())

    def order_by(self, *args):
        return self

    def create(self, **kwargs):
        row = self.model(**kwargs)
        row.save(force_insert=True)
        return row

    def bulk_create(self, batch, **kwargs):
        self.model.rows.extend(batch)


class MemoryORM:
    """Deliberately small test double: does not model database constraints."""

    def __init__(self):
        self.calls = []
        self.models = {}
        for label, name, table in (
            ("accounts", "Organization", "accounts_organization"),
            ("prism", "AgentccWebhook", "agentcc_webhook"),
            ("prism", "AgentccWebhookEvent", "agentcc_webhook_event"),
            ("prism", "AgentccShadowResult", "agentcc_shadow_result"),
        ):
            pk = SimpleNamespace(
                name="id",
                attname="id",
                unique=True,
                primary_key=True,
                get_internal_type=lambda: "UUIDField",
            )
            model = type(
                name,
                (),
                {
                    "rows": [],
                    "_meta": SimpleNamespace(
                        db_table=table,
                        managed=True,
                        pk=pk,
                        concrete_fields=[pk],
                        unique_together=(),
                        constraints=[],
                    ),
                },
            )

            def initialize(obj, **kwargs):
                obj.__dict__.update(
                    id=uuid.uuid4(),
                    deleted=False,
                    deleted_at=None,
                    _state=SimpleNamespace(adding=True),
                )
                obj.__dict__.update(kwargs)

            def save(obj, **kwargs):
                if any(str(row.pk) == str(obj.pk) for row in type(obj).rows):
                    raise ValueError("duplicate primary key")
                type(obj).rows.append(obj)

            model.__init__ = initialize
            model.save = save
            model.full_clean = lambda obj: None
            model.pk = property(lambda obj: obj.id)
            model.objects = model.all_objects = Query(model)
            self.models[label, name] = model
        self.organization = self.models["accounts", "Organization"]
        self.hook = self.models["prism", "AgentccWebhook"]
        self.event = self.models["prism", "AgentccWebhookEvent"]
        self.event.webhook = property(
            lambda obj: self.hook.objects.get(pk=obj.webhook_id)
        )
        for model in (self.hook, self.event):
            model._meta.get_field = lambda name: SimpleNamespace(
                many_to_one=True,
                db_constraint=True,
                remote_field=SimpleNamespace(
                    model=self.organization if name == "organization" else self.hook
                ),
            )
        self.organization.objects.create(id=uuid.UUID(ORG), name=NAMESPACE)
        self.organization.objects.create(id=uuid.UUID(OTHER_ORG), name="other")

    @contextmanager
    def atomic(self):
        before = {key: copy.deepcopy(model.rows) for key, model in self.models.items()}
        try:
            yield
        except Exception:
            for key, model in self.models.items():
                model.rows[:] = before[key]
            raise

    def __call__(self, program, args):
        self.calls.append((program, copy.deepcopy(args)))
        assert program.startswith(s.DJANGO_PREFIX)
        apps = SimpleNamespace(get_model=lambda app, model: self.models[app, model])
        modules = {
            "django.apps": SimpleNamespace(apps=apps),
            "django.db": SimpleNamespace(
                transaction=SimpleNamespace(atomic=self.atomic)
            ),
        }
        output = io.StringIO()
        with mock.patch.dict("sys.modules", modules), redirect_stdout(output):
            # Execute only our checked-in producer programs, never external input.
            exec(program[len(s.DJANGO_PREFIX) :], {"a": args, "json": json})  # noqa: S102
        return json.loads(output.getvalue())


def driver_for(fake=None, cls=s.ShippingRehearsal):
    driver = object.__new__(cls)
    driver.app_python = fake if fake is not None else MemoryORM()
    driver.witnesses = SimpleNamespace(
        namespaces={
            "source": {
                "namespace": NAMESPACE,
                "headers": {
                    "X-Organization-Id": ORG,
                    "Authorization": "Bearer NOT-FOR-FIXTURE",
                    "X-Api-Key": "SECRET",
                },
            }
        }
    )
    driver.corpus = {}
    return driver


class WebhookEventReferenceTests(unittest.TestCase):
    def test_owned_sample_selection_never_falls_back_to_another_event(self):
        fake = MemoryORM()
        driver_for(fake).seed_reference_table(ENTRY, 10)
        arguments = fake.calls[-1][1]
        for field in ("event_id", "webhook_id", "organization_id"):
            altered = dict(arguments["sample_webhook_event"], **{field: OTHER_ORG})
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(
                    AssertionError, "reference_seed_operation_unavailable"
                ),
            ):
                fake(
                    s.REFERENCE_SEED,
                    dict(arguments, rows=20, sample_webhook_event=altered),
                )
        with self.assertRaisesRegex(
            AssertionError, "reference_seed_operation_unavailable"
        ):
            fake(s.REFERENCE_SEED, dict(ENTRY, rows=20))
        self.assertEqual(len(fake.event.rows), 10)

    def test_fixture_creation_is_atomic_on_event_identity_collision(self):
        fake = MemoryORM()
        namespace = uuid.uuid5(
            uuid.NAMESPACE_URL,
            "futureagi-qualification-webhook-event:" + ORG + ":" + NAMESPACE,
        )
        event = fake.event.objects.create(
            id=uuid.uuid5(namespace, "event"), organization_id=uuid.UUID(OTHER_ORG)
        )
        with self.assertRaisesRegex(AssertionError, "owned fixture differs"):
            driver_for(fake).seed_reference_table(ENTRY, 10)
        self.assertEqual(fake.hook.rows, [])
        self.assertEqual(len(fake.event.rows), 1)
        self.assertEqual(fake.event.rows[0].id, event.id)
        self.assertEqual(fake.event.rows[0].organization_id, uuid.UUID(OTHER_ORG))

    def test_source_schema_relation_drift_fails_before_creating_rows(self):
        fake = MemoryORM()
        for property_name, value in (
            ("many_to_one", False),
            ("db_constraint", False),
            ("remote_field", SimpleNamespace(model=fake.event)),
        ):
            field = fake.hook._meta.get_field("organization")
            setattr(field, property_name, value)
            with (
                self.subTest(property_name=property_name),
                mock.patch.object(fake.hook._meta, "get_field", return_value=field),
                self.assertRaisesRegex(AssertionError, "FK drift"),
            ):
                driver_for(fake).seed_reference_table(ENTRY, 10)
            self.assertEqual(fake.hook.rows, [])
            self.assertEqual(fake.event.rows, [])

    def test_bad_preparation_or_expansion_results_cannot_claim_success(self):
        for value in (None, {}, {"event_id": "unowned"}):
            driver = driver_for(mock.Mock(return_value=value))
            with self.subTest(prepared=value), self.assertRaises(p.ManualRelease):
                driver.seed_reference_table(ENTRY, 10)
            self.assertEqual(driver.app_python.call_count, 1)
            self.assertEqual(driver.corpus, {})
        for value in (
            None,
            {},
            {"seeded_rows": True},
            {"seeded_rows": 9},
            {"seeded_rows": 10.0},
        ):
            fake = MemoryORM()
            driver = driver_for(
                lambda program, args, value=value, fake=fake: (
                    value if program == s.REFERENCE_SEED else fake(program, args)
                )
            )
            with self.subTest(expanded=value), self.assertRaises(p.ManualRelease):
                driver.seed_reference_table(ENTRY, 10)
            self.assertEqual(driver.corpus, {})

    def test_already_at_requested_scale_preserves_all_existing_rows(self):
        fake = MemoryORM()
        hook = fake.hook.objects.create(organization_id=OTHER_ORG, is_active=True)
        for _ in range(10):
            fake.event.objects.create(
                organization_id=OTHER_ORG,
                webhook_id=hook.id,
                payload={"preexisting": True},
                status="pending",
            )
        before = copy.deepcopy([row.__dict__ for row in fake.event.rows])
        driver_for(fake).seed_reference_table(ENTRY, 10)
        self.assertEqual(len(fake.event.rows), 11)
        self.assertEqual([row.__dict__ for row in fake.event.rows[:10]], before)
        self.assertTrue(fake.event.rows[-1].payload["qualification_fixture"])

    def test_zero_row_source_creates_owned_relations_before_expansion(self):
        for cls in (s.ShippingRehearsal, q.QualificationRehearsal):
            with self.subTest(driver=cls.__name__):
                fake = MemoryORM()
                driver = driver_for(fake, cls)
                driver.seed_reference_table(ENTRY, 1000)
                self.assertEqual(len(fake.event.rows), 1000)
                self.assertEqual(len(fake.hook.rows), 1)
                hook = fake.hook.rows[0]
                self.assertFalse(hook.is_active)
                self.assertEqual(str(hook.organization_id), ORG)
                for event in fake.event.rows:
                    self.assertEqual(str(event.organization_id), ORG)
                    self.assertEqual(event.webhook_id, hook.id)
                    self.assertEqual(event.status, "delivered")
                    self.assertTrue(event.payload["qualification_fixture"])
                self.assertEqual(fake.calls[-1][0], s.REFERENCE_SEED)
                self.assertEqual(len({row.id for row in fake.event.rows}), 1000)

    def test_populated_source_is_preserved_and_never_selected_for_cloning(self):
        fake = MemoryORM()
        hook = fake.hook.objects.create(organization_id=OTHER_ORG, is_active=True)
        unrelated = fake.event.objects.create(
            organization_id=OTHER_ORG,
            webhook_id=hook.id,
            status="pending",
            payload={"private": "do-not-copy"},
        )
        before = copy.deepcopy(unrelated.__dict__)
        driver_for(fake).seed_reference_table(ENTRY, 1000)
        self.assertEqual(unrelated.__dict__, before)
        self.assertEqual(len(fake.event.rows), 1000)
        self.assertEqual(
            sum(row.organization_id == OTHER_ORG for row in fake.event.rows), 1
        )
        self.assertTrue(
            all(
                row.payload.get("qualification_fixture")
                for row in fake.event.rows
                if row is not unrelated
            )
        )

    def test_repeated_and_fresh_driver_replay_add_no_rows_or_mutations(self):
        fake = MemoryORM()
        driver = driver_for(fake)
        driver.seed_reference_table(ENTRY, 1000)
        before = copy.deepcopy([row.__dict__ for row in fake.event.rows])
        driver.seed_reference_table(ENTRY, 1000)
        driver_for(fake).seed_reference_table(ENTRY, 1000)
        self.assertEqual([row.__dict__ for row in fake.event.rows], before)
        self.assertEqual(len(fake.hook.rows), 1)

    def test_existing_sample_is_not_overwritten_after_tampering(self):
        for model, attribute, value in (
            ("hook", "organization_id", OTHER_ORG),
            ("hook", "is_active", True),
            ("hook", "deleted", True),
            ("event", "payload", {"not": "owned"}),
            ("event", "organization_id", OTHER_ORG),
            ("event", "status", "pending"),
            ("event", "deleted", True),
        ):
            fake = MemoryORM()
            driver = driver_for(fake)
            driver.seed_reference_table(ENTRY, 1000)
            row = getattr(fake, model).rows[0]
            setattr(row, attribute, value)
            with (
                self.subTest(model=model, attribute=attribute),
                self.assertRaises(AssertionError),
            ):
                driver.seed_reference_table(ENTRY, 2000)
            self.assertEqual(len(fake.event.rows), 1000)
            self.assertEqual(getattr(row, attribute), value)

    def test_missing_organization_fails_without_creating_support_rows(self):
        fake = MemoryORM()
        fake.organization.rows.clear()
        with self.assertRaises(LookupError):
            driver_for(fake).seed_reference_table(ENTRY, 1000)
        self.assertEqual(fake.hook.rows, [])
        self.assertEqual(fake.event.rows, [])

    def test_generic_no_sample_and_constraint_refusals_remain(self):
        fake = MemoryORM()
        entry = {
            "database": "postgres",
            "app_label": "prism",
            "model": "AgentccShadowResult",
            "table": "agentcc_shadow_result",
        }
        with self.assertRaisesRegex(
            AssertionError, "reference_seed_operation_unavailable"
        ):
            fake(s.REFERENCE_SEED, dict(entry, rows=1000))
        model = fake.models["prism", "AgentccShadowResult"]
        model.objects.create()
        for attribute, value in (
            ("unique_together", (("organization", "name"),)),
            ("constraints", [SimpleNamespace(name="unknown")]),
            ("concrete_fields", [SimpleNamespace(unique=True, primary_key=False)]),
        ):
            with (
                mock.patch.object(model._meta, attribute, value),
                self.assertRaises(AssertionError),
            ):
                fake(s.REFERENCE_SEED, dict(entry, rows=1000))
        self.assertEqual(len(model.rows), 1)

    def test_dispatch_rejects_mismatched_registry_identities(self):
        driver = driver_for(mock.Mock())
        bad = [
            dict(ENTRY, **{key: value})
            for key, value in (
                ("database", "sqlite"),
                ("app_label", "agentcc"),
                ("model", "Unknown"),
                ("model", "AgentccShadowResult"),
                ("table", "unreviewed"),
            )
        ]
        for entry in bad:
            with self.subTest(entry=entry), self.assertRaises(p.ManualRelease):
                driver.seed_reference_table(entry, 1000)
        driver.app_python.assert_not_called()

    def test_bad_context_and_rows_fail_before_product_calls(self):
        for source in (
            None,
            {},
            {"namespace": NAMESPACE, "headers": {}},
            {"namespace": NAMESPACE, "headers": {"X-Organization-Id": "not-uuid"}},
        ):
            driver = driver_for(mock.Mock())
            driver.witnesses.namespaces["source"] = source
            with self.subTest(source=source), self.assertRaises(p.ManualRelease):
                driver.seed_reference_table(ENTRY, 1000)
            driver.app_python.assert_not_called()
        for rows in (True, 0, -1, 100000001, 1.5, "1000"):
            driver = driver_for(mock.Mock())
            with self.subTest(rows=rows), self.assertRaises(p.ManualRelease):
                driver.seed_reference_table(ENTRY, rows)
            driver.app_python.assert_not_called()

    def test_failure_never_records_success_and_secrets_are_not_forwarded(self):
        fake = MemoryORM()
        driver = driver_for(fake)
        driver.seed_reference_table(ENTRY, 1000)
        self.assertEqual(len(driver.corpus["reference_seed_operations"]), 1)
        serialized = json.dumps([args for program, args in fake.calls]) + json.dumps(
            driver.corpus
        )
        for secret in ("Authorization", "X-Api-Key", "NOT-FOR-FIXTURE", "SECRET"):
            self.assertNotIn(secret, serialized)
        driver = driver_for(mock.Mock(side_effect=RuntimeError("fixture failed")))
        with self.assertRaises(RuntimeError):
            driver.seed_reference_table(ENTRY, 1000)
        self.assertNotIn("reference_seed_operations", driver.corpus)

    def test_fixture_uses_no_destructive_writes_or_network_calls(self):
        from scripts.update_rehearsal_webhook_event_reference import (
            WEBHOOK_EVENT_REFERENCE_PREPARE,
        )

        program = s.DJANGO_PREFIX + WEBHOOK_EVENT_REFERENCE_PREPARE
        compile(program, "<webhook-fixture>", "exec")
        tree = ast.parse(program)
        forbidden = {
            "delete",
            "update",
            "update_or_create",
            "bulk_update",
            "execute",
            "post",
            "get_or_create",
        }
        self.assertFalse(
            any(
                isinstance(node, ast.Attribute) and node.attr in forbidden
                for node in ast.walk(tree)
            )
        )
        self.assertNotIn("ignore_conflicts", program)


if __name__ == "__main__":
    unittest.main()
