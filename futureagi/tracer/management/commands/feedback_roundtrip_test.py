"""End-to-end feedback few-shot WRITE↔READ round-trip test (TH-5462).

Runs against the LIVE stack — real ``EmbeddingManager`` (serving-model
embeddings + ClickHouse ``feedbacks`` vector store). NO mocks. For each case it:

  1. WRITE  — embeds a feedback row via the unified write
              (``EmbeddingManager.parallel_process_metadata``, feedback branch).
  2. VERIFY — the row landed in ClickHouse for that eval_id.
  3. READ   — retrieves it back via the unified read
              (``retrieve_feedback_fewshots``) and checks the feedback content
              comes back in the few-shot block.

Covers the three caller shapes (keying differs, primitives are shared):
  - dataset            → indexed by a column UUID key
  - observe            → indexed by the field name "output"
  - run_eval_func_task → field name "output" (empty mapping → identity)
and modalities text / image / audio (image & audio via real URLs, which
``detect_input_type`` fetches + sniffs).

Each case uses a unique eval_id (UUID) so it is isolated from real data and
from the other cases. Rows are deleted at the end unless ``--keep``.

Run:
    docker exec futureagi-backend-1 python manage.py feedback_roundtrip_test
    docker exec futureagi-backend-1 python manage.py feedback_roundtrip_test --keep --org-id <uuid>
"""

import uuid

from django.core.management.base import BaseCommand

from accounts.models import Organization
from agentic_eval.core.database.ch_vector import ClickHouseVectorDB
from agentic_eval.core.embeddings.embedding_manager import (
    FEEDBACK_TABLE_NAME,
    EmbeddingManager,
)
from agentic_eval.core.embeddings.feedback_fewshots import retrieve_feedback_fewshots

# Public, reachable media URLs. detect_input_type fetches + content-sniffs them,
# so they must return the right Content-Type and NOT block the default UA.
# (wikimedia 403s the default user-agent -> classified as 'file' -> embedded as
# text, which silently defeats the image/audio test. These two return 200 with
# image/jpeg and audio/mpeg respectively.)
IMAGE_URL = (
    "https://cdn-s3.autocarindia.com/Mercedes/cla-electric/"
    "Mercedes-Benz_CLA_EV_Front_Quarter_Tracking.jpg?w=640&q=75"
)
AUDIO_URL = "https://download.samplelib.com/mp3/sample-3s.mp3"

TEXT_INPUT = "he is a funny chap"

# name, path, input_key, input_value, feedback_value, feedback_comment
# name, path, input_key, input_value, feedback_value, feedback_comment, expected_modality
CASES = [
    (
        "dataset-text",
        "dataset",
        str(uuid.uuid4()),  # column-UUID style key
        TEXT_INPUT,
        "Failed",
        "this uses informal language and is not allowed at our company",
        "text",
    ),
    (
        "dataset-image",
        "dataset",
        str(uuid.uuid4()),
        IMAGE_URL,
        "Passed",
        "image content is appropriate and on-brand",
        "image",
    ),
    (
        "dataset-audio",
        "dataset",
        str(uuid.uuid4()),
        AUDIO_URL,
        "Failed",
        "audio contains disallowed background music",
        "audio",
    ),
    (
        "observe-text",
        "observe",
        "output",  # observe configs map name->name
        TEXT_INPUT,
        "Passed",
        "meets generic corporate standards",
        "text",
    ),
    (
        "run_eval_func_task-text",
        "run_eval_func_task",
        "output",  # empty mapping_cfg => identity, key stays "output"
        TEXT_INPUT,
        "Failed",
        "does not meet standards, too casual",
        "text",
    ),
]


