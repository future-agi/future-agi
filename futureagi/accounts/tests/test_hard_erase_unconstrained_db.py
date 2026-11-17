"""Database-required organization and user erasure with absent telemetry links."""

import uuid

from accounts.models import Organization, User
from accounts.services.hard_erase import HardEraseRefused, authorized_scope, locked_preflight
from accounts.tests import test_hard_erase_db as support
from model_hub.models.annotation_queues import AnnotationQueue, QueueItem
from model_hub.models.choices import QueueItemSourceType
from tracer.models.observation_span import ObservationSpan
from tracer.models.span_notes import SpanNotes
from tracer.models.trace import Trace


class UnconstrainedEraseDBTests(support.IsolatedEraseTestCase):
    setUp = support.HardEraseDBTests.setUp
    person = support.HardEraseDBTests.person
    raw = support.HardEraseDBTests.raw

    def test_p4_organization_erase_with_missing_trace_completes(self):
        org = Organization.objects.create(name=f"Absent telemetry {uuid.uuid4().hex}")
        queue = AnnotationQueue.objects.create(name="Erase closure", organization=org)
        missing_trace = uuid.uuid4()
        self.assertFalse(self.raw(Trace).filter(pk=missing_trace).exists())
        item = QueueItem.objects.create(
            queue=queue,
            organization=org,
            source_type=QueueItemSourceType.TRACE.value,
            trace_id=missing_trace,
        )
        org_pk, queue_pk, item_pk = org.pk, queue.pk, item.pk

        # Same reviewed entry point used by the existing organization fixture proof.
        with locked_preflight([org], using="default", origin=org) as collector:
            collector.delete()

        self.assertFalse(self.raw(Organization).filter(pk=org_pk).exists())
        self.assertFalse(self.raw(AnnotationQueue).filter(pk=queue_pk).exists())
        self.assertFalse(self.raw(QueueItem).filter(pk=item_pk).exists())
        self.assertTrue(self.raw(Organization).filter(pk=self.o2.pk).exists())

    def test_n2_user_erase_with_only_missing_span_path_refuses(self):
        missing_span = uuid.uuid4()
        self.assertFalse(self.raw(ObservationSpan).filter(pk=missing_span).exists())
        note = SpanNotes.objects.create(
            span_id=missing_span,
            notes="Must survive refusal",
            created_by_user=self.u,
            created_by_annotator="Fixture author",
        )
        before = list(self.raw(SpanNotes).filter(pk=note.pk).values())
        for delivery in self.delivery:
            delivery.reset_mock()

        # Authorizing the user's legacy org must not turn an unowned note into O1 data.
        with authorized_scope([self.o1.pk], using="default"):
            with self.assertRaisesMessage(
                HardEraseRefused, "an ownership reference cannot be resolved"
            ):
                self.raw(User).filter(pk=self.u.pk).delete()

        self.assertTrue(self.raw(User).filter(pk=self.u.pk).exists())
        self.assertEqual(list(self.raw(SpanNotes).filter(pk=note.pk).values()), before)
        for delivery in self.delivery:
            delivery.assert_not_called()
