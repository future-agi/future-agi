import { formatDate } from "src/utils/report-utils";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Runtime instrumentation emits one `skill.load` span AFTER actual loading,
 * with scalar fi.skill.id, fi.skill.name, fi.skill.version_id, fi.skill.version,
 * fi.skill.digest and optionally fi.skill.requested_label. Downloads and internal
 * tests do not emit customer-load spans. No traces does not prove no usage.
 *
 * The caller must select projectId from the authorized workspace project picker.
 * This link does not confer access. Exactly one immutable version predicate
 * avoids matching a skill name and version on two different spans of a trace.
 */
export function buildSkillTraceLink({
  projectId,
  versionId,
  windowStart,
  windowEnd,
}) {
  if (
    typeof projectId !== "string" ||
    !projectId ||
    !UUID_PATTERN.test(versionId)
  )
    return null;
  const hasDates = windowStart !== undefined || windowEnd !== undefined;
  if (hasDates && (!windowStart || !windowEnd)) return null;
  const end = hasDates ? new Date(windowEnd) : new Date();
  const start = hasDates
    ? new Date(windowStart)
    : new Date(end.getTime() - 7 * 24 * 60 * 60 * 1000);
  if (
    !Number.isFinite(start.getTime()) ||
    !Number.isFinite(end.getTime()) ||
    start >= end
  )
    return null;
  const params = new URLSearchParams({ tab: "traces", selectedTab: "trace" });
  params.set(
    "observeLinkFilter",
    JSON.stringify([
      {
        column_id: "fi.skill.version_id",
        filter_config: {
          col_type: "SPAN_ATTRIBUTE",
          filter_type: "text",
          filter_op: "equals",
          filter_value: versionId,
        },
      },
    ]),
  );
  // Match the Observe date picker's local-time serialization, including seconds.
  params.set(
    "primaryTraceDateFilter",
    JSON.stringify({
      dateFilter: [formatDate(start), formatDate(end)],
      dateOption: "Custom",
    }),
  );
  return `/dashboard/observe/${encodeURIComponent(projectId)}/llm-tracing?${params.toString()}`;
}
