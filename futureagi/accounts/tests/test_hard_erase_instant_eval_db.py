"""DB-REQUIRED: an organization hard erase removes its Instant eval rows.

InstantEvalRow.task has no database constraint, so PostgreSQL never cascades a
removed eval task to the row table. Only Django's Collector, behind the
hard-erase boundary, removes those rows. This test erases a real organization
through the admin's delete and reads the row table back.
"""

import uuid

from django.contrib.admin.sites import AdminSite

from accounts.admin import OrganizationAdmin
from accounts.models import Organization
from accounts.tests import test_hard_erase_db as support
from model_hub.models.ai_model import AIModel
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
from tracer.models.instant_eval import InstantEvalRow
from tracer.models.project import Project


class InstantEvalEraseDBTests(support.IsolatedEraseTestCase):
    setUp = support.HardEraseDBTests.setUp
    person = support.HardEraseDBTests.person
    raw = support.HardEraseDBTests.raw

    def instant_row(self, org):
        project = Project.objects.create(
            name=f"erase-instant-{uuid.uuid4()}",
            organization=org,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        task = EvalTask.objects.create(
            project=project,
            name="Instant eval · question",
            filters={},
            sampling_rate=100,
            run_type=RunType.HISTORICAL,
            row_type="sessions",
            status=EvalTaskStatus.RUNNING,
        )
        return InstantEvalRow.objects.create(
            task=task,
            row_id=str(uuid.uuid4()),
            unit="sessions",
            end_user_id=uuid.uuid4(),
            user_label="Erase fixture person",
            excerpt="private excerpt",
        )

    def test_organization_erase_removes_its_instant_eval_rows(self):
        self.assertEqual(InstantEvalRow._meta.db_table, "tracer_instant_eval_row")
        org = Organization.objects.create(name=f"erase-instant-{uuid.uuid4()}")
        row = self.instant_row(org)
        kept = self.instant_row(self.o2)
        org_id, row_id, task_id = org.pk, row.pk, row.task_id
        self.assertIsNotNone(row.end_user_id)
        self.assertEqual(row.user_label, "Erase fixture person")
        self.assertTrue(self.raw(InstantEvalRow).filter(pk=row_id).exists())

        # The operator's route: the admin's delete calls Model.delete, whose
        # guarded Collector is the only thing that can remove this row.
        OrganizationAdmin(Organization, AdminSite()).delete_model(None, org)

        self.assertFalse(self.raw(Organization).filter(pk=org_id).exists())
        self.assertFalse(InstantEvalRow.objects.filter(pk=row_id).exists())
        # The unfiltered table, by the row's id and by its erased task's id.
        self.assertFalse(self.raw(InstantEvalRow).filter(pk=row_id).exists())
        self.assertFalse(self.raw(InstantEvalRow).filter(task_id=task_id).exists())
        # Another organization's row stays.
        self.assertTrue(self.raw(InstantEvalRow).filter(pk=kept.pk).exists())
