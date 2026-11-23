import { useErrorFeedOverview } from "src/api/errorFeed/mockAdapter";
import { enqueueSnackbar } from "notistack";
import { downloadCsv, tracesToCsv } from "./exportTracesCsv";

/*
  Export (#65): the issue's traces as dev's dataset-ready CSV, or the issue's
  analysis — what it is, the likely cause, the suggested fix, the pattern and
  the traces it rests on — as one Markdown file to share or attach.
*/
const download = (text, filename, type) => {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};

const word = (v) =>
  v ? String(v).replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()) : "—";

const analysisOf = (issue, overview, rootCause) => {
  const traces = overview?.representative_traces || [];
  const lines = [
    `# ${issue.error?.name || issue.cluster_id}`,
    "",
    `${issue.cluster_id} · ${word(issue.severity)} · ${word(issue.status)} · ${issue.trace_count ?? traces.length} traces · ${issue.users_affected ?? "—"} users`,
    "",
  ];
  /* The same cause and fix the page shows: the issue's suggested fix first. */
  const suggested = issue.suggested_fix || null;
  const cause = suggested?.cause || rootCause?.headline;
  if (cause) lines.push("## Likely cause", "", cause, "");
  const fix = suggested?.change
    ? suggested
    : rootCause?.fix && typeof rootCause.fix === "object"
      ? rootCause.fix
      : null;
  const fixText = fix
    ? [fix.change, fix.wrongIf && `Wrong if: ${fix.wrongIf}`]
        .filter(Boolean)
        .join("\n\n")
    : typeof rootCause?.fix === "string"
      ? rootCause.fix
      : "";
  if (fixText) lines.push("## Suggested fix", "", fixText, "");
  if (overview?.pattern_summary)
    lines.push("## Pattern", "", overview.pattern_summary, "");
  if (traces.length) {
    lines.push("## Traces", "");
    traces.forEach((t) => {
      const reason = t.evidence?.judge_reason;
      lines.push(
        `- ${t.id}${t.timestamp ? ` · ${t.timestamp}` : ""}${reason ? ` — ${reason}` : ""}`,
      );
    });
    lines.push("");
  }
  return lines.join("\n");
};

/** The issue's two exports, for the header's ⋯ menu. */
export function useIssueExport(issue, rootCause) {
  const { data: overview } = useErrorFeedOverview(issue?.cluster_id);
  const traces = overview?.representative_traces || [];

  const exportTraces = () => {
    if (!traces.length) {
      enqueueSnackbar("No traces to export", { variant: "info" });
      return;
    }
    downloadCsv(
      tracesToCsv(traces),
      `error-feed-${issue.cluster_id}-traces.csv`,
    );
    const total = overview?.representative_total ?? traces.length;
    enqueueSnackbar(
      total > traces.length
        ? `Exported the latest ${traces.length} of ${total} traces`
        : `Exported ${traces.length} trace${traces.length === 1 ? "" : "s"}`,
      { variant: total > traces.length ? "warning" : "success" },
    );
  };
  const exportAnalysis = () => {
    download(
      analysisOf(issue, overview, rootCause),
      `error-feed-${issue.cluster_id}-analysis.md`,
      "text/markdown;charset=utf-8",
    );
    enqueueSnackbar("Exported the analysis", { variant: "success" });
  };
  return { exportTraces, exportAnalysis, hasTraces: traces.length > 0 };
}
