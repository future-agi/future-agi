"""
Reusable model builder functions for ai_tools tests.

Each builder creates a minimal valid instance of a Django model, accepting
keyword overrides. All functions follow the pattern:

    make_<entity>(tool_context, **overrides) -> model instance

This avoids repeating ORM setup across test files.
"""

import uuid

# ---------------------------------------------------------------------------
# Dataset helpers
# ---------------------------------------------------------------------------


def make_dataset(
    tool_context, *, name="Test Dataset", source="sdk", columns=None, **kwargs
):
    """Create a Dataset with optional columns.

    Args:
        tool_context: ToolContext fixture.
        name: Dataset name.
        source: Dataset source.
        columns: List of (col_name, col_type) tuples. Defaults to [("input", "text"), ("output", "text")].

    Returns:
        Dataset instance with columns created and column_order set.
    """
    from model_hub.models.develop_dataset import Column, Dataset

    ds = Dataset(
        name=name,
        source=source,
        organization=tool_context.organization,
        workspace=tool_context.workspace,
        user=tool_context.user,
        column_order=[],
        model_type="GenerativeLLM",
        **kwargs,
    )
    ds.save()

    columns = columns or [("input", "text"), ("output", "text")]
    col_objs = []
    for col_name, col_type in columns:
        col = Column.objects.create(
            name=col_name,
            data_type=col_type,
            dataset=ds,
            source="OTHERS",
        )
        col_objs.append(col)

    ds.column_order = [str(c.id) for c in col_objs]
    ds.save(update_fields=["column_order"])
    return ds


def make_dataset_with_rows(tool_context, *, row_data=None, **ds_kwargs):
    """Create a Dataset with columns and populated rows.

    Args:
        tool_context: ToolContext fixture.
        row_data: List of dicts mapping column names to values.
            Defaults to [{"input": "hello", "output": "world"}].

    Returns:
        (Dataset, [Column], [Row]) tuple.
    """
    from model_hub.models.develop_dataset import Cell, Column, Row

    ds = make_dataset(tool_context, **ds_kwargs)
    cols = list(Column.objects.filter(dataset=ds, deleted=False))
    cols_by_name = {c.name: c for c in cols}

    row_data = row_data or [{"input": "hello", "output": "world"}]
    rows = []
    for i, rd in enumerate(row_data):
        row = Row.objects.create(id=uuid.uuid4(), dataset=ds, order=i)
        rows.append(row)
        for col in cols:
            Cell.objects.create(
                id=uuid.uuid4(),
                dataset=ds,
                column=col,
                row=row,
                value=str(rd.get(col.name, "")),
            )

    return ds, cols, rows


# ---------------------------------------------------------------------------
# Evaluation / EvalTemplate helpers
# ---------------------------------------------------------------------------


def make_eval_template(tool_context, *, name="test-eval", owner="system", **kwargs):
    """Create an EvalTemplate."""
    from model_hub.models.evals_metric import EvalTemplate

    defaults = {
        "name": name,
        "description": "Test evaluation template",
        "organization": tool_context.organization,
        "workspace": tool_context.workspace,
        "owner": owner,
        "eval_tags": ["test"],
    }
    defaults.update(kwargs)
    return EvalTemplate.objects.create(**defaults)


def make_evaluation(tool_context, *, eval_template=None, **kwargs):
    """Create an Evaluation."""
    from model_hub.models.evaluation import Evaluation

    if eval_template is None:
        eval_template = make_eval_template(tool_context)

    defaults = {
        "user": tool_context.user,
        "organization": tool_context.organization,
        "workspace": tool_context.workspace,
        "eval_template": eval_template,
        "model_name": "gpt-4o",
        "status": "completed",
        "value": "Pass",
        "output_type": "Pass/Fail",
        "reason": "Criteria met.",
        "runtime": 1.0,
        "metrics": {"accuracy": 0.95},
    }
    defaults.update(kwargs)
    return Evaluation.objects.create(**defaults)


# ---------------------------------------------------------------------------
# Tracing helpers
# ---------------------------------------------------------------------------


def make_project(tool_context, *, name="Test Project", **kwargs):
    """Create a Project."""
    from tracer.models.project import Project

    defaults = {
        "name": name,
        "organization": tool_context.organization,
        "workspace": tool_context.workspace,
        "user": tool_context.user,
        "model_type": "GenerativeLLM",
        "trace_type": "observe",
    }
    defaults.update(kwargs)
    return Project.objects.create(**defaults)


