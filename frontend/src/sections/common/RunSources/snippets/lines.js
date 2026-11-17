// Shared helpers of the R4 snippet generators (lane Ra). Pure.
//
// Every generated file is complete top to bottom (R4-SPEC D23): no
// placeholder, no undefined name, lines ≤ 88 characters (Python; the TS
// files follow the same limit), and a status header on its first line.

// the capability contract(4): the one API base (this build's HOST_API), never a fixed
// production host.
export { FI_BASE_URL } from "src/sections/common/commands/apiBase";
export const MAX_LINE = 88;

/**
 * File headers (options-truth L1 S1: facts, no status words). The upload
 * line says what the file does. dataset integration FE-0: the connect headers are gone.
 */
export const UPLOAD_HEADER =
  "Traces every row with traceAI, then uploads the results to POST /model-hub/experiments/v2/{EXPERIMENT_ID}/runs/{RUN_ID}/results/ (both ids are set below).";
/** The CLI (S3 / I5): `fi` is a shell reserved word, so it is `futureagi`. */
export const CLI = "futureagi";
/** Lines under the two path tabs: what each path needs (no status). */
export const PATH_STATUS = Object.freeze({
  upload: "",
  connect: "",
});

// ------------------------------------------------ one grammar (S3, C8)
/** The CI workflow file (the whole path is `.github/workflows/<CI_FILE>`). */
export const CI_FILE = "fi-experiment.yml";
export const CI_PATH = `.github/workflows/${CI_FILE}`;

/** Every runner file a screen or a download names. */
export const RUNNER_FILES = Object.freeze({
  python: "run_and_upload.py",
  typescript: "run_and_upload.ts",
  harness: "run_harness_and_upload.py",
  upload_py: "upload_results.py",
  upload_ts: "upload_results.ts",
  sandbox: "sandbox_server.py",
});

/** The flags every generated runner accepts (and only these). */
export const RUNNER_FLAGS = Object.freeze([
  "--agent",
  "--run",
  "--rows",
  "--limit",
  "--trials",
  "--concurrency",
  "--max-cost-usd",
  "--timeout-s",
  "--param",
  "--config",
  "--side-effects",
  "--dry-run",
  "--mock-tools",
  "--no-sandbox",
  "--fail-under",
]);
/** Extra flags of one runner: the harness workdir. */
export const RUNNER_EXTRA_FLAGS = Object.freeze({
  harness: ["--workdir"],
  // sandbox_server.py is not a runner: its own three flags
  sandbox: ["--port", "--host", "--check"],
  // upload_results.py reads a local rows file
  upload: ["--rows-file"],
});
export const SDK_RUN_FLAGS = Object.freeze([
  "--run",
  "--rows",
  "--trials",
  "--concurrency",
  "--max-cost-usd",
  "--timeout-s",
  "--param",
  "--watch",
]);

/** Quote one shell word only when it needs it (`'a b'`, never `#2C` bare). */
export function shellWord(v) {
  const s = String(v ?? "");
  if (s && /^[A-Za-z0-9_@%+=:,./{}$-]+$/.test(s)) return s;
  if (/^\$\{\{ [\w.]+ \}\}$/.test(s)) return s; // a CI expression
  return `'${s.replace(/'/g, "'\\''")}'`;
}

const num = (v) => {
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : null;
};

/** `all` / `failed` / `new` / `id,id` for --rows (null when absent). */
function rowsArg(rows) {
  if (rows == null || rows === "") return null;
  if (Array.isArray(rows)) return rows.length ? rows.join(",") : null;
  return String(rows);
}

function paramArgs(params) {
  const entries = Array.isArray(params)
    ? params
    : Object.entries(params ?? {}).map(([k, v]) => [k, v]);
  return entries
    .filter(([k, v]) => k && v != null && v !== "")
    .flatMap(([k, v]) => ["--param", shellWord(`${k}=${v}`)]);
}

/**
 * The one runner command line (S3, C8). Every screen builds its command
 * here; every flag it prints exists in the generated runner.
 *   lang: python | typescript | harness
 *   limits: {concurrency, max_cost_usd|maxCostUsd, timeout_s|timeoutS}
 * @returns {string}
 */
export function runnerCommand({
  lang = "python",
  entry,
  runId,
  rows,
  limit,
  trials,
  params,
  limits,
  dryRun = false,
  mockTools = false,
  noSandbox = false,
  config,
  sideEffects,
  failUnder,
  workdir,
} = {}) {
  const head =
    lang === "typescript"
      ? `npx tsx ${RUNNER_FILES.typescript}`
      : lang === "harness"
        ? `python ${RUNNER_FILES.harness}`
        : `python ${RUNNER_FILES.python}`;
  const out = [head];
  const add = (flag, v) => {
    if (v != null && v !== "") out.push(flag, shellWord(v));
  };
  if (config) add("--config", config);
  if (lang === "harness") add("--workdir", workdir ?? ".");
  else add("--agent", entry);
  add("--run", runId);
  add("--rows", rowsArg(rows));
  add("--limit", num(limit));
  const t = num(trials);
  if (t && t > 1) add("--trials", t);
  const l = limits ?? {};
  const conc = num(l.concurrency);
  if (conc && conc > 1) add("--concurrency", conc);
  add("--max-cost-usd", num(l.max_cost_usd ?? l.maxCostUsd));
  add("--timeout-s", num(l.timeout_s ?? l.timeoutS));
  out.push(...paramArgs(params));
  if (sideEffects && sideEffects !== "real") add("--side-effects", sideEffects);
  if (dryRun) out.push("--dry-run");
  if (mockTools) out.push("--mock-tools");
  if (noSandbox) out.push("--no-sandbox");
  if (failUnder != null && failUnder !== "") add("--fail-under", failUnder);
  return out.join(" ");
}

