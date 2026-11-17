// Agent specs and names of a run source (options-truth L1 C9). Pure.
// dataset integration FE-0: the live-connection command builders (the connect line,
// its install extra, the serve file and Copy all) are removed with the
// live connection itself: that command does not exist. What stays is
// shared by the iterate guard and the harness snippets.
//
//   specOf(entry, lang)               → `agent.py:graph`
//   defaultAgentName({kind, entry})   → `my_agent` / `claude-code`
//   runCommand({entry, runId, rows, trials, limits, params, watch})
import { catalogEntry } from "src/sections/common/RunSources/catalog";
import { sdkRunCommand } from "src/sections/common/RunSources/snippets/lines";

/** Harness id → its `--harness` flag (`claude_code` → `claude-code`). */
export const HARNESS_FLAG = Object.freeze({
  claude_code: "claude-code",
  codex_cli: "codex",
  gemini_cli: "gemini-cli",
  openhands: "openhands",
  aider: "aider",
  goose: "goose",
  mini_swe_agent: "mini-swe-agent",
  custom_command: "custom",
});

export const harnessFlag = (id) =>
  HARNESS_FLAG[id] ?? String(id ?? "custom").replace(/_/g, "-");

const entryOf = (entry, entryId) =>
  typeof entry === "string"
    ? catalogEntry(entry)
    : entry ?? (entryId ? catalogEntry(entryId) : null);

const tsOnly = (e) =>
  (e?.languages ?? []).includes("typescript") &&
  !(e?.languages ?? []).includes("python");

/** The language a code entry connects in (`lang` wins when the entry has it). */
export function connectLang(entry, lang) {
  const e = entryOf(entry);
  const langs = e?.languages ?? [];
  if (lang && (langs.length === 0 || langs.includes(lang))) return lang;
  return tsOnly(e) ? "typescript" : "python";
}

/** The spec a code agent connects with (`agent.py:graph`, `src/agent.ts:agent`). */
export function specOf(entry, lang) {
  const e = entryOf(entry);
  const l = connectLang(e, lang);
  const spec = e?.agent?.spec;
  if (spec && (l === "typescript") === /\.ts:/.test(spec)) return spec;
  return l === "typescript" ? "src/agent.ts:target" : spec ?? "agent.py:target";
}

/**
 * The `--name` an agent connects as when the caller has none: code agents
 * `my_agent`, harnesses their flag (`claude-code`).
 */
export function defaultAgentName({ kind = "code", entry, entryId } = {}) {
  const e = entryOf(entry, entryId);
  if (kind === "harness") return harnessFlag(e?.id ?? entryId);
  return "my_agent";
}

/** The API key a harness needs (by the vendor of its CLI). */
export function harnessKeyEnv(entryId) {
  if (entryId === "codex_cli") return "OPENAI_API_KEY";
  if (entryId === "gemini_cli") return "GEMINI_API_KEY";
  if (entryId === "openhands") return "LLM_API_KEY";
  return "ANTHROPIC_API_KEY";
}

/**
 * One SDK run command (S3 / I5): `futureagi run <file>:<attr> --run <uuid>
 * [--rows …] [--trials N] [--concurrency N] [--max-cost-usd X]
 * [--timeout-s N] [--param k=v] [--watch]`. `entry` is a catalog entry, an
 * entry id or a `file:attr` spec.
 */
export function runCommand({
  entry,
  entryId,
  spec,
  runId,
  rows,
  trials,
  limits,
  params,
  watch = false,
} = {}) {
  const asSpec = typeof entry === "string" && entry.includes(":");
  const e = asSpec ? null : entryOf(entry, entryId);
  const target = spec ?? (asSpec ? entry : specOf(e, "python"));
  return sdkRunCommand({
    entry: target,
    runId,
    rows,
    trials,
    limits,
    params,
    watch,
  });
}