def make_trace(tool_context, *, project=None, name="test-trace", **kwargs):
    """Create a Trace."""
    from tracer.models.trace import Trace

    if project is None:
        project = make_project(tool_context)

    defaults = {
        "project": project,
        "name": name,
        "metadata": {"key": "value"},
        "input": {"prompt": "Hello"},
        "output": {"response": "World"},
        "tags": ["test"],
    }
    defaults.update(kwargs)
    return Trace.objects.create(**defaults)


# ---------------------------------------------------------------------------
# Error cluster helpers
# ---------------------------------------------------------------------------


def make_trace_error_analysis(tool_context, *, trace=None, project=None, **kwargs):
    """Create a TraceErrorAnalysis."""
    from tracer.models.trace_error_analysis import TraceErrorAnalysis

    if trace is None:
        trace = make_trace(tool_context, project=project)
    if project is None:
        project = trace.project

    defaults = {
        "trace": trace,
        "project": project,
        "overall_score": 3.5,
        "total_errors": 2,
        "high_impact_errors": 1,
        "medium_impact_errors": 1,
        "low_impact_errors": 0,
        "recommended_priority": "HIGH",
        "factual_grounding_score": 4.0,
        "factual_grounding_reason": "Generally accurate",
        "privacy_and_safety_score": 5.0,
        "privacy_and_safety_reason": "No issues",
        "instruction_adherence_score": 3.0,
        "instruction_adherence_reason": "Partially followed",
        "optimal_plan_execution_score": 2.5,
        "optimal_plan_execution_reason": "Suboptimal path taken",
        "insights": "The agent hallucinated in the retrieval step.",
    }
    defaults.update(kwargs)
    return TraceErrorAnalysis.objects.create(**defaults)


def make_trace_error_detail(tool_context, *, analysis=None, error_id="E001", **kwargs):
    """Create a TraceErrorDetail."""
    from tracer.models.trace_error_analysis import TraceErrorDetail

    if analysis is None:
        analysis = make_trace_error_analysis(tool_context)

    defaults = {
        "analysis": analysis,
        "error_id": error_id,
        "cluster_id": "C01",
        "category": "Thinking & Response Issues > Hallucination Errors > Hallucinated Content",
        "impact": "HIGH",
        "urgency_to_fix": "HIGH",
        "location_spans": ["span-001", "span-002"],
        "evidence_snippets": ["The agent stated X but the document says Y"],
        "description": "Agent generated content not supported by source material",
        "root_causes": [
            "Retrieval returned stale documents",
            "No grounding check performed",
        ],
        "recommendation": "Add a grounding verification step after retrieval",
        "immediate_fix": "Re-index the knowledge base",
        "trace_impact": "User received incorrect information",
        "trace_assessment": "Critical error affecting output quality",
    }
    defaults.update(kwargs)
    return TraceErrorDetail.objects.create(**defaults)


def make_error_cluster(tool_context, *, project=None, cluster_id="KC01TEST", **kwargs):
    """Create a TraceErrorGroup (error cluster)."""
    from django.utils import timezone

    from tracer.models.trace_error_analysis import TraceErrorGroup

    if project is None:
        project = make_project(tool_context, name="Cluster Project")

    now = timezone.now()
    defaults = {
        "project": project,
        "cluster_id": cluster_id,
        "error_type": "Thinking & Response Issues > Hallucination Errors > Hallucinated Content",
        "total_events": 10,
        "unique_traces": 3,
        "unique_users": 5,
        "first_seen": now - timezone.timedelta(days=7),
        "last_seen": now - timezone.timedelta(hours=2),
        "error_ids": ["E001", "E002", "E003"],
        "combined_impact": "HIGH",
        "combined_description": "Agent hallucinated content not grounded in source documents.",
        "error_count": 3,
    }
    defaults.update(kwargs)
    return TraceErrorGroup.objects.create(**defaults)


def make_error_cluster_trace(tool_context, *, cluster=None, trace=None, span=None):
    """Create an ErrorClusterTraces junction record."""
    from tracer.models.trace_error_analysis import ErrorClusterTraces

    return ErrorClusterTraces.objects.create(
        cluster=cluster,
        trace=trace,
        span=span,
    )


