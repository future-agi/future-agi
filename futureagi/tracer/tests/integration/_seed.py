"""Dual-write seeding helpers for the eval-task filter integration suite.

Every seeded row lands in BOTH Postgres (Django ORM) and ClickHouse — direct
inserts into the v2 read tables (`spans` / `traces` / `trace_sessions` via
``tracer.tests._ch_seed``, plus the v2-shaped eval-logger tables).
The base corpus is a fixed 24-row span set across 6 traces and 3 sessions, with
distinct values for every column the FilterCase matrix can filter on.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
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
    # PASS_FAIL eval (distinct template config {"output": "Pass/Fail"}) on
    # sp_idx ∈ {1,3}; pf_value True → "Passed", False → "Failed".
    has_pf_eval: bool = False
    pf_value: bool | None = None
    # Text / thumbs / categorical annotations mirrored onto the same sp_idx==2
    # spans as the numeric label.
    ann_text: str | None = None
    ann_thumb: str | None = None
    ann_selected: list[str] | None = None
    # Numeric-label Score carries an annotator (the integration user) for
    # s_idx ∈ {0,1}; None for s_idx==2.
    ann_annotator_is_user: bool = False
    # Score scope the annotation was created at: "span" (base corpus) or
    # "trace" (voice corpus — mirrors prod, where voice-call annotations are
    # created trace-scoped via the trace-backed voice grid).
    annotation_scope: str = "span"


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
    _pf_eval_config_id: uuid.UUID | None = None
    _text_label_id: uuid.UUID | None = None
    _thumbs_label_id: uuid.UUID | None = None
    _categorical_label_id: uuid.UUID | None = None
    _annotator_user_id: uuid.UUID | None = None

    @property
    def eval_config_id(self) -> str:
        return str(self._eval_config_id) if self._eval_config_id else ""

    @property
    def annotation_label_id(self) -> str:
        return str(self._annotation_label_id) if self._annotation_label_id else ""

    @property
    def choice_eval_config_id(self) -> str:
        return str(self._choice_eval_config_id) if self._choice_eval_config_id else ""

    @property
    def pf_eval_config_id(self) -> str:
        return str(self._pf_eval_config_id) if self._pf_eval_config_id else ""

    @property
    def text_label_id(self) -> str:
        return str(self._text_label_id) if self._text_label_id else ""

    @property
    def thumbs_label_id(self) -> str:
        return str(self._thumbs_label_id) if self._thumbs_label_id else ""

    @property
    def categorical_label_id(self) -> str:
        return str(self._categorical_label_id) if self._categorical_label_id else ""

    @property
    def annotator_user_id(self) -> str:
        return str(self._annotator_user_id) if self._annotator_user_id else ""

    # ------- public ---------------------------------------------------------

    def _setup_shared_configs(self, project) -> dict:
        """Create the eval templates / configs, annotation labels, and the
        annotator user shared by every corpus, and cache their ids."""
        cfg = {
            "eval_config": self._get_or_create_eval_config(
                project, self._get_or_create_eval_template(project)
            ),
            "choice_eval_config": self._get_or_create_choice_eval_config(
                project, self._get_or_create_choice_eval_template(project)
            ),
            "pf_eval_config": self._get_or_create_pf_eval_config(
                project, self._get_or_create_pf_eval_template(project)
            ),
            "numeric_label": self._get_or_create_annotation_label(project),
            "text_label": self._get_or_create_text_label(project),
            "thumbs_label": self._get_or_create_thumbs_label(project),
            "categorical_label": self._get_or_create_categorical_label(project),
            "annotator_user": self._get_or_create_annotator_user(project),
        }
        self._eval_config_id = cfg["eval_config"].id
        self._choice_eval_config_id = cfg["choice_eval_config"].id
        self._pf_eval_config_id = cfg["pf_eval_config"].id
        self._annotation_label_id = cfg["numeric_label"].id
        self._text_label_id = cfg["text_label"].id
        self._thumbs_label_id = cfg["thumbs_label"].id
        self._categorical_label_id = cfg["categorical_label"].id
        self._annotator_user_id = cfg["annotator_user"].id
        return cfg

    def seed_base_corpus(self, project) -> dict:
        """Dual-write the 25-span corpus. Returns counts dict."""
        cfg = self._setup_shared_configs(project)

        rows: list[SeededRow] = []
        sessions = []
        traces = []
        span_objs = []
        score_objs = []
        for s_idx in range(3):
            sess = TraceSession.objects.create(
                id=uuid.uuid4(), project=project, name=f"session_{s_idx}"
            )
            sessions.append(sess)
            for t_idx in range(2):
                trace = Trace.objects.create(
                    id=uuid.uuid4(),
                    project=project,
                    session=sess,
                    name=f"trace_s{s_idx}_t{t_idx}",
                )
                traces.append(trace)
                for sp_idx in range(4):
                    row = self._build_row(project, trace, sess, s_idx, t_idx, sp_idx)
                    span_objs.append(self._insert_span_pg(row, trace, project))
                    self._insert_evals(row, trace, cfg)
                    if row.has_annotation:
                        score_objs.extend(
                            self._insert_annotations(row, trace, project, cfg)
                        )
                    rows.append(row)
        # One extra trace in session 2 (single root span, no evals/annotations)
        # so traces_count discriminates (2, 2, 3) for session aggregates.
        extra_trace = Trace.objects.create(
            id=uuid.uuid4(),
            project=project,
            session=sessions[2],
            name="trace_s2_extra",
        )
        traces.append(extra_trace)
        extra_row = self._build_extra_row(project, extra_trace, sessions[2])
        span_objs.append(self._insert_span_pg(extra_row, extra_trace, project))
        rows.append(extra_row)

        self.seeded = rows
        self._flush_ch(span_objs, traces, sessions, score_objs)
        return {
            "span_count": len(rows),
            "session_count": len(sessions),
            "trace_count": len(traces),
        }

    def _build_extra_row(self, project, trace, sess) -> SeededRow:
        """A lone root span (cost 0.05, model gpt-4o, 50 tokens) with no
        evals / annotations, distinguishing session 2's traces_count."""
        span_id = f"span_root_{trace.id.hex[:12]}"
        created_at = _NOW + timedelta(days=2, hours=2)
        return SeededRow(
            span_id=span_id,
            trace_id=str(trace.id),
            session_id=str(sess.id),
            project_id=str(project.id),
            observation_type="chain",
            parent_span_id=None,
            model="gpt-4o",
            provider="openai",
            status="OK",
            cost=0.05,
            latency_ms=100,
            total_tokens=50,
            prompt_tokens=25,
            completion_tokens=25,
            created_at=created_at,
            span_attr_str={},
            span_attr_num={},
            span_attr_bool={},
            has_eval=False,
            eval_value=None,
            has_annotation=False,
            annotation_value=None,
            has_choice_eval=False,
            choice_value=None,
        )

    def _insert_evals(self, row, trace, cfg) -> None:
        if row.has_eval:
            self._insert_eval_pg_ch(row, trace, cfg["eval_config"])
        if row.has_choice_eval:
            self._insert_choice_eval_pg_ch(row, trace, cfg["choice_eval_config"])
        if row.has_pf_eval:
            self._insert_pf_eval_pg_ch(row, trace, cfg["pf_eval_config"])

    # ------- row construction ----------------------------------------------

    def _build_row(self, project, trace, sess, s_idx, t_idx, sp_idx) -> SeededRow:
        is_voice_root = sp_idx == 0 and t_idx == 0
        is_root = sp_idx == 0
        parent = None if is_root else f"span_root_{trace.id.hex[:12]}"
        observation_type = (
            "conversation" if is_voice_root else ("llm" if sp_idx > 0 else "chain")
        )
        # Empty model on the sp_idx==0 root of each session's second trace so
        # text is_null / is_not_null / NOT-family ops have non-degenerate rows.
        model = "" if (sp_idx == 0 and t_idx == 1) else _MODELS[s_idx]
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
        # coupon present only on sp_idx==1 spans → text is_null / is_not_null
        # discrimination on a partially-present attribute.
        if sp_idx == 1:
            span_attr_str["coupon"] = "SAVE10"
        span_attr_num = {"retries": float(sp_idx), "score": 0.1 * (sp_idx + 1)}
        # premium omitted for s_idx==2 → boolean is_null discrimination.
        span_attr_bool = {} if s_idx == 2 else {"premium": (s_idx == 0)}
        # Eval: spans at sp_idx ∈ {1,2,3} all get an eval, with three distinct
        # values so range filters (lt / gt / between) have real boundaries.
        _EVAL_VALUE_BY_SP_IDX = {1: 0.3, 2: 0.6, 3: 0.9}
        has_eval = sp_idx in _EVAL_VALUE_BY_SP_IDX
        eval_value = _EVAL_VALUE_BY_SP_IDX.get(sp_idx)
        # PASS_FAIL eval (distinct template config): sp_idx==1 → Passed,
        # sp_idx==3 → Failed. Disjoint value axis from the SCORE eval above.
        has_pf_eval = sp_idx in (1, 3)
        pf_value = (sp_idx == 1) if has_pf_eval else None
        # Annotation: 6 spans (sp_idx=2 across both traces of all sessions),
        # values 0.2/0.5/0.8 cycled by s_idx for non-trivial range coverage.
        _ANNOTATION_VALUE_BY_S_IDX = {0: 0.2, 1: 0.5, 2: 0.8}
        has_annotation = sp_idx == 2
        annotation_value = (
            _ANNOTATION_VALUE_BY_S_IDX.get(s_idx) if has_annotation else None
        )
        # Text / thumbs / categorical annotations on the same sp_idx==2 spans,
        # cycled by s_idx so each op discriminates.
        ann_text = ["helpful", "needs work", "spam"][s_idx] if has_annotation else None
        ann_thumb = ["up", "up", "down"][s_idx] if has_annotation else None
        ann_selected = (
            [["tag_a"], ["tag_b"], ["tag_a", "tag_b"]][s_idx]
            if has_annotation
            else None
        )
        # Numeric-label Score carries the integration user as annotator for
        # s_idx ∈ {0,1}; None for s_idx==2.
        ann_annotator_is_user = has_annotation and s_idx in (0, 1)
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
            has_pf_eval=has_pf_eval,
            pf_value=pf_value,
            ann_text=ann_text,
            ann_thumb=ann_thumb,
            ann_selected=ann_selected,
            ann_annotator_is_user=ann_annotator_is_user,
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

    def _get_or_create_pf_eval_template(self, project):
        from model_hub.models.evals_metric import EvalTemplate

        template, _ = EvalTemplate.objects.get_or_create(
            name=f"int_test_pf_template_{project.id}",
            organization=project.organization,
            workspace=project.workspace,
            defaults={
                "description": "integration test PASS_FAIL template",
                "config": {"type": "pass_fail", "criteria": "x", "output": "Pass/Fail"},
            },
        )
        return template

    def _get_or_create_pf_eval_config(self, project, eval_template):
        from tracer.models.custom_eval_config import CustomEvalConfig

        cfg, _ = CustomEvalConfig.objects.get_or_create(
            project=project,
            name=f"int_test_pf_cfg_{project.id}",
            defaults={
                "eval_template": eval_template,
                "config": {"output": "Pass/Fail"},
                "mapping": {"input": "input", "output": "output"},
                "filters": {},
            },
        )
        return cfg

    def _get_or_create_text_label(self, project):
        from model_hub.models.choices import AnnotationTypeChoices
        from model_hub.models.develop_annotations import AnnotationsLabels

        label, _ = AnnotationsLabels.objects.get_or_create(
            name=f"int_test_text_label_{project.id}",
            type=AnnotationTypeChoices.TEXT.value,
            organization=project.organization,
            workspace=project.workspace,
            project=project,
            defaults={
                "settings": {
                    "placeholder": "note",
                    "max_length": 500,
                    "min_length": 0,
                }
            },
        )
        return label

    def _get_or_create_thumbs_label(self, project):
        from model_hub.models.choices import AnnotationTypeChoices
        from model_hub.models.develop_annotations import AnnotationsLabels

        label, _ = AnnotationsLabels.objects.get_or_create(
            name=f"int_test_thumbs_label_{project.id}",
            type=AnnotationTypeChoices.THUMBS_UP_DOWN.value,
            organization=project.organization,
            workspace=project.workspace,
            project=project,
            defaults={"settings": {"display_type": "thumbs"}},
        )
        return label

    def _get_or_create_categorical_label(self, project):
        from model_hub.models.choices import AnnotationTypeChoices
        from model_hub.models.develop_annotations import AnnotationsLabels

        label, _ = AnnotationsLabels.objects.get_or_create(
            name=f"int_test_categorical_label_{project.id}",
            type=AnnotationTypeChoices.CATEGORICAL.value,
            organization=project.organization,
            workspace=project.workspace,
            project=project,
            defaults={
                "settings": {
                    "rule_prompt": "",
                    "multi_choice": True,
                    "options": [{"label": "tag_a"}, {"label": "tag_b"}],
                    "auto_annotate": False,
                    "strategy": None,
                }
            },
        )
        return label

    def _get_or_create_annotator_user(self, project):
        from accounts.models.user import User

        email = f"annotator-{project.id}@futureagi.com"
        existing = User.objects.filter(email=email).first()
        if existing:
            return existing
        return User.objects.create_user(
            email=email,
            password="testpassword123",
            name="Integration Annotator",
            organization=project.organization,
        )

    def _insert_span_pg(self, row: SeededRow, trace, project):
        attrs = {
            **row.span_attr_str,
            **row.span_attr_num,
            **row.span_attr_bool,
        }
        span = ObservationSpan.objects.create(
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
        ObservationSpan.objects.filter(id=row.span_id).update(created_at=row.created_at)
        span.created_at = row.created_at
        return span

    def _flush_ch(self, spans, traces, sessions, scores=()) -> None:
        from tracer.tests._ch_seed import (
            seed_ch_scores,
            seed_ch_spans,
            seed_ch_trace_sessions,
            seed_ch_traces,
        )

        seed_ch_spans(spans)
        seed_ch_traces(traces)
        seed_ch_trace_sessions(sessions)
        if scores:
            seed_ch_scores(scores)
        # trace_dict / trace_sessions_dict are HASHED dictionaries with a
        # 30-60s LIFETIME sourced from the tables we just seeded; the voice
        # endpoints resolve project scope via dictGet. Force a reload so the
        # fresh rows are visible immediately and deterministically, instead of
        # racing the refresh cycle (the source of prior timing-based flakiness).
        for dictionary in ("trace_dict", "trace_sessions_dict"):
            self.ch.command(f"SYSTEM RELOAD DICTIONARY {dictionary}")

    # The filter subqueries hardcode ``tracer_eval_logger``; eval-config
    # discovery reads ``settings.CH25_EVAL_LOGGER_TABLE`` (tracer_eval_logger_v2
    # under test settings). Both carry the v2 shape in the test CH (see the
    # integration conftest's ch_schema) — mirror eval rows to both.
    _EVAL_LOGGER_TABLES = ("tracer_eval_logger", "tracer_eval_logger_v2")

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
        created = row.created_at.strftime("%Y-%m-%d %H:%M:%S")
        for table in self._EVAL_LOGGER_TABLES:
            self.ch.command(
                f"""
                INSERT INTO {self.ch_database}.{table}
                  (id, observation_span_id, trace_id,
                   custom_eval_config_id, target_type,
                   output_float, output_bool,
                   created_at, updated_at)
                VALUES
                  ('{uuid.uuid4()}', '{row.span_id}', '{row.trace_id}',
                   '{eval_config.id}', 'span',
                   {row.eval_value}, {int((row.eval_value or 0) >= 0.5)},
                   '{created}', '{created}')
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
        created = row.created_at.strftime("%Y-%m-%d %H:%M:%S")
        # CH expects output_str_list as a JSON string ("[\"good\"]"). output_str
        # mirrors the prod dual-write (single canonical value) — the choice
        # not_contains handler ANDs ``NOT (... OR output_str ILIKE ...)`` and a
        # NULL output_str collapses the whole predicate to NULL.
        choice_json = json.dumps([row.choice_value])
        for table in self._EVAL_LOGGER_TABLES:
            self.ch.command(
                f"""
                INSERT INTO {self.ch_database}.{table}
                  (id, observation_span_id, trace_id,
                   custom_eval_config_id, target_type,
                   output_str_list, output_str,
                   created_at, updated_at)
                VALUES
                  ('{uuid.uuid4()}', '{row.span_id}', '{row.trace_id}',
                   '{eval_config.id}', 'span',
                   '{choice_json}', '{row.choice_value}',
                   '{created}', '{created}')
                """
            )

    def _insert_pf_eval_pg_ch(self, row: SeededRow, trace, eval_config) -> None:
        """PASS_FAIL EvalLogger: output_bool set (output_float null)."""
        EvalLogger.objects.create(
            id=uuid.uuid4(),
            observation_span_id=row.span_id,
            trace=trace,
            custom_eval_config=eval_config,
            target_type=EvalTargetType.SPAN,
            output_bool=bool(row.pf_value),
        )
        created = row.created_at.strftime("%Y-%m-%d %H:%M:%S")
        for table in self._EVAL_LOGGER_TABLES:
            self.ch.command(
                f"""
                INSERT INTO {self.ch_database}.{table}
                  (id, observation_span_id, trace_id,
                   custom_eval_config_id, target_type,
                   output_bool,
                   created_at, updated_at)
                VALUES
                  ('{uuid.uuid4()}', '{row.span_id}', '{row.trace_id}',
                   '{eval_config.id}', 'span',
                   {int(bool(row.pf_value))},
                   '{created}', '{created}')
                """
            )

    def _insert_annotations(self, row: SeededRow, trace, project, cfg) -> list:
        """Create the numeric / text / thumbs / categorical Scores for a
        sp_idx==2 span and return them for CH mirroring. The numeric Score
        carries the annotator user for s_idx ∈ {0,1}."""
        annotator = cfg["annotator_user"] if row.ann_annotator_is_user else None
        scores = [
            self._create_score(
                row,
                trace,
                project,
                cfg["numeric_label"],
                {"value": row.annotation_value},
                annotator=annotator,
                scope=row.annotation_scope,
            ),
            self._create_score(
                row,
                trace,
                project,
                cfg["text_label"],
                {"text": row.ann_text},
                scope=row.annotation_scope,
            ),
            self._create_score(
                row,
                trace,
                project,
                cfg["thumbs_label"],
                {"value": row.ann_thumb},
                scope=row.annotation_scope,
            ),
            self._create_score(
                row,
                trace,
                project,
                cfg["categorical_label"],
                {"selected": row.ann_selected},
                scope=row.annotation_scope,
            ),
        ]
        return scores

    def _create_score(
        self,
        row: SeededRow,
        trace,
        project,
        label,
        value,
        annotator=None,
        scope: str = "span",
    ):
        """Create one PG Score and return it for CH mirroring.

        ``scope`` picks the Score source: "span" attaches to the row's span,
        "trace" attaches to its trace (the shape the voice grid creates).
        """
        from model_hub.models.choices import QueueItemSourceType, ScoreSource
        from model_hub.models.score import Score

        if scope == "trace":
            source_kwargs = {
                "source_type": QueueItemSourceType.TRACE.value,
                "trace_id": row.trace_id,
            }
        else:
            source_kwargs = {
                "source_type": QueueItemSourceType.OBSERVATION_SPAN.value,
                "observation_span_id": row.span_id,
            }
        score = Score.objects.create(
            **source_kwargs,
            label=label,
            value=value,
            annotator=annotator,
            organization=project.organization,
            workspace=project.workspace,
            score_source=ScoreSource.HUMAN.value,
        )
        return score

    # ------- voice corpus ---------------------------------------------------

    def seed_voice_corpus(self, project) -> dict:
        """Seed 24 voice-call root spans (observation_type='conversation',
        parent_span_id=NULL) into ``project``. Each call is its own trace;
        per-span attribute variation matches the base corpus so the same
        FilterCase predicates discriminate identically."""
        cfg = self._setup_shared_configs(project)

        rows: list[SeededRow] = []
        traces = []
        span_objs = []
        score_objs = []
        sess = TraceSession.objects.create(
            id=uuid.uuid4(), project=project, name="voice_session"
        )

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
            traces.append(trace)

            row = self._build_row(project, trace, sess, s_idx, t_idx, sp_idx)
            # Force voice-call shape.
            row.observation_type = "conversation"
            row.parent_span_id = None

            span_objs.append(self._insert_span_pg(row, trace, project))
            self._insert_evals(row, trace, cfg)
            if row.has_annotation:
                # Voice annotations land trace-scoped in prod (the voice grid
                # is trace-backed) — seed the same shape here.
                row.annotation_scope = "trace"
                score_objs.extend(self._insert_annotations(row, trace, project, cfg))
            rows.append(row)

        self.seeded = rows
        self._flush_ch(span_objs, traces, [sess], score_objs)
        return {"span_count": len(rows), "session_count": 1, "trace_count": 24}

    # ------- accessors ------------------------------------------------------

    def expected_predicate_count(self, predicate) -> int:
        """Convenience for tests: count rows matching a per-row predicate."""
        return sum(1 for r in self.seeded if predicate(r))