class Command(BaseCommand):
    help = "End-to-end feedback embed/retrieve round-trip test (real stack, no mocks)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--org-id",
            default=None,
            help="Organization UUID to scope embeddings (defaults to the first org).",
        )
        parser.add_argument(
            "--keep",
            action="store_true",
            help="Keep the test feedback rows in ClickHouse (default: delete them).",
        )

    # -- helpers ---------------------------------------------------------------
    def _ch_count(self, eval_id):
        db = ClickHouseVectorDB()
        try:
            rows = db.client.execute(
                f"SELECT count() FROM {FEEDBACK_TABLE_NAME} "
                "WHERE eval_id = %(e)s AND deleted = 0",
                {"e": str(eval_id)},
            )
            return rows[0][0] if rows else 0
        finally:
            try:
                db.close()
            except Exception:
                pass

    def _ch_input_type(self, eval_id):
        """The modality actually stored ('text'/'image'/'audio') — proves the
        image/audio embedding path ran, not a silent text fallback."""
        db = ClickHouseVectorDB()
        try:
            rows = db.client.execute(
                f"SELECT metadata.value[indexOf(metadata.key, 'input_type')] "
                f"FROM {FEEDBACK_TABLE_NAME} WHERE eval_id = %(e)s AND deleted = 0 "
                "LIMIT 1",
                {"e": str(eval_id)},
            )
            return rows[0][0] if rows and rows[0] else None
        except Exception:
            return None
        finally:
            try:
                db.close()
            except Exception:
                pass

    def _ch_delete(self, eval_id):
        db = ClickHouseVectorDB()
        try:
            db.client.execute(
                f"ALTER TABLE {FEEDBACK_TABLE_NAME} DELETE WHERE eval_id = %(e)s",
                {"e": str(eval_id)},
            )
        finally:
            try:
                db.close()
            except Exception:
                pass

    def _run_case(
        self, name, path, key, value, fb_value, fb_comment, expected_modality, org_id
    ):
        eval_id = str(uuid.uuid4())
        result = {"name": name, "path": path, "eval_id": eval_id}

        # 1. WRITE — unified embed via parallel_process_metadata (feedback branch).
        row_dict = {
            key: value,
            "feedback_comment": fb_comment,
            "feedback_value": fb_value,
            "item_id": str(uuid.uuid4()).replace("-", "_"),
        }
        em = EmbeddingManager()
        try:
            em.parallel_process_metadata(
                eval_id=eval_id,
                metadatas=row_dict,
                inputs_formater=[key],
                organization_id=org_id,
                workspace_id=None,
            )
        finally:
            try:
                em.close()
            except Exception:
                pass

        # 2. VERIFY the row landed in ClickHouse.
        result["ch_rows"] = self._ch_count(eval_id)
        result["written"] = result["ch_rows"] > 0
        result["input_type"] = self._ch_input_type(eval_id)

        # 3. READ — unified retrieval; assert the feedback comes back.
        few = retrieve_feedback_fewshots(
            eval_id=eval_id,
            inputs=[value],
            input_cols=[key],
            organization_id=org_id,
            workspace_id=None,
        )
        result["fewshot_count"] = len(few) if isinstance(few, list) else (1 if few else 0)
        blob = str(few).lower()
        result["read"] = result["fewshot_count"] > 0
        # The feedback comment/value must surface in the retrieved few-shot.
        result["content_ok"] = (
            fb_comment.strip().lower() in blob or str(fb_value).strip().lower() in blob
        )
        # The input must have been embedded AS its real modality — not silently
        # fallen back to text (e.g. an image/audio URL that 403'd or failed to
        # content-sniff). Stored input_type must match what we sent.
        result["expected_modality"] = expected_modality
        result["modality_ok"] = result.get("input_type") == expected_modality
        result["passed"] = bool(
            result["written"]
            and result["read"]
            and result["content_ok"]
            and result["modality_ok"]
        )
        return result

    # -- entrypoint ------------------------------------------------------------
    def handle(self, *args, **opts):
        org_id = opts.get("org_id")
        if not org_id:
            org_id = str(
                Organization.objects.values_list("id", flat=True).first() or ""
            )
        if not org_id:
            self.stderr.write("No organization found; pass --org-id <uuid>.")
            return

        self.stdout.write(f"Org: {org_id}   cases: {len(CASES)}\n")
        results = []
        for name, path, key, value, fb_value, fb_comment, expected_modality in CASES:
            try:
                r = self._run_case(
                    name,
                    path,
                    key,
                    value,
                    fb_value,
                    fb_comment,
                    expected_modality,
                    org_id,
                )
            except Exception as e:  # report, don't abort the suite
                import traceback

                r = {
                    "name": name,
                    "path": path,
                    "passed": False,
                    "error": f"{type(e).__name__}: {e}",
                    "trace": traceback.format_exc()[-800:],
                }
            results.append(r)
            status = "PASS" if r.get("passed") else "FAIL"
            self.stdout.write(
                f"[{status}] {name:24s} path={r.get('path'):18s} "
                f"written={r.get('written')} ch_rows={r.get('ch_rows')} "
                f"modality={r.get('input_type')}/{r.get('expected_modality')} "
                f"modality_ok={r.get('modality_ok')} "
                f"read={r.get('read')} fewshots={r.get('fewshot_count')} "
                f"content_ok={r.get('content_ok')}"
                + (f"  ERROR={r['error']}" if r.get("error") else "")
            )
            if r.get("trace"):
                self.stdout.write(r["trace"])

        # cleanup
        if not opts.get("keep"):
            for r in results:
                if r.get("eval_id"):
                    try:
                        self._ch_delete(r["eval_id"])
                    except Exception:
                        pass
            self.stdout.write("\nCleaned up test rows (use --keep to retain).")
        else:
            self.stdout.write("\nKept test rows (--keep).")

        passed = sum(1 for r in results if r.get("passed"))
        self.stdout.write(f"\n=== {passed}/{len(results)} cases passed ===")
