import {
  formatExactMoney,
  isMoneyValue,
} from "src/sections/common/format/money";
// What the result drawer's Artifacts tab shows (R2-SPEC §5.3.4). Pure:
// formats one attempt's W0b `artifacts` (transcript, end-state diff,
// container, cost breakdown) and its checks.

export const HARNESS_SUBTABS = [
  { value: "transcript", label: "Transcript" },
  { value: "files", label: "Files" },
  { value: "end_state", label: "End state" },
  { value: "evals", label: "Evals" },
  { value: "cost", label: "Cost & time" },
  { value: "container", label: "Container" },
];
export const CODE_SUBTABS = [
  { value: "transcript", label: "Transcript" },
  { value: "cost", label: "Cost & time" },
  { value: "code", label: "Code" },
];

const fmt = (n) => Number(n || 0).toLocaleString("en-US");

/** What Collect gathered, by kind (options-truth L2 G6). */
const COLLECTED_LABELS = Object.freeze({
  git_diff: "Git diff",
  final_message: "Final message",
  log: "Terminal log",
  files: "Files",
});

/** The Files sub-tab's empty line when an attempt has nothing collected. */
export const NO_FILES_TEXT =
  "Nothing collected for this attempt: turn on Git diff, Final message or Terminal log, or add file patterns, in More settings › Collect.";

/**
 * An attempt's collected files (`artifacts.collected`: the git diff, the
 * final message, the terminal log, files matching the patterns) as
 * `[{kind, label, name, text, lines}]`, in the order Collect lists them.
 * Pure.
 */
export function collectedFiles(art) {
  return (art?.collected ?? [])
    .filter((c) => c && (c.name || c.kind))
    .map((c) => {
      // L3 G6 fix 10: a pattern lists the paths it matched, or says none did
      const paths = Array.isArray(c.paths) ? c.paths : null;
      const text = paths
        ? paths.length
          ? paths.join("\n")
          : `no files matched ${c.name}`
        : String(c.text ?? "");
      const lines = paths ? paths.length : text ? text.split("\n").length : 0;
      return {
        kind: c.kind ?? "files",
        label: COLLECTED_LABELS[c.kind] ?? COLLECTED_LABELS.files,
        name: c.name ?? c.kind,
        text,
        lines,
        ...(paths ? { paths, matched: paths.length } : {}),
      };
    });
}
/**
 * The Files sub-tab's head line (L3 G6 fix 10): counts the files Collect
 * got (a pattern counts the paths it matched) and names a pattern that
 * matched nothing as such, never as collected:
 * `3 collected · changes.diff · run.log · reports/*.json (1 file)` or
 * `2 collected · changes.diff · final_message.txt · reports/*.json: no match`.
 * Pure.
 */
export function collectedSummary(files = []) {
  const count = files.reduce(
    (n, f) => n + (f.paths ? f.matched ?? f.paths.length : 1),
    0,
  );
  const names = files.map((f) =>
    f.paths
      ? f.paths.length
        ? `${f.name} (${f.paths.length} ${f.paths.length === 1 ? "file" : "files"})`
        : `${f.name}: no match`
      : f.name,
  );
  return [`${count} collected`, ...names].join(" · ");
}
const MONEY_KEY = /amount|price|total|refund|credit|usd|cost/i;

/** `$129.00`; costs under a cent keep 3 significant digits (`$0.004`). */
export function money(v) {
  return formatExactMoney(v);
}

/** `2.9 s` / `74 s` / `2 min`. */
export function seconds(ms) {
  const s = Number(ms) / 1000;
  if (!Number.isFinite(s)) return "—";
  if (s < 10) return Number.isInteger(s) ? `${s} s` : `${s.toFixed(1)} s`;
  if (s < 120) return `${Math.round(s)} s`;
  return `${Math.round(s / 60)} min`;
}

const valueText = (key, v) => {
  if (v === null || v === undefined) return "none";
  if (isMoneyValue(v) && MONEY_KEY.test(key)) return money(v);
  if (typeof v === "object")
    return Object.entries(v)
      .map(([k, x]) => valueText(k, x))
      .join(", ");
  return String(v);
};

/**
 * One end-state diff line as two columns: `{path, change, ok}`, e.g.
 * `refunds` · `+1 row (A-8830, $129.00)`; `orders.A-8830.refund_status` ·
 * `none → issued`.
 */
