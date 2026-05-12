from django.core.exceptions import ValidationError
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import (
    dashboard_link,
    format_datetime,
    key_value_block,
    markdown_table,
    section,
    truncate,
)
from ai_tools.registry import register_tool
from ai_tools.tools.annotation_queues._utils import clean_ref, uuid_text


def _candidate_sessions_result(
    context: ToolContext,
    title: str = "Session Required",
    detail: str = "",
    search: str = "",
) -> ToolResult:
    from django.db.models import Q
    from tracer.models.trace_session import TraceSession

    qs = TraceSession.objects.select_related("project").filter(
        project__organization=context.organization
    )
    if context.workspace:
        qs = qs.filter(
            Q(project__workspace=context.workspace) | Q(project__workspace__isnull=True)
        )
    search = clean_ref(search)
    if search:
        qs = qs.filter(name__icontains=search)
    sessions = list(qs.order_by("-created_at")[:10])
    rows = [
        [
            f"`{session.id}`",
            truncate(session.name or "-", 36),
            session.project.name if session.project else "-",
            format_datetime(session.created_at),
        ]
        for session in sessions
    ]
    body = detail or ""
    if rows:
        body = (body + "\n\n" if body else "") + markdown_table(
            ["ID", "Name", "Project", "Created"],
            rows,
        )
    else:
        body = body or "No trace sessions found in this workspace."
    return ToolResult(
        content=section(title, body),
        data={
            "requires_session_id": True,
            "sessions": [
                {"id": str(session.id), "name": session.name} for session in sessions
            ],
        },
    )


def _resolve_session(session_ref: str, context: ToolContext):
    from django.db.models import Q
    from tracer.models.trace_session import TraceSession

    ref = clean_ref(session_ref)
    if not ref:
        return None, _candidate_sessions_result(context)
    qs = TraceSession.objects.select_related("project").filter(
        project__organization=context.organization
    )
    if context.workspace:
        qs = qs.filter(
            Q(project__workspace=context.workspace) | Q(project__workspace__isnull=True)
        )
    ref_uuid = uuid_text(ref)
    try:
        if ref_uuid:
            return qs.get(id=ref_uuid), None
        exact = qs.filter(name__iexact=ref)
        if exact.count() == 1:
            return exact.first(), None
        fuzzy = qs.filter(name__icontains=ref)
        if fuzzy.count() == 1:
            return fuzzy.first(), None
    except (TraceSession.DoesNotExist, ValidationError, ValueError, TypeError):
        pass
    return None, _candidate_sessions_result(
        context,
        "Session Not Found",
        f"Session `{ref}` was not found. Use one of these IDs instead.",
        search="" if ref_uuid else ref,
    )


class GetSessionInput(PydanticBaseModel):
    session_id: str = Field(
        default="",
        description="Trace session name or UUID to retrieve",
    )


@register_tool
class GetSessionTool(BaseTool):
    name = "get_session"
    description = (
        "Returns detailed information about a trace session, including all "
        "traces in the session, timeline, total duration, and trace count."
    )
    category = "tracing"
    input_model = GetSessionInput

    def execute(self, params: GetSessionInput, context: ToolContext) -> ToolResult:

        from tracer.models.trace import Trace

        session, unresolved = _resolve_session(params.session_id, context)
        if unresolved:
            return unresolved

        # Verify the session's project is accessible in the current workspace
        if session.project_id:
            from tracer.models.project import Project

            if not Project.objects.filter(id=session.project_id).exists():
                return ToolResult.not_found("Session", str(params.session_id))

        # Get traces in this session
        traces = (
            Trace.objects.filter(session=session)
            .select_related("project")
            .order_by("created_at")
        )

        trace_count = traces.count()

        # Calculate session duration
        first_trace = traces.first()
        last_trace = traces.last()
        duration_str = "—"
        if (
            first_trace
            and last_trace
            and first_trace.created_at
            and last_trace.created_at
        ):
            duration_secs = (
                last_trace.created_at - first_trace.created_at
            ).total_seconds()
            if duration_secs < 60:
                duration_str = f"{duration_secs:.1f}s"
            elif duration_secs < 3600:
                duration_str = f"{duration_secs / 60:.1f}m"
            else:
                duration_str = f"{duration_secs / 3600:.1f}h"

        project_name = session.project.name if session.project else "—"

        info = key_value_block(
            [
                ("ID", f"`{session.id}`"),
                ("Name", session.name or "—"),
                ("Project", project_name),
                ("Bookmarked", "Yes" if session.bookmarked else "No"),
                ("Trace Count", str(trace_count)),
                ("Duration", duration_str),
                (
                    "First Trace",
                    format_datetime(first_trace.created_at) if first_trace else "—",
                ),
                (
                    "Last Trace",
                    format_datetime(last_trace.created_at) if last_trace else "—",
                ),
                ("Created", format_datetime(session.created_at)),
            ]
        )

        content = section(f"Session: {session.name or str(session.id)}", info)

        # Trace list
        if traces:
            content += f"\n\n### Traces ({trace_count})\n\n"
            trace_rows = []
            trace_data = []
            for t in traces[:50]:
                has_err = "Yes" if t.error and t.error != {} else "No"
                tag_str = ", ".join(t.tags[:3]) if t.tags else "—"

                trace_rows.append(
                    [
                        dashboard_link("trace", str(t.id), label=f"`{str(t.id)}`"),
                        truncate(t.name, 30) if t.name else "—",
                        has_err,
                        tag_str,
                        format_datetime(t.created_at),
                    ]
                )
                trace_data.append(
                    {
                        "id": str(t.id),
                        "name": t.name,
                        "has_error": has_err == "Yes",
                        "tags": t.tags,
                    }
                )

            content += markdown_table(
                ["ID", "Name", "Error", "Tags", "Created"], trace_rows
            )

            if trace_count > 50:
                content += f"\n\n_Showing 50 of {trace_count} traces._"
        else:
            content += "\n\n### Traces\n\n_No traces in this session._"

        data = {
            "id": str(session.id),
            "name": session.name,
            "project": project_name,
            "bookmarked": session.bookmarked,
            "trace_count": trace_count,
            "traces": trace_data if traces else [],
        }

        return ToolResult(content=content, data=data)
