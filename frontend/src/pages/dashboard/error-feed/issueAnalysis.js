// The analysis grouping stores for an issue, as the Root cause tab and the
// Analysis (Markdown) export show it. `description` is the stored mechanism;
// `fix_hypothesis` and `falsifier` come with it. Eval and older scan issues
// have no stored analysis, and both places say why.

export const SAMPLE_TRACE_LIMIT = 10;

export const ANALYSIS_LABELS = {
  cause: "Likely cause",
  change: "Suggested change",
  wrongIf: "Wrong if",
};

export const hasStoredAnalysis = (issue) => Boolean(issue?.fix_hypothesis);

export const missingAnalysisReason = (issue) =>
  issue?.source === "eval"
    ? "This issue was grouped from eval results, so the Error Feed stored no likely cause or suggested change for it."
    : "The Error Feed stored no likely cause or suggested change for this issue.";

// What the issue page shows under each label, in order. Without a stored
// analysis the description is an eval's explanation (or absent), not a cause.
export const analysisRows = (issue) => {
  if (hasStoredAnalysis(issue)) {
    return [
      [ANALYSIS_LABELS.cause, issue.description],
      [ANALYSIS_LABELS.change, issue.fix_hypothesis],
      [ANALYSIS_LABELS.wrongIf, issue.falsifier],
    ].filter(([, text]) => text);
  }
  if (!issue?.description) return [];
  return [
    [
      issue.source === "eval" ? "Eval explanation" : "Description",
      issue.description,
    ],
  ];
};

const day = (iso) => (iso ? String(iso).slice(0, 10) : null);

const traces = (count) =>
  `${count.toLocaleString()} trace${count === 1 ? "" : "s"}`;

// Title and caption may be blank on the wire; build the line from what is set.
const insightLine = ({ title, value, caption }) => {
  const body = [value, caption].filter(Boolean).join(". ");
  return title ? `- **${title}**: ${body}` : `- ${body}`;
};

// A fence one backtick longer than any run inside the text keeps it intact.
const fenced = (text) => {
  const longest = Math.max(
    2,
    ...(String(text).match(/`+/g) ?? []).map((run) => run.length),
  );
  const ticks = "`".repeat(longest + 1);
  return `${ticks}text\n${text}\n${ticks}`;
};

const traceSection = (trace) => {
  const evidence = trace.evidence ?? {};
  const facts = [
    trace.timestamp && `Time: ${trace.timestamp}`,
    evidence.score != null && `Score: ${evidence.score}`,
  ].filter(Boolean);
  return [
    `### ${trace.id}`,
    facts.length && facts.join(" · "),
    evidence.judge_reason && `Judge reason: ${evidence.judge_reason}`,
    evidence.input && `Input:\n\n${fenced(evidence.input)}`,
    evidence.output && `Output:\n\n${fenced(evidence.output)}`,
  ]
    .filter(Boolean)
    .join("\n\n");
};

/**
 * The issue's stored analysis, its pattern and a sample of its traces as one
 * Markdown document. Built from data already on the page; no model call.
 */
export const analysisToMarkdown = (issue, overview) => {
  const sample = (overview?.representative_traces ?? []).slice(
    0,
    SAMPLE_TRACE_LIMIT,
  );
  const insights = (overview?.pattern_summary?.insights ?? []).filter(
    (i) => i.value || i.caption,
  );
  const meta = [
    issue.cluster_id,
    traces(issue.trace_count ?? 0),
    day(issue.first_seen) && `first seen ${day(issue.first_seen)}`,
    day(issue.last_seen) && `last seen ${day(issue.last_seen)}`,
  ].filter(Boolean);
  // A fallback title can be the multi-line mechanism; a heading takes one line.
  const title = String(issue.error?.name || issue.cluster_id).split("\n")[0];

  return [
    `# ${title}`,
    meta.join(" · "),
    !hasStoredAnalysis(issue) && `> ${missingAnalysisReason(issue)}`,
    ...analysisRows(issue).map(([label, text]) => `## ${label}\n\n${text}`),
    insights.length && `## Pattern\n\n${insights.map(insightLine).join("\n")}`,
    sample.length
      ? `## Sample traces\n\nThe ${traces(sample.length)} seen most recently.\n\n${sample
          .map(traceSection)
          .join("\n\n")}`
      : "## Sample traces\n\nNo traces to show.",
  ]
    .filter(Boolean)
    .join("\n\n")
    .concat("\n");
};