export function endStateRow(d) {
  const leaf = String(d?.path ?? "")
    .split(".")
    .pop();
  let change;
  if (d?.before === null && d?.after && typeof d.after === "object")
    change = `+1 row (${valueText(leaf, d.after)})`;
  else if (d?.after === null && d?.before && typeof d.before === "object")
    change = `−1 row (${valueText(leaf, d.before)})`;
  else change = `${valueText(leaf, d?.before)} → ${valueText(leaf, d?.after)}`;
  return { path: d?.path ?? "", change, ok: d?.ok !== false };
}

/**
 * `14 turns · 7 tool calls · 69 s`; a harness that edits files (Aider)
 * reads `3 turns · 2 file edits · 52 s` (it makes no tool calls).
 */
export function transcriptLine(summary, latencyMs, transcript = []) {
  const edits = transcript.filter((t) => t.role === "edit").length;
  const turns =
    summary?.turns ??
    transcript.filter((t) => t.role !== "tool" && t.role !== "edit").length;
  const tools =
    summary?.tool_calls ?? transcript.filter((t) => t.role === "tool").length;
  return [
    `${fmt(turns)} ${turns === 1 ? "turn" : "turns"}`,
    edits || summary?.edits != null
      ? `${fmt(summary?.edits ?? edits)} ${(summary?.edits ?? edits) === 1 ? "file edit" : "file edits"}`
      : `${fmt(tools)} ${tools === 1 ? "tool call" : "tool calls"}`,
    Number.isFinite(Number(latencyMs)) ? seconds(latencyMs) : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

/** The transcript as ChatMessageView messages. */
export function transcriptMessages(transcript = []) {
  return transcript.map((t) =>
    t.role === "edit"
      ? // a file edit is the model's own turn (Aider makes no tool calls)
        { role: "assistant", content: t.text ?? "" }
      : t.role === "tool"
        ? { role: "tool", name: t.tool ?? "tool", content: t.text ?? "" }
        : {
            role: t.role === "user" ? "user" : "assistant",
            content: t.text ?? "",
          },
  );
}

/**
 * `$0.21 · 9 model calls · 49,000 in / 4,200 out tokens · simulated
 * customer $0.002 · wall 69 s` (+ the setup / agent / evals split when the
 * breakdown carries it).
 */
export function costLine(cost, latencyMs) {
  if (!cost) return null;
  const split =
    cost.setup_ms !== undefined && cost.agent_ms !== undefined
      ? ` (setup ${seconds(cost.setup_ms)} · agent ${seconds(cost.agent_ms)}${cost.evals_ms !== undefined ? ` · evals ${seconds(cost.evals_ms)}` : ""})`
      : "";
  return [
    money(cost.total_usd),
    `${fmt(cost.model_calls)} model ${cost.model_calls === 1 ? "call" : "calls"}`,
    `${fmt(cost.tokens_in)} in / ${fmt(cost.tokens_out)} out tokens`,
    cost.user_sim_usd ? `simulated customer ${money(cost.user_sim_usd)}` : null,
    Number.isFinite(Number(latencyMs))
      ? `wall ${seconds(latencyMs)}${split}`
      : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

/**
 * Eval rows of one attempt: checks (✓ / ✕ / – not reached) then judge evals
 * with the judge's reason.
 * @param {object[]} checks  `[{key, name, passed}]`
 * @param {object} scores  trials_detail `scores` (`eval:<key>` → {value, reason})
 */
export function evalRows(checks = [], scores = {}) {
  return (checks ?? []).map((c) => {
    const s = scores?.[`eval:${c.key}`] ?? scores?.[`check:${c.key}`];
    const judge = Boolean(scores?.[`eval:${c.key}`]);
    return {
      key: c.key,
      name: c.name ?? c.key,
      glyph:
        c.passed === null || c.passed === undefined
          ? "–"
          : c.passed
            ? "✓"
            : "✕",
      passed: c.passed,
      type: judge ? "Judge" : "Check",
      reason: judge ? s?.reason ?? null : null,
    };
  });
}

/**
 * `acme-support-sandbox:1.4 · exit 0`; a repository keeps its full
 * `owner/repo@branch` (`acme/support-desk@release-2 · exit 1`).
 */
export function containerLine(container) {
  if (!container) return null;
  const raw = String(container.image ?? "");
  const image = raw.includes("@") ? raw : raw.split("/").pop();
  return `${image} · exit ${container.exit_code ?? "—"}`;
}
