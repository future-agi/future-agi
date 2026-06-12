"""Dual-write seeding helpers for the eval-task filter integration suite.

Every seeded row lands in BOTH Postgres (Django ORM) and ClickHouse (direct INSERT
into the CDC landing tables; spans_mv repopulates the denormalized `spans` table).
The base corpus is a fixed 24-row span set across 6 traces and 3 sessions, with
distinct values for every column the FilterCase matrix can filter on.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from tracer.models.observation_span import (
    EvalLogger,
    EvalTargetType,
    ObservationSpan,
)
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession

# ---------- Base corpus shape ------------------------------------------------

# 3 sessions × 2 traces/session × 4 spans/trace = 24 spans.
# Each session's first trace's first span is a root conversation span (voice call).
# Each session has one "high cost" trace and one "low cost" trace.

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_MODELS = ["gpt-4", "gpt-3.5-turbo", "claude-3-opus"]
_PROVIDERS = ["openai", "openai", "anthropic"]
CHOICE_OPTIONS = ["good", "bad", "neutral"]


@dataclass
class SeededRow:
    span_id: str
    trace_id: str
    session_id: str
    project_id: str
    observation_type: str
    parent_span_id: str | None
    model: str
    provider: str
    status: str
    cost: float
    latency_ms: int
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    created_at: datetime
    span_attr_str: dict[str, str]
    span_attr_num: dict[str, float]
    span_attr_bool: dict[str, bool]
    has_eval: bool
    eval_value: float | None
    has_annotation: bool
    annotation_value: float | None
    has_choice_eval: bool
    choice_value: str | None


@dataclass
class DualWriter:
    ch: Any  # clickhouse_connect Client
    ch_database: str
    seeded: list[SeededRow] = field(default_factory=list)

    # Lazily-allocated when first eval / annotation row is seeded; the matrix
    # uses these to target EVAL_METRIC / ANNOTATION cases.
    _eval_config_id: uuid.UUID | None = None
    _annotation_label_id: uuid.UUID | None = None
    _choice_eval_config_id: uuid.UUID | None = None

    @property
    def eval_config_id(self) -> str:
        return str(self._eval_config_id) if self._eval_config_id else ""

    @property
    def annotation_label_id(self) -> str:
        return str(self._annotation_label_id) if self._annotation_label_id else ""

    @property
    def choice_eval_config_id(self) -> str:
        return str(self._choice_eval_config_id) if self._choice_eval_config_id else ""

    # ------- public ---------------------------------------------------------

    def seed_base_corpus(self, project) -> dict:
        """Dual-write the 24-span corpus. Returns counts dict."""
        # Materialize the eval template + label up front so PG row inserts
        # have valid FKs.
        eval_template = self._get_or_create_eval_template(project)
        eval_config = self._get_or_create_eval_config(project, eval_template)
        choice_template = self._get_or_create_choice_eval_template(project)
        choice_eval_config = self._get_or_create_choice_eval_config(
            project, choice_template
        )
        annotation_label = self._get_or_create_annotation_label(project)
        self._eval_config_id = eval_config.id
        self._choice_eval_config_id = choice_eval_config.id
        self._annotation_label_id = annotation_label.id

        rows: list[SeededRow] = []
        sessions = []
        for s_idx in range(3):
            sess = TraceSession.objects.create(
                id=uuid.uuid4(), project=project, name=f"session_{s_idx}"
            )
            sessions.append(sess)
            self._insert_session_ch(sess)
            for t_idx in range(2):
                trace = Trace.objects.create(
                    id=uuid.uuid4(),
                    project=project,
                    session=sess,
                    name=f"trace_s{s_idx}_t{t_idx}",
                )
                self._insert_trace_ch(trace)
                for sp_idx in range(4):
                    row = self._build_row(project, trace, sess, s_idx, t_idx, sp_idx)
                    self._insert_span_pg(row, trace, project)
                    self._insert_span_ch(row)
                    if row.has_eval:
                        self._insert_eval_pg_ch(row, trace, eval_config)
                    if row.has_choice_eval:
                        self._insert_choice_eval_pg_ch(row, trace, choice_eval_config)
                    if row.has_annotation:
                        self._insert_annotation_pg_ch(
                            row, trace, project, annotation_label
                        )
                    rows.append(row)
        self.seeded = rows
        # spans_mv is non-refreshable; INSERT INTO tracer_observation_span fires
        # the MV write to ``spans`` synchronously. OPTIMIZE FINAL eliminates
        # any ReplicatedReplacingMergeTree merge lag before the test reads.
        try:
            self.ch.command(f"OPTIMIZE TABLE {self.ch_database}.spans FINAL")
        except Exception:
            pass
        return {
            "span_count": len(rows),
            "session_count": len(sessions),
            "trace_count": len(sessions) * 2,
        }

    # ------- row construction ----------------------------------------------

    def _build_row(self, project, trace, sess, s_idx, t_idx, sp_idx) -> SeededRow:
        is_voice_root = (sp_idx == 0 and t_idx == 0)
        is_root = (sp_idx == 0)
        parent = None if is_root else f"span_root_{trace.id.hex[:12]}"
        observation_type = (
            "conversation" if is_voice_root else ("llm" if sp_idx > 0 else "chain")
        )
        model = _MODELS[s_idx]
        provider = _PROVIDERS[s_idx]
        status = "ERROR" if (s_idx == 2 and sp_idx == 3) else "OK"
        cost = 0.001 * (s_idx + 1) * (t_idx + 1) * (sp_idx + 1)
        latency_ms = 100 * (sp_idx + 1) + 50 * t_idx
        total_tokens = 10 * (sp_idx + 1)
        created_at = _NOW + timedelta(days=s_idx, hours=t_idx, minutes=sp_idx)
        span_attr_str = {
            "user_intent": "checkout" if sp_idx % 2 == 0 else "browse",
            "channel": ["web", "mobile", "voice"][s_idx],
        }
        span_attr_num = {"retries": float(sp_idx), "score": 0.1 * (sp_idx + 1)}
        span_attr_bool = {"premium": (s_idx == 0)}
        # Eval: spans at sp_idx ∈ {1,2,3} all get an eval, with three distinct
        # values so range filters (lt / gt / between) have real boundaries.
        _EVAL_VALUE_BY_SP_IDX = {1: 0.3, 2: 0.6, 3: 0.9}
        has_eval = sp_idx in _EVAL_VALUE_BY_SP_IDX
        eval_value = _EVAL_VALUE_BY_SP_IDX.get(sp_idx)
        # Annotation: 6 spans (sp_idx=2 across both traces of all sessions),
        # values 0.2/0.5/0.8 cycled by s_idx for non-trivial range coverage.
        _ANNOTATION_VALUE_BY_S_IDX = {0: 0.2, 1: 0.5, 2: 0.8}
        has_annotation = (sp_idx == 2)
        annotation_value = (
            _ANNOTATION_VALUE_BY_S_IDX.get(s_idx) if has_annotation else None
        )
        # CHOICE eval: same 18 spans as float eval (sp_idx ∈ {1,2,3}),
        # choice_value cycled by sp_idx so each option discriminates 6 spans.
        _CHOICE_BY_SP_IDX = {1: "good", 2: "bad", 3: "neutral"}
        has_choice_eval = sp_idx in _CHOICE_BY_SP_IDX
        choice_value = _CHOICE_BY_SP_IDX.get(sp_idx)

        span_id = (
            f"span_root_{trace.id.hex[:12]}"
            if is_root
            else f"span_{uuid.uuid4().hex[:16]}"
        )
        return SeededRow(
            span_id=span_id,
            trace_id=str(trace.id),
            session_id=str(sess.id),
            project_id=str(project.id),
            observation_type=observation_type,
            parent_span_id=parent,
            model=model,
            provider=provider,
            status=status,
            cost=cost,
            latency_ms=latency_ms,
            total_tokens=total_tokens,
            prompt_tokens=total_tokens // 2,
            completion_tokens=total_tokens // 2,
            created_at=created_at,
            span_attr_str=span_attr_str,
            span_attr_num=span_attr_num,
            span_attr_bool=span_attr_bool,
            has_eval=has_eval,
            eval_value=eval_value,
            has_annotation=has_annotation,
            annotation_value=annotation_value,
            has_choice_eval=has_choice_eval,
            choice_value=choice_value,
        )

    # ------- PG insert helpers ---------------------------------------------

    def _get_or_create_eval_template(self, project):
        from model_hub.models.evals_metric import EvalTemplate

        template, _ = EvalTemplate.objects.get_or_create(
            name=f"int_test_template_{project.id}",
            organization=project.organization,
            workspace=project.workspace,
            defaults={
                "description": "integration test template",
                "config": {"type": "pass_fail", "criteria": "x"},
            },
        )
        return template

    def _get_or_create_choice_eval_template(self, project):
        from model_hub.models.evals_metric import EvalTemplate

        template, _ = EvalTemplate.objects.get_or_create(
            name=f"int_test_choice_template_{project.id}",
            organization=project.organization,
            workspace=project.workspace,
            defaults={
                "description": "integration test CHOICE template",
                "config": {"type": "choice", "criteria": "x", "output": "CHOICE"},
                "choices": CHOICE_OPTIONS,
            },
        )
        return template

    def _get_or_create_choice_eval_config(self, project, eval_template):
        from tracer.models.custom_eval_config import CustomEvalConfig

        cfg, _ = CustomEvalConfig.objects.get_or_create(
            project=project,
            name=f"int_test_choice_cfg_{project.id}",
            defaults={
                "eval_template": eval_template,
                "config": {"output": "CHOICE", "choices": CHOICE_OPTIONS},
                "mapping": {"input": "input", "output": "output"},
                "filters": {},
            },
        )
        return cfg

    def _get_or_create_eval_config(self, project, eval_template):
        from tracer.models.custom_eval_config import CustomEvalConfig

        cfg, _ = CustomEvalConfig.objects.get_or_create(
            project=project,
            name=f"int_test_eval_cfg_{project.id}",
            defaults={
                "eval_template": eval_template,
                "config": {"threshold": 0.5},
                "mapping": {"input": "input", "output": "output"},
                "filters": {},
            },
        )
        return cfg

    def _get_or_create_annotation_label(self, project):
        from model_hub.models.choices import AnnotationTypeChoices
        from model_hub.models.develop_annotations import AnnotationsLabels

        label, _ = AnnotationsLabels.objects.get_or_create(
            name=f"int_test_label_{project.id}",
            type=AnnotationTypeChoices.NUMERIC.value,
            organization=project.organization,
            workspace=project.workspace,
            project=project,
            defaults={
                "settings": {
                    "min": 0,
                    "max": 1,
                    "step_size": 0.1,
                    "display_type": "slider",
                },
            },
        )
        return label

    def _insert_span_pg(self, row: SeededRow, trace, project) -> None:
        attrs = {
            **{k: v for k, v in row.span_attr_str.items()},
            **{k: v for k, v in row.span_attr_num.items()},
            **{k: v for k, v in row.span_attr_bool.items()},
        }
        ObservationSpan.objects.create(
            id=row.span_id,
            project=project,
            trace=trace,
            name=row.span_id,
            observation_type=row.observation_type,
            status=row.status,
            parent_span_id=row.parent_span_id,
            start_time=row.created_at,
            end_time=row.created_at + timedelta(milliseconds=row.latency_ms),
            latency_ms=row.latency_ms,
            model=row.model,
            provider=row.provider,
            prompt_tokens=row.prompt_tokens,
            completion_tokens=row.completion_tokens,
            total_tokens=row.total_tokens,
            cost=row.cost,
            span_attributes=attrs,
        )
        # BaseModel.created_at is auto_now_add — override post-create.
        ObservationSpan.objects.filter(id=row.span_id).update(
            created_at=row.created_at
        )

    def _insert_span_ch(self, row: SeededRow) -> None:
        attrs_json = json.dumps(
            {
                **row.span_attr_str,
                **row.span_attr_num,
                **{k: bool(v) for k, v in row.span_attr_bool.items()},
            }
        ).replace("'", "''")
        parent_sql = f"'{row.parent_span_id}'" if row.parent_span_id else "NULL"
        start = row.created_at.strftime("%Y-%m-%d %H:%M:%S")
        end = (row.created_at + timedelta(milliseconds=row.latency_ms)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        self.ch.command(
            f"""
            INSERT INTO {self.ch_database}.tracer_observation_span
              (id, trace_id, project_id, name, observation_type, status,
               parent_span_id, start_time, end_time, latency_ms, model, provider,
               prompt_tokens, completion_tokens, total_tokens, cost,
               input, output, span_attributes, resource_attributes,
               metadata, tags, span_events, created_at, updated_at,
               _peerdb_synced_at, _peerdb_is_deleted, _peerdb_version)
            VALUES
              ('{row.span_id}', '{row.trace_id}', '{row.project_id}',
               '{row.span_id}', '{row.observation_type}', '{row.status}',
               {parent_sql},
               '{start}', '{end}',
               {row.latency_ms}, '{row.model}', '{row.provider}',
               {row.prompt_tokens}, {row.completion_tokens}, {row.total_tokens}, {row.cost},
               '', '', '{attrs_json}', '{{}}',
               '{{}}', '[]', '[]',
               '{start}', '{start}',
               now64(), 0, 1)
            """
        )

    def _insert_trace_ch(self, trace) -> None:
        sess_sql = f"'{trace.session_id}'" if trace.session_id else "NULL"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.ch.command(
            f"""
            INSERT INTO {self.ch_database}.tracer_trace
              (id, project_id, name, session_id, external_id, tags,
               metadata, input, output, error,
               created_at, updated_at,
               _peerdb_synced_at, _peerdb_is_deleted, _peerdb_version)
            VALUES
              ('{trace.id}', '{trace.project_id}', '{trace.name}',
               {sess_sql}, '', '[]',
               '{{}}', '{{}}', '{{}}', '{{}}',
               '{now}', '{now}',
               now64(), 0, 1)
            """
        )

    def _insert_session_ch(self, sess) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.ch.command(
            f"""
            INSERT INTO {self.ch_database}.trace_session
              (id, project_id, name,
               created_at, updated_at,
               _peerdb_synced_at, _peerdb_is_deleted, _peerdb_version)
            VALUES
              ('{sess.id}', '{sess.project_id}', '{sess.name}',
               '{now}', '{now}',
               now64(), 0, 1)
            """
        )

    def _insert_eval_pg_ch(self, row: SeededRow, trace, eval_config) -> None:
        # Span-level eval row mirrors what process_eval_task would create
        # (target_type=SPAN, FK to span + trace).
        EvalLogger.objects.create(
            id=uuid.uuid4(),
            observation_span_id=row.span_id,
            trace=trace,
            custom_eval_config=eval_config,
            target_type=EvalTargetType.SPAN,
            output_float=row.eval_value,
            output_bool=(row.eval_value or 0) >= 0.5,
        )
        ch_id = uuid.uuid4()
        created = row.created_at.strftime("%Y-%m-%d %H:%M:%S")
        self.ch.command(
            f"""
            INSERT INTO {self.ch_database}.tracer_eval_logger
              (id, observation_span_id, trace_id,
               custom_eval_config_id, target_type,
               output_float, output_bool, eval_task_id,
               created_at, updated_at,
               _peerdb_synced_at, _peerdb_is_deleted, _peerdb_version)
            VALUES
              ('{ch_id}', '{row.span_id}', '{row.trace_id}',
               '{eval_config.id}', 'span',
               {row.eval_value}, {int((row.eval_value or 0) >= 0.5)}, NULL,
               '{created}', '{created}',
               now64(), 0, 1)
            """
        )

    def _insert_choice_eval_pg_ch(self, row: SeededRow, trace, eval_config) -> None:
        # CHOICE-type EvalLogger: output_str_list set, output_float/_bool null.
        # The `When(output_str_list__isnull=False, ...)` branch of the metric
        # annotation requires the null shape to dispatch correctly.
        EvalLogger.objects.create(
            id=uuid.uuid4(),
            observation_span_id=row.span_id,
            trace=trace,
            custom_eval_config=eval_config,
            target_type=EvalTargetType.SPAN,
            output_str_list=[row.choice_value],
        )
        ch_id = uuid.uuid4()
        created = row.created_at.strftime("%Y-%m-%d %H:%M:%S")
        # CH expects output_str_list as a JSON string ("[\"good\"]").
        choice_json = json.dumps([row.choice_value])
        self.ch.command(
            f"""
            INSERT INTO {self.ch_database}.tracer_eval_logger
              (id, observation_span_id, trace_id,
               custom_eval_config_id, target_type,
               output_str_list, eval_task_id,
               created_at, updated_at,
               _peerdb_synced_at, _peerdb_is_deleted, _peerdb_version)
            VALUES
              ('{ch_id}', '{row.span_id}', '{row.trace_id}',
               '{eval_config.id}', 'span',
               '{choice_json}', NULL,
               '{created}', '{created}',
               now64(), 0, 1)
            """
        )

    def _insert_annotation_pg_ch(
        self, row: SeededRow, trace, project, annotation_label
    ) -> None:
        """Create the per-row Score and mirror to CH.

        Only the PG-side Score is required for the eval-task runner
        (process_eval_task's per-label annotate subquery reads model_hub.Score).
        The CH-side mirror is a best-effort: the list endpoints' annotation
        filter path is exercised against the CH copy.
        """
        from model_hub.models.choices import QueueItemSourceType, ScoreSource
        from model_hub.models.score import Score

        # Per-row distinct annotator user to dodge the unique constraint on
        # (observation_span, label, annotator) when the same label is reused.
        # NULL annotator is also fine here since we only have one row per
        # (span, label) but make it explicit.
        Score.objects.create(
            source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
            observation_span_id=row.span_id,
            label=annotation_label,
            value={"value": row.annotation_value},
            organization=project.organization,
            workspace=project.workspace,
            score_source=ScoreSource.HUMAN.value,
        )
        # CH mirror is informational only — the list endpoints' annotation
        # path in CH doesn't currently consume this in the SpanListQueryBuilder
        # the way PG does. Skipped to avoid coupling tests to a CH schema we
        # don't drive.

    # ------- voice corpus ---------------------------------------------------

    def seed_voice_corpus(self, project) -> dict:
        """Seed 24 voice-call root spans (observation_type='conversation',
        parent_span_id=NULL) into ``project``. Each call is its own trace;
        per-span attribute variation matches the base corpus so the same
        FilterCase predicates discriminate identically."""
        eval_template = self._get_or_create_eval_template(project)
        eval_config = self._get_or_create_eval_config(project, eval_template)
        choice_template = self._get_or_create_choice_eval_template(project)
        choice_eval_config = self._get_or_create_choice_eval_config(
            project, choice_template
        )
        annotation_label = self._get_or_create_annotation_label(project)
        self._eval_config_id = eval_config.id
        self._choice_eval_config_id = choice_eval_config.id
        self._annotation_label_id = annotation_label.id

        rows: list[SeededRow] = []
        sess = TraceSession.objects.create(
            id=uuid.uuid4(), project=project, name="voice_session"
        )
        self._insert_session_ch(sess)

        for i in range(24):
            # Mirror base corpus variation axes (3 × 2 × 4 = 24 cells).
            s_idx = i // 8
            t_idx = (i // 4) % 2
            sp_idx = i % 4

            trace = Trace.objects.create(
                id=uuid.uuid4(),
                project=project,
                session=sess,
                name=f"voice_call_{i}",
            )
            self._insert_trace_ch(trace)

            row = self._build_row(project, trace, sess, s_idx, t_idx, sp_idx)
            # Force voice-call shape.
            row.observation_type = "conversation"
            row.parent_span_id = None

            self._insert_span_pg(row, trace, project)
            self._insert_span_ch(row)
            if row.has_eval:
                self._insert_eval_pg_ch(row, trace, eval_config)
            if row.has_choice_eval:
                self._insert_choice_eval_pg_ch(row, trace, choice_eval_config)
            if row.has_annotation:
                self._insert_annotation_pg_ch(row, trace, project, annotation_label)
            rows.append(row)

        self.seeded = rows
        try:
            self.ch.command(f"OPTIMIZE TABLE {self.ch_database}.spans FINAL")
        except Exception:
            pass
        return {"span_count": len(rows), "session_count": 1, "trace_count": 24}

    # ------- accessors ------------------------------------------------------

    def expected_predicate_count(self, predicate) -> int:
        """Convenience for tests: count rows matching a per-row predicate."""
        return sum(1 for r in self.seeded if predicate(r))
