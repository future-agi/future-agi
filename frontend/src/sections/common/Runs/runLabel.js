// Run label `#4B` (D2): experiment ordinal + variant letter. One spelling
// everywhere a run is named (chips, headers, tooltips, pills).

/** Letter for a 0-based variant index: 0 -> "A", 25 -> "Z", 26 -> "AA". */
export function variantLetter(index) {
  if (!Number.isInteger(index) || index < 0) return "";
  let n = index;
  let out = "";
  do {
    out = String.fromCharCode(65 + (n % 26)) + out;
    n = Math.floor(n / 26) - 1;
  } while (n >= 0);
  return out;
}

/** `#4B` for a canonical Run (uses `label`, else ordinal + letter). */
export function runLabel(run) {
  if (!run) return "";
  if (run.label) return run.label;
  const ord = run.experiment_ordinal ?? run.ordinal;
  if (ord != null && run.letter) return `#${ord}${run.letter}`;
  return run.run_id ? String(run.run_id) : "";
}

/**
 * `{ordinal: 4, letter: "B"}` from `#4B`, or null. W1-1 kept runs: a later
 * run of the config adds `attempt` (`#2A·2` → attempt 2), typed as `#2A·2`,
 * `#2A.2`, `#2A-2` or `#2A 2` because people can't type `·`.
 */
export function parseRunLabel(label) {
  const m = /^#?(\d+)([A-Z]+)(?:\s*[·.-]\s*|\s+)?(\d+)?$/.exec(
    String(label ?? "").trim(),
  );
  if (!m) return null;
  const attempt = m[3] ? Number(m[3]) : null;
  return {
    ordinal: Number(m[1]),
    letter: m[2],
    ...(attempt && attempt > 1 ? { attempt } : {}),
  };
}

/** `#2A`, or `#2A·2` for the second run of the config (W1-1). */
export function formatRunLabel({ ordinal, letter, attempt } = {}) {
  if (ordinal == null || !letter) return "";
  return `#${ordinal}${letter}${Number(attempt) > 1 ? `·${attempt}` : ""}`;
}

/** A label in a file name or CSV: `2A`, `2A-2` (W1-1). */
/**
 * W1-1: the base name of a comparison download, every label file-safe
 * (`#2A·2`, `#2B` → `compare-2A-2-2B`; a kept run's `·` never reaches a
 * file name).
 */
export const compareFileBase = (labels = []) =>
  `compare-${(labels ?? []).map((l) => fileSafeLabel(l)).join("-")}`;

export function fileSafeLabel(label) {
  const p = parseRunLabel(label);
  if (!p)
    return String(label ?? "")
      .replace(/^#/, "")
      .replace(/·/g, "-");
  return `${p.ordinal}${p.letter}${p.attempt ? `-${p.attempt}` : ""}`;
}

/** Short human name: `Support Agent v3`, `Support Reply V4 × gpt-4o-mini`. */
export function runShortName(run) {
  if (!run) return "";
  if (run.name) return run.name;
  const t = run.target ?? {};
  if (run.target_type === "prompt" && t.prompt_name)
    return [t.prompt_name, t.prompt_version_name, t.model && `× ${t.model}`]
      .filter(Boolean)
      .join(" ");
  if (run.target_type === "agent" && t.graph_name)
    return [t.graph_name, t.version_name].filter(Boolean).join(" ");
  if (run.target_type === "external")
    return [t.branch, t.commit].filter(Boolean).join("@") || "External run";
  if (run.target_type === "http" && t.url)
    return `${t.method ?? "POST"} ${t.url}`;
  return run.experiment_name ?? "";
}

/** `#4B Support Agent v3` (label + short name) for menus and tooltips. */
export const runTitle = (run) =>
  [runLabel(run), runShortName(run)].filter(Boolean).join(" ");

/** Index a list of runs by `run_id`. */
export const runsById = (runs) =>
  Object.fromEntries((runs ?? []).filter(Boolean).map((r) => [r.run_id, r]));

const firstPart = (s) =>
  String(s ?? "")
    .split(" · ")[0]
    .split(" × ")[0]
    .trim();

/**
 * IT-R2 G2 P2/P7: the name a person says for a run — what they picked, not
 * the connector or the model (rule 17): `Claude Code`, `Support Agent
 * v3.1`, `Support Ops Reply V1`, `CrewAI support crew`, `Acme voice v2`.
 * The model goes on its own line (`runModelsText`).
 * @param {object} run  canonical Run (with `config` when available)
 */
export function runSpokenName(run) {
  if (!run) return "";
  const c = run.config ?? {};
  const t = run.target ?? {};
  const kind = c.kind ?? run.kind ?? run.target_type;
  let raw;
  if (kind === "harness") raw = c.harness?.name ?? t.harness ?? run.name;
  else if (kind === "agent")
    raw = [
      c.agent?.name ?? t.graph_name,
      String(c.agent?.version ?? t.version_name ?? "").replace(
        /\s*\(.*\)$/,
        "",
      ),
    ]
      .filter(Boolean)
      .join(" ");
  else if (kind === "prompt")
    raw = [
      c.prompt?.name ?? t.prompt_name,
      c.prompt?.version ?? t.prompt_version_name,
    ]
      .filter(Boolean)
      .join(" ");
  else raw = run.name;
  const n = firstPart(raw || runShortName(run));
  return n ? `${n[0].toUpperCase()}${n.slice(1)}` : "";
}

/** `claude-sonnet-4-5`, `gpt-4.1-mini +1` (every model the run called). */
export function runModelsText(run) {
  const c = run?.config ?? {};
  const ids = [
    ...new Set(
      [
        ...(Array.isArray(c.models) ? c.models.map((m) => m?.id) : []),
        c.model?.id,
        c.voice?.llm?.model,
        run?.target?.model,
      ].filter(Boolean),
    ),
  ];
  if (!ids.length) return "";
  return ids.length > 1 ? `${ids[0]} +${ids.length - 1}` : ids[0];
}