/** The first picked row (`[T5, T12]` → `[T5]`), else undefined (L3 G4 C5). */
export const firstRow = (rows) =>
  Array.isArray(rows) && rows.length ? [rows[0]] : undefined;

/**
 * options-truth L2 G4 (C1, S3): what a drawer form adds to every command
 * it prints, from the form itself (pure): tries, at a time, cost cap, time
 * limit, the params that differ from their declared default, the picked
 * rows and a skipped sandbox. Spread it into `runnerCommand`.
 * @param {{runId?: string, limits?: object, params?: object,
 *   schema?: {name: string, default?: unknown}[], rowIds?: string[],
 *   noSandbox?: boolean}} p
 * @returns {{runId?: string, trials?: number, limits: object,
 *   params: object, rows?: string[], noSandbox: boolean}}
 */
export function runFlagsOf({
  runId,
  limits = {},
  params = {},
  schema,
  rowIds = [],
  noSandbox = false,
} = {}) {
  const values = params ?? {};
  const changed = Array.isArray(schema)
    ? Object.fromEntries(
        schema
          .filter(
            (p) =>
              p?.name &&
              p.name in values &&
              values[p.name] != null &&
              JSON.stringify(values[p.name]) !==
                JSON.stringify(p.default ?? null),
          )
          .map((p) => [p.name, values[p.name]]),
      )
    : { ...values };
  const l = limits ?? {};
  return {
    ...(runId ? { runId } : {}),
    ...(num(l.tries) ? { trials: num(l.tries) } : {}),
    limits: {
      concurrency: l.concurrency ?? null,
      maxCostUsd: l.maxCostUsd ?? l.max_cost_usd ?? null,
      timeoutS: l.timeoutS ?? l.timeout_s ?? null,
    },
    params: changed,
    ...(rowIds?.length ? { rows: [...rowIds] } : {}),
    noSandbox: Boolean(noSandbox),
  };
}

/**
 * The SDK run command (S3 / I5): `futureagi run <entry> --run <uuid>
 * [--rows …] [--trials N] [--concurrency N] [--max-cost-usd X]
 * [--timeout-s N] [--param k=v] [--watch]`.
 * @returns {string}
 */
export function sdkRunCommand({
  entry,
  runId,
  rows,
  trials,
  limits,
  params,
  watch = false,
} = {}) {
  const out = [CLI, "run", shellWord(entry || "agent.py:agent")];
  if (runId) out.push("--run", shellWord(runId));
  const r = rowsArg(rows);
  if (r && r !== "all") out.push("--rows", shellWord(r));
  const t = num(trials);
  if (t && t > 1) out.push("--trials", String(t));
  const l = limits ?? {};
  const conc = num(l.concurrency);
  if (conc && conc > 1) out.push("--concurrency", String(conc));
  const cost = num(l.max_cost_usd ?? l.maxCostUsd);
  if (cost) out.push("--max-cost-usd", String(cost));
  const time = num(l.timeout_s ?? l.timeoutS);
  if (time) out.push("--timeout-s", String(time));
  out.push(...paramArgs(params));
  if (watch) out.push("--watch");
  return out.join(" ");
}

/**
 * Wrap a sentence into comment lines of at most `width` characters.
 * @param {string} text
 * @param {{prefix?: string, indent?: string, width?: number}} [o]
 * @returns {string[]}
 */
export function commentLines(
  text,
  { prefix = "#", indent = "", width = MAX_LINE } = {},
) {
  const lead = `${indent}${prefix} `;
  const words = String(text ?? "")
    .split(/\s+/)
    .filter(Boolean);
  const out = [];
  let line = lead;
  words.forEach((w) => {
    const next = line === lead ? `${line}${w}` : `${line} ${w}`;
    if (next.length > width && line !== lead) {
      out.push(line);
      line = `${lead}${w}`;
    } else line = next;
  });
  if (line !== lead) out.push(line);
  return out;
}

/** `fic_live_…7Q2k`: a secret shown on screen (never the full value). */
export function maskSecret(value, { keep = 4 } = {}) {
  const s = String(value ?? "");
  if (!s) return "";
  const head = s.match(/^[a-z]+_(live_|test_|proto_)?/i)?.[0] ?? "";
  return `${head}…${s.slice(-keep)}`;
}

const ALPHA = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";

/** A deterministic string of `n` characters from a seed (FNV-1a + LCG). */
export function seededChars(seed, n) {
  let h = 2166136261;
  for (const ch of String(seed ?? "")) {
    h ^= ch.charCodeAt(0);
    h = Math.imul(h, 16777619) >>> 0;
  }
  let out = "";
  for (let i = 0; i < n; i += 1) {
    h = (Math.imul(h, 1103515245) + 12345) >>> 0;
    out += ALPHA[h % ALPHA.length];
  }
  return out;
}

/**
 * The draft connect token a Level 3 shows before implementation's token API
 * answers (seeded by the reserved run id).
 */
export const draftConnectToken = (runId) =>
  `fic_live_${seededChars(runId || "draft", 24)}`;

/** `support_agent`: an agent name safe for a CLI flag. */
export function agentSlug(name) {
  const s = String(name ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return s || "my_agent";
}

/** Python / JS string literal (double quotes, escaped). */
export const lit = (s) => JSON.stringify(String(s ?? ""));

/** Longest line of a text (tests and the UI's `≤ 88` check). */
export const longestLine = (text) =>
  Math.max(
    0,
    ...String(text ?? "")
      .split("\n")
      .map((l) => l.length),
  );
