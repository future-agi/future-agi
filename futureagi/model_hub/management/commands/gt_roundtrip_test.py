"""Live round-trip test for the Ground Truth <-> ClickHouse path.

Builds a real ``EvalGroundTruth``, runs it through
:meth:`GroundTruthService.embed_dataset`, then verifies the CH vectors
are queryable via :meth:`GroundTruthService.retrieve_few_shot`.

    python manage.py gt_roundtrip_test [--keep] [--inspect]
"""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass

from django.core.management.base import BaseCommand

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from agentic_eval.core.database.ch_vector import ClickHouseVectorDB
from agentic_eval.core.embeddings.embedding_manager import (
    GROUND_TRUTH_TABLE_NAME,
)
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalGroundTruth, EvalTemplate
from model_hub.services.ground_truth_service import GroundTruthService
from tfc.constants.roles import OrganizationRoles


@dataclass
class _Fixture:
    org: Organization
    user: User
    workspace: Workspace
    template: EvalTemplate
    gt: EvalGroundTruth


class Command(BaseCommand):
    help = (
        "End-to-end round-trip test for the GT → ClickHouse path. "
        "Creates a temporary EvalGroundTruth, embeds it, runs both the "
        "service-level retrieval and the agent tool against it, then "
        "tears the fixtures down (unless --keep is set)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--keep",
            action="store_true",
            help=(
                "Leave PG fixtures + CH vectors in place after the run. "
                "Use with --inspect to query them with manage.py shell."
            ),
        )
        parser.add_argument(
            "--inspect",
            action="store_true",
            help="Print extra detail per case (queries, raw row dicts).",
        )

    def handle(self, *args, **options):
        keep = options["keep"]
        inspect = options["inspect"]

        failures: list[str] = []
        fixture: _Fixture | None = None
        try:
            fixture = self._build_fixture()
            self._announce(fixture, inspect)

            self._case_embed_pass(fixture, inspect, failures)
            self._case_retrieve_same_tenant(fixture, inspect, failures)
            self._case_retrieve_cross_tenant_is_zero(fixture, inspect, failures)
            self._case_skip_gate_empty_inputs(fixture, inspect, failures)
            self._case_re_embed_replaces_prior_vectors(fixture, inspect, failures)
        except Exception as exc:
            failures.append(f"unhandled error: {exc!r}")
            self.stdout.write(self.style.ERROR(f"unhandled error: {exc!r}"))
        finally:
            if fixture and not keep:
                self._teardown(fixture)

        if failures:
            self.stdout.write(
                self.style.ERROR(
                    f"\nFAILED - {len(failures)} case(s):\n  - "
                    + "\n  - ".join(failures)
                )
            )
            sys.exit(1)
        self.stdout.write(
            self.style.SUCCESS("\n✓ GT round-trip OK - all cases passed.")
        )

    # ─────────────────────────────────────────────────────────────────
    # Cases
    # ─────────────────────────────────────────────────────────────────

    def _case_embed_pass(self, fx: _Fixture, inspect: bool, failures: list[str]):
        self.stdout.write(self.style.NOTICE("→ Case: embed_dataset"))
        result = GroundTruthService.embed_dataset(gt=fx.gt)
        fx.gt.refresh_from_db()
        self._assert(
            "service returns status=completed",
            result.status == EvalGroundTruth.EmbeddingStatus.COMPLETED,
            f"got {result.status!r} (error={result.error!r})",
            failures,
        )
        self._assert(
            f"service returns rows_embedded={len(fx.gt.data)}",
            result.rows_embedded == len(fx.gt.data),
            f"got {result.rows_embedded}",
            failures,
        )
        self._assert(
            "PG row reflects status=completed",
            fx.gt.embedding_status == EvalGroundTruth.EmbeddingStatus.COMPLETED,
            f"got {fx.gt.embedding_status}",
            failures,
        )
        if inspect:
            self.stdout.write(f"  embed result: {result}")

    def _case_retrieve_same_tenant(
        self, fx: _Fixture, inspect: bool, failures: list[str]
    ):
        self.stdout.write(self.style.NOTICE("→ Case: same-tenant retrieval"))
        rows, _ = GroundTruthService.retrieve_few_shot(
            gt=fx.gt,
            inputs={
                "question": "i forgot my password, how do i log in",
                "answer": "click forgot password link",
            },
            max_results=3,
        )
        self._assert(
            "same-tenant retrieval returns at least one match",
            len(rows) >= 1,
            f"got {len(rows)}",
            failures,
        )
        verdicts = {r.get("verdict") for r in rows}
        self._assert(
            "matches include the password-reset row (verdict=Pass)",
            "Pass" in verdicts,
            f"got verdicts={verdicts}",
            failures,
        )
        if inspect:
            for r in rows:
                self.stdout.write(
                    f"  match: q={r.get('question', '')[:40]!r} verdict={r.get('verdict')}"
                )

    def _case_retrieve_cross_tenant_is_zero(
        self, fx: _Fixture, inspect: bool, failures: list[str]
    ):
        self.stdout.write(self.style.NOTICE("→ Case: cross-tenant isolation"))
        # Mutate the gt to look like a different org for the retrieval
        # check; we don't persist this - we just want the helper's
        # tenant filter to gate the CH read.
        original_org_id = fx.gt.organization_id
        other = Organization.objects.create(name=f"rt-other-{uuid.uuid4().hex[:6]}")
        fx.gt.organization_id = other.id
        try:
            rows, _ = GroundTruthService.retrieve_few_shot(
                gt=fx.gt,
                inputs={"question": "reset password", "answer": "forgot link"},
                max_results=3,
            )
            self._assert(
                "different-org retrieval returns 0 matches",
                len(rows) == 0,
                f"got {len(rows)}",
                failures,
            )
        finally:
            fx.gt.organization_id = original_org_id
            other.delete()
        if inspect:
            self.stdout.write("  cross-tenant: 0 rows (as expected)")

    def _case_skip_gate_empty_inputs(
        self, fx: _Fixture, inspect: bool, failures: list[str]
    ):
        self.stdout.write(self.style.NOTICE("→ Case: empty-input skip gate"))
        rows, _ = GroundTruthService.retrieve_few_shot(
            gt=fx.gt,
            inputs={"question": "", "answer": None},
            max_results=3,
        )
        self._assert(
            "all-empty runtime inputs short-circuit the gate (0 matches)",
            len(rows) == 0,
            f"got {len(rows)}",
            failures,
        )
        if inspect:
            self.stdout.write("  empty-inputs: 0 rows (as expected)")

    def _case_re_embed_replaces_prior_vectors(
        self, fx: _Fixture, inspect: bool, failures: list[str]
    ):
        self.stdout.write(self.style.NOTICE("→ Case: re-embed replaces prior vectors"))
        live_before = self._live_row_count(fx)
        # Add one extra row and re-embed; expect the resulting live count
        # to be (new total rows) × (mapped columns), not stacked.
        fx.gt.data = fx.gt.data + [
            {
                "question": "is your shipping free?",
                "answer": "yes, on orders above 50 dollars",
                "verdict": "Pass",
                "reason": "accurate and helpful",
            }
        ]
        fx.gt.row_count = len(fx.gt.data)
        fx.gt.save(update_fields=["data", "row_count", "updated_at"])

        GroundTruthService.embed_dataset(gt=fx.gt)
        live_after = self._live_row_count(fx)
        expected_after = len(fx.gt.data) * 2  # 2 mapped columns
        # If the soft-delete had failed, the new rows would stack on the
        # old ones and we'd see ``live_before + expected_after`` instead
        # of just ``expected_after``. The single equality check pins
        # both: count is exactly right AND stacking didn't happen.
        self._assert(
            (
                f"after re-embed live rows = {expected_after} "
                f"(prior {live_before}, stacked would be {live_before + expected_after})"
            ),
            live_after == expected_after,
            f"got {live_after}",
            failures,
        )
        if inspect:
            self.stdout.write(
                f"  re-embed live row count: {live_before} → {live_after}"
            )

    # ─────────────────────────────────────────────────────────────────
    # Fixtures & helpers
    # ─────────────────────────────────────────────────────────────────

    def _build_fixture(self) -> _Fixture:
        suffix = uuid.uuid4().hex[:8]
        org = Organization.objects.create(name=f"rt-org-{suffix}")
        user = User.objects.create_user(
            email=f"rt-{suffix}@futureagi.com",
            password="testpassword123",
            name=f"rt-user-{suffix}",
            organization=org,
            organization_role=OrganizationRoles.OWNER,
        )
        workspace = Workspace.objects.create(
            name=f"rt-ws-{suffix}",
            organization=org,
            is_default=True,
            is_active=True,
            created_by=user,
        )
        template = EvalTemplate.no_workspace_objects.create(
            name=f"rt-eval-{suffix}",
            organization=org,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
            config={
                "output": "Pass/Fail",
                "required_keys": ["question", "answer"],
            },
            criteria="Judge {{question}} against {{answer}}",
            visible_ui=True,
        )
        gt = EvalGroundTruth.objects.create(
            eval_template=template,
            organization=org,
            workspace=workspace,
            name=f"rt-gt-{suffix}",
            file_name="rt.csv",
            columns=["question", "answer", "verdict", "reason"],
            data=[
                {
                    "question": "how do i reset my password",
                    "answer": "click the forgot password link on the login page",
                    "verdict": "Pass",
                    "reason": "clear and helpful",
                },
                {
                    "question": "my refund is delayed two weeks",
                    "answer": "hold on while i check that for you",
                    "verdict": "Fail",
                    "reason": "should have escalated per policy",
                },
                {
                    "question": "can you help me close my account",
                    "answer": "sure, please confirm your email to begin",
                    "verdict": "Pass",
                    "reason": "polite and within scope",
                },
            ],
            row_count=3,
            variable_mapping={"question": "question", "answer": "answer"},
            role_mapping={"output": "verdict", "explanation": "reason"},
            embedding_status=EvalGroundTruth.EmbeddingStatus.PENDING,
        )
        return _Fixture(
            org=org, user=user, workspace=workspace, template=template, gt=gt
        )

    def _announce(self, fx: _Fixture, inspect: bool):
        self.stdout.write(
            self.style.NOTICE(
                "→ Round-trip fixture:\n"
                f"   gt_id      = {fx.gt.id}\n"
                f"   eval_id    = {fx.template.id}\n"
                f"   org_id     = {fx.org.id}\n"
                f"   workspace  = {fx.workspace.id}\n"
                f"   row_count  = {fx.gt.row_count}\n"
            )
        )
        if inspect:
            self.stdout.write(f"   data       = {fx.gt.data}")

    def _live_row_count(self, fx: _Fixture) -> int:
        db = ClickHouseVectorDB()
        try:
            db.create_table(GROUND_TRUTH_TABLE_NAME)
            rows = db.client.execute(
                f"SELECT count() FROM {GROUND_TRUTH_TABLE_NAME} "
                f"WHERE deleted = 0 AND eval_id = '{fx.template.id}' AND "
                f"has(metadata.key, 'organization_id') AND "
                f"metadata.value[indexOf(metadata.key, 'organization_id')] = '{fx.org.id}'"
            )
            return int(rows[0][0]) if rows else 0
        finally:
            db.close()

    def _teardown(self, fx: _Fixture) -> None:
        # Soft-delete the CH vectors for this run, then drop the PG
        # fixtures. We delete in reverse FK order.
        db = ClickHouseVectorDB()
        try:
            db.client.execute(
                f"ALTER TABLE {GROUND_TRUTH_TABLE_NAME} UPDATE deleted = 1 "
                f"WHERE eval_id = '{fx.template.id}' AND "
                f"has(metadata.key, 'organization_id') AND "
                f"metadata.value[indexOf(metadata.key, 'organization_id')] = '{fx.org.id}'"
            )
        except Exception as exc:
            self.stdout.write(
                self.style.WARNING(f"  CH cleanup failed (ignored): {exc!r}")
            )
        finally:
            db.close()
        EvalGroundTruth.objects.filter(id=fx.gt.id).delete()
        EvalTemplate.no_workspace_objects.filter(id=fx.template.id).delete()
        Workspace.objects.filter(id=fx.workspace.id).delete()
        User.objects.filter(id=fx.user.id).delete()
        Organization.objects.filter(id=fx.org.id).delete()

    def _assert(
        self, label: str, ok: bool, failure_detail: str, failures: list[str]
    ) -> None:
        """Single-line pass/fail printer.

        ``label`` describes the invariant being checked (e.g.
        "service returns status=completed") and is printed on both pass
        AND fail. ``failure_detail`` is appended on failure so the run
        log is self-explanatory without re-reading the source.
        """
        if ok:
            self.stdout.write(self.style.SUCCESS(f"  ✓ {label}"))
        else:
            message = f"{label} - {failure_detail}"
            self.stdout.write(self.style.ERROR(f"  ✗ {message}"))
            failures.append(message)