def make_full_error_cluster(tool_context, *, num_traces=3, cluster_id="KFULL001"):
    """Create a complete error cluster with traces, analyses, details, and junction records.

    Returns:
        (cluster, project, traces, analyses, details) tuple.
    """
    project = make_project(tool_context, name=f"Project-{cluster_id}")
    cluster = make_error_cluster(
        tool_context,
        project=project,
        cluster_id=cluster_id,
        total_events=num_traces * 2,
        unique_traces=num_traces,
    )

    traces = []
    analyses = []
    details = []
    for i in range(num_traces):
        trace = make_trace(
            tool_context,
            project=project,
            name=f"trace-{cluster_id}-{i}",
        )
        traces.append(trace)

        analysis = make_trace_error_analysis(
            tool_context,
            trace=trace,
            project=project,
            overall_score=3.0 + i * 0.5,
            total_errors=2,
        )
        analyses.append(analysis)

        d1 = make_trace_error_detail(
            tool_context,
            analysis=analysis,
            error_id=f"E{i:03d}a",
            impact="HIGH" if i == 0 else "MEDIUM",
            root_causes=[f"Root cause for trace {i}"],
            recommendation=f"Fix recommendation for trace {i}",
            evidence_snippets=[f"Evidence snippet from trace {i}"],
        )
        d2 = make_trace_error_detail(
            tool_context,
            analysis=analysis,
            error_id=f"E{i:03d}b",
            impact="LOW",
            category="Tool & System Failures > Setup Errors > Tool Missing",
            root_causes=[f"Tool not configured in trace {i}"],
            recommendation="Configure the missing tool",
        )
        details.extend([d1, d2])

        make_error_cluster_trace(tool_context, cluster=cluster, trace=trace)

    return cluster, project, traces, analyses, details


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------


def make_prompt_template(tool_context, *, name="Test Prompt", **kwargs):
    """Create a PromptTemplate."""
    from model_hub.models.run_prompt import PromptTemplate

    defaults = {
        "name": name,
        "organization": tool_context.organization,
        "workspace": tool_context.workspace,
        "created_by": tool_context.user,
    }
    defaults.update(kwargs)
    return PromptTemplate.objects.create(**defaults)


def make_prompt_version(tool_context, *, template=None, version="v1", **kwargs):
    """Create a PromptVersion."""
    from model_hub.models.run_prompt import PromptVersion

    if template is None:
        template = make_prompt_template(tool_context)

    defaults = {
        "original_template": template,
        "template_version": version,
        "prompt_config_snapshot": {
            "messages": [{"role": "system", "content": "You are a helpful assistant."}],
            "model": "gpt-4o",
            "configuration": {"temperature": 0.7, "max_tokens": 1000},
        },
        "is_default": True,
        "is_draft": True,
    }
    defaults.update(kwargs)
    return PromptVersion.objects.create(**defaults)


# ---------------------------------------------------------------------------
# Agent helpers
# ---------------------------------------------------------------------------


def make_agent_definition(tool_context, *, name="Test Agent", **kwargs):
    """Create an AgentDefinition."""
    from simulate.models import AgentDefinition

    defaults = {
        "agent_name": name,
        "agent_type": "voice",
        "languages": ["en"],
        "inbound": True,
        "organization": tool_context.organization,
        "workspace": tool_context.workspace,
    }
    defaults.update(kwargs)
    return AgentDefinition.objects.create(**defaults)


# ---------------------------------------------------------------------------
# Simulation helpers
# ---------------------------------------------------------------------------


def make_scenario(tool_context, *, name="Test Scenario", **kwargs):
    """Create a Scenario."""
    from simulate.models import Scenarios

    defaults = {
        "name": name,
        "organization": tool_context.organization,
        "workspace": tool_context.workspace,
    }
    defaults.update(kwargs)
    return Scenarios.objects.create(**defaults)


# ---------------------------------------------------------------------------
# Annotation helpers
# ---------------------------------------------------------------------------


def make_annotation_label(
    tool_context, *, name="Test Label", label_type="categorical", **kwargs
):
    """Create an AnnotationsLabels instance."""
    from model_hub.models.develop_annotations import AnnotationsLabels

    defaults = {
        "name": name,
        "type": label_type,
        "settings": {},
        "organization": tool_context.organization,
        "workspace": tool_context.workspace,
    }
    defaults.update(kwargs)
    return AnnotationsLabels.objects.create(**defaults)
