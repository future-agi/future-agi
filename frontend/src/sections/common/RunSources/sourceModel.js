// The New experiment picker model (R4-SPEC D2–D5, D20, D21).
// Pure: no React, no clock, no random. The drawer (SourcePicker,
// SourceConfig) and the create handler read the same rules.
//
//   picker state  {level: 1|2|3, card, entryId, mode, form, view, lang}
//   URL keys      src · entry · mode · form · view   (in the drawer)
//   deep link     new_experiment=1&source=&entry=&agent=&mode=&form=
import { sandboxSentence } from "./snippets/runLocal";
import { CI_FILE, RUNNER_FILES, runnerCommand } from "./snippets/lines";
import { ciWorkflowPath } from "./snippets/ci";
import {
  catalogEntry,
  isOffered,
  modesFor,
  runSourceCard,
  stepCardShown,
} from "./catalog";
import { fmtCost, fmtCount } from "src/sections/common/format/fmt";

/** In-drawer URL keys of the picker (useViewUrlState, replace). */
export const PICKER_URL_KEYS = Object.freeze([
  "src",
  "entry",
  "mode",
  "form",
  "view",
  "lang",
]);

/** The step-1 card a run kind belongs to (`http` → `running`). */
export function cardOfKind(kind) {
  if (!kind) return null;
  if (["http", "traces", "upload", "running"].includes(kind)) return "running";
  return runSourceCard(kind) ? kind : null;
}

/** The run kind of a catalog entry (`langgraph` → `code`). */
export const kindOfEntry = (entryId) => catalogEntry(entryId)?.kind ?? null;

/** Cards whose Level 2 is a catalog (prompt / agent open R3's pickers). */
export const CATALOG_CARDS = Object.freeze(["running"]);

/**
 * Entries that open Level 3 (`SourceConfig`). The rest keep R3's cards
 * (HTTP, traces, upload) or open `ComingSoonDetail` (A2A, MCP).
 */
export function opensLevel3(entryId) {
  const e = catalogEntry(entryId);
  if (!e || !isOffered(entryId)) return false;
  return ["code", "harness"].includes(e.kind);
}

/** The mode a Level 3 opens on: the entry's default (D5). */
export function defaultModeOf(entryId) {
  const e = catalogEntry(entryId);
  if (!e) return null;
  const modes = modesFor(e.kind, e.id);
  const usable = (id) => modes.some((m) => m.id === id && !m.disabledReason);
  if (e.defaultMode && usable(e.defaultMode)) return e.defaultMode;
  return modes.find((m) => !m.disabledReason)?.id ?? null;
}

/**
 * Section 1's tab for a code run (D20). dataset integration FE-0: there is no live
 * connection, so every code run opens on its script form, whatever the URL
 * asked for.
 */
export function defaultFormOf() {
  return "upload";
}

/**
 * Picker state from URL params. The deep link (`new_experiment=1&source=`)
 * wins over the in-drawer keys (`src`).
 * @param {URLSearchParams|object} params
 */
export function pickerFromParams(params) {
  const get = (k) =>
    params instanceof URLSearchParams ? params.get(k) : params?.[k] ?? null;
  const deep = get("new_experiment") === "1" && get("source");
  const requestedCard = cardOfKind(deep ? get("source") : get("src"));
  // source/src aliases must obey the same gate as the visible step-1 cards.
  const card =
    requestedCard && stepCardShown(requestedCard) ? requestedCard : null;
  const entryRaw = get("entry");
  const entry = entryRaw && isOffered(entryRaw) ? entryRaw : null;
  const cardOfEntry = entry ? cardOfKind(kindOfEntry(entry)) : null;
  const finalCard = card ?? cardOfEntry;
  const level = entry && opensLevel3(entry) ? 3 : finalCard ? 2 : 1;
  const modeRaw = get("mode");
  const modes = entry ? modesFor(kindOfEntry(entry), entry) : [];
  const mode =
    modeRaw && modes.some((m) => m.id === modeRaw && !m.disabledReason)
      ? modeRaw
      : entry
        ? defaultModeOf(entry)
        : null;
  const formRaw = get("form");
  const view = get("view") === "code" ? "code" : "cli";
  return {
    level: CATALOG_CARDS.includes(finalCard) || level === 3 ? level : 1,
    card: finalCard,
    entryId: level === 3 ? entry : null,
    mode,
    form: defaultFormOf(),
    arrivedForm: formRaw === "upload" ? formRaw : null,
    view,
    lang: ["python", "typescript"].includes(get("lang")) ? get("lang") : null,
    agentId: get("agent"),
  };
}

/** URL params for a picker state (null = remove the key). */
export function pickerToParams(state) {
  const s = state ?? {};
  const atLeast = (n) => (s.level ?? 1) >= n;
  return {
    src: atLeast(2) ? s.card ?? null : null,
    entry: atLeast(3) ? s.entryId ?? null : null,
    mode: atLeast(3) ? s.mode ?? null : null,
    form: atLeast(3) && kindOfEntry(s.entryId) === "code" ? s.form : null,
    // G5: `view=code` opens the script pane (connect's In your code).
    view: atLeast(3) && s.view === "code" ? "code" : null,
    lang:
      atLeast(3) && kindOfEntry(s.entryId) === "code" ? s.lang ?? null : null,
  };
}

/** One level back (Backspace / alt+←): 3 → 2 → 1. */
export function pickerBack(state) {
  const s = state ?? {};
  if (s.level === 3)
    return { ...s, level: 2, entryId: null, mode: null, view: "cli" };
  if (s.level === 2) return { ...s, level: 1, card: null };
  return { ...s, level: 1 };
}

/** Pick a step-1 card. */
export function pickCard(state, cardId) {
  if (!stepCardShown(cardId)) return state ?? { level: 1 };
  return { ...(state ?? {}), level: 2, card: cardId, entryId: null };
}

/** Pick a catalog entry (Level 2 → 3). */
export function pickEntry(state, entryId) {
  if (!isOffered(entryId)) return state ?? { level: 1 };
  const e = catalogEntry(entryId);
  return {
    ...(state ?? {}),
    level: 3,
    card: cardOfKind(e?.kind) ?? state?.card ?? null,
    entryId,
    mode: defaultModeOf(entryId),
    form: defaultFormOf(),
    view: "cli",
  };
}

// --------------------------------------------------------------- names

/**
 * The agent name a draft uses before a handshake: the known agent's, else
 * the dataset's first word + `_agent` (`Support ops tasks` → `support_agent`).
 */
export function agentNameFor({ knownAgent, datasetName } = {}) {
  if (knownAgent?.name) return knownAgent.name;
  const first = String(datasetName ?? "")
    .toLowerCase()
    .match(/[a-z0-9]+/)?.[0];
  return first ? `${first}_agent` : "my_agent";
}

/** `support_agent · LangGraph`: agent + catalog name (P2). */
export function runNameFor({ agentName, entryId, savedName }) {
  const e = catalogEntry(entryId);
  if (!e) return agentName ?? "";
  // IT-R2 G1 P4 (rule 17): a run picked from a saved agent is named by it,
  // never by the connector alone.
  if (savedName) return `${savedName} · ${e.short ?? e.name}`;
  if (e.kind !== "code") return e.name;
  return `${agentName} · ${e.name}`;
}

// ------------------------------------------------------- connected now

/**
 * The agents of the machines the user may use, compatible with the
 * dataset kind and online. ConnectedMachine / ConnectedAgent shapes
 * (R4-SPEC §6). FE-0 (dataset integration): an agent counts only when the picker
 * offers its entry; there is no live connection command, so no code or
 * harness agent is usable.
 * @returns {{machine: object, agent: object}[]}
 */
export function usableAgents(machines, { entryId } = {}) {
  const kinds = ["code", "harness"];
  const out = [];
  (machines ?? []).forEach((m) => {
    if (m?.may_use === false || m?.status !== "online") return;
    (m.agents ?? []).forEach((a) => {
      if (!kinds.includes(a?.kind) || !isOffered(a?.entry_id)) return;
      if (entryId && a.entry_id !== entryId) return;
      out.push({ machine: m, agent: a });
    });
  });
  return out;
}

/** The Connected now strip's agent (first usable), or null (D2). */
export const connectedNowOf = (machines, opts) =>
  usableAgents(machines, opts)[0] ?? null;

/** The machine + agent for an agent id the user may use, or null. */
export function findAgent(machines, agentId) {
  for (const m of machines ?? []) {
    const a = (m?.agents ?? []).find((x) => x?.id === agentId);
    if (a) return { machine: m, agent: a };
  }
  return null;
}

// ------------------------------------------------------- what it uses

/**
 * Section 3 values from a ConnectedAgent (declared) and an observed run
 * config (seen). Each item `{name, declared, seen, side_effect?}`.
 */
export function usesOf({ agent, observed } = {}) {
  const merge = (declared = [], seen = []) => {
    const names = [...new Set([...declared, ...seen])];
    return names.map((name) => ({
      name,
      declared: declared.includes(name),
      seen: seen.includes(name),
    }));
  };
  const sideEffects = new Set(agent?.side_effect_tools ?? []);
  const seenTools = (observed?.tools ?? [])
    .filter((t) => t.called !== false)
    .map((t) => t.name);
  const tools = merge(agent?.tools ?? [], seenTools).map((t) => ({
    ...t,
    side_effect: sideEffects.has(t.name),
  }));
  const models = merge(
    agent?.models ?? [],
    (observed?.models ?? []).map((m) => m.id),
  );
  const mcp = merge(
    [],
    (observed?.mcp_servers ?? []).map((m) => m.name),
  );
  const prompts = (agent?.params_schema ?? [])
    .filter((p) => p.type === "prompt")
    .map((p) => ({ name: p.name, declared: true, seen: false }));
  return {
    models,
    tools,
    mcp,
    prompts,
    params: agent?.params_schema ?? [],
    sideEffectTools: [...sideEffects],
    code: agent?.git ?? null,
  };
}

/** `Uses gpt-4.1-mini · 7 tools · 4 parameters` (collapsed section 3). */
export function usesSummary(uses) {
  const u = uses ?? {};
  const models = u.models ?? [];
  const bits = [];
  if (models.length)
    bits.push(
      `Uses ${models[0].name}${models.length > 1 ? ` +${models.length - 1}` : ""}`,
    );
  if (u.tools?.length) bits.push(fmtCount(u.tools.length, "tool"));
  if (u.mcp?.length) bits.push(fmtCount(u.mcp.length, "MCP server"));
  if (u.params?.length) bits.push(fmtCount(u.params.length, "parameter"));
  return bits.length ? bits.join(" · ") : null;
}

/** `3 tries per task · 4 at a time` (collapsed section 4). */
export function limitsSummary({ tries, concurrency, rowNoun = "task" } = {}) {
  const t = Math.max(1, Number(tries) || 1);
  const c = Math.max(1, Number(concurrency) || 1);
  return `${fmtCount(t, "try", "tries")} per ${rowNoun} · ${c} at a time`;
}

/**
 * Section 4's limits after the source's own config (R4 integrate). No
 * source owns tries or run-at-a-time any more (the voice config is gone).
 * @param {object} limits  section 4's limits
 */
export function limitsWithSlot(limits) {
  return limits;
}

/** Param values from a schema's defaults. */
export function paramDefaults(schema) {
  return Object.fromEntries(
    (schema ?? []).map((p) => [p.name, p.default ?? null]),
  );
}

/** Names whose value differs from the code default. */
export function changedParams(schema, values) {
  return (schema ?? [])
    .filter(
      (p) =>
        values &&
        p.name in values &&
        JSON.stringify(values[p.name]) !== JSON.stringify(p.default),
    )
    .map((p) => p.name);
}

/** Clamp a numeric param into its declared range. */
export function clampParam(p, v) {
  let n = Number(v);
  if (!Number.isFinite(n)) n = Number(p?.default) || 0;
  if (Number.isFinite(p?.min)) n = Math.max(p.min, n);
  if (Number.isFinite(p?.max)) n = Math.min(p.max, n);
  return p?.type === "int" ? Math.round(n) : n;
}

// ------------------------------------------------------ limits / safety

/**
 * The picked rows as the labels a command prints (S3: `--rows T5,T12`;
 * the runner answers to a row's id, its label, its number and T<number>):
 * a row's own `label`, else `T<n>` in a task suite / `<n>` in a row
 * dataset (n = its 1-based position), else its id. Pure.
 * @param {string[]} rowIds  the Data step's picked row ids
 * @param {object[]} rows  the dataset's rows ({row_id, order?, label?})
 * @param {"task"|"row"} rowNoun
 */
export function scopeRowLabels(rowIds = [], rows = [], rowNoun = "row") {
  const at = new Map();
  (rows ?? []).forEach((r, i) => {
    const id = r?.row_id ?? r?.id;
    if (id != null) at.set(String(id), { r, i });
  });
  return (rowIds ?? []).map((id) => {
    const hit = at.get(String(id));
    if (!hit) return String(id);
    if (hit.r?.label) return String(hit.r.label);
    const n = Number.isFinite(hit.r?.order) ? hit.r.order + 1 : hit.i + 1;
    return rowNoun === "task" ? `T${n}` : String(n);
  });
}

/** Run at a time on a fresh card (R4-SPEC D21 `Run 4 at a time`). */
export const DEFAULT_AT_A_TIME = 4;

/**
 * Section 4 defaults. concurrencyMax = the agent's declared concurrency,
 * or null when it declares none (S5: no invented cap; LimitsRow allows up
 * to 50 then).
 */
export function limitsDefaults({ agent, tries } = {}) {
  const max = Number(agent?.concurrency) > 0 ? Number(agent.concurrency) : null;
  return {
    tries: Math.max(1, Number(tries) || 3),
    concurrency: max ? Math.min(DEFAULT_AT_A_TIME, max) : DEFAULT_AT_A_TIME,
    concurrencyMax: max,
    maxCostUsd: 5,
    timeoutS: 120,
  };
}

/**
 * Side effects (D21): on a dataset with a sandbox Environment, tools run
 * there; else, when the agent declares side-effect tools, section 4 warns
 * and Review counts the possible calls.
 * @returns {{state: "sandbox"|"warn"|null, line: string|null,
 *   reviewWarning: string|null}}
 */
export function sideEffectRule({
  kind,
  agentName,
  sideEffectTools = [],
  environment,
  safety = "real",
  tries = 1,
  rows = 0,
  rowNoun = "task",
} = {}) {
  if (!["code", "harness"].includes(kind))
    return { state: null, line: null, reviewWarning: null };
  if (environment) {
    const name =
      environment?.mcp?.name ?? environment?.name ?? environment?.image ?? "";
    // IT-R1 G1 P2: one sentence on every surface (G5's Evaluations note
    // matches it). Code runs: the script starts each task from its
    // start_state (snippets/runLocal.js); harness runs: the recipe's MCP.
    const sb = name || "this task suite's sandbox";
    return {
      state: "sandbox",
      line:
        kind === "code"
          ? sandboxSentence({ name: sb })
          : `Tools in this suite run against ${sb} through its MCP server; each task starts from its start_state and the end-state checks read the sandbox after the harness finishes.`,
      reviewWarning: null,
    };
  }
  const tools = sideEffectTools.filter(Boolean);
  if (!tools.length) return { state: null, line: null, reviewWarning: null };
  const t = Math.max(1, Number(tries) || 1);
  const n = Math.max(0, Number(rows) || 0);
  const warn =
    safety === "real" && t > 1 && n > 0
      ? `${tools[0]} may be called up to ${fmtCount(t * n)} times (${fmtCount(t, "try", "tries")} × ${fmtCount(n, rowNoun)}).`
      : null;
  return {
    state: "warn",
    line: `${agentName} can call ${tools.join(", ")}.`,
    reviewWarning: warn,
  };
}

// -------------------------------------------------------------- review

/**
 * `2 runs × 30 tasks × 3 tries = 180 tries · about $46 · about 12 min
 * (your machine runs 4 at a time)`.
 */
export function reviewLine({
  runs = 1,
  rows = 0,
  tries = 1,
  costUsd,
  minutes,
  timeText,
  localConcurrency,
  rowNoun = "task",
} = {}) {
  const r = Math.max(0, Number(runs) || 0);
  const n = Math.max(0, Number(rows) || 0);
  const t = Math.max(1, Number(tries) || 1);
  const head = `${fmtCount(r, "run")} × ${fmtCount(n, rowNoun)} × ${fmtCount(t, "try", "tries")} = ${fmtCount(r * n * t, "try", "tries")}`;
  const bits = [head];
  if (Number.isFinite(costUsd)) bits.push(`about ${fmtCost(costUsd)}`);
  if (timeText) bits.push(timeText);
  else if (Number.isFinite(minutes) && minutes > 0)
    bits.push(`about ${fmtCount(Math.max(1, Math.round(minutes)))} min`);
  const line = bits.join(" · ");
  return localConcurrency
    ? `${line} (your machine runs ${localConcurrency} at a time)`
    : line;
}

// ---------------------------------------------------------- run config

const TRACING = {
  auto: "auto",
  otel_preview: "otel",
  otel: "otel",
  manual: "manual",
};

/**
 * The R4 RunConfig of a Level 3 draft (R4-SPEC §6). Additive on R2's code
 * fields; the create handler stores it as `run.config`.
 * @param {object} d  the draft (see SourceConfig)
 */
export function runConfigFromDraft(d = {}) {
  const e = catalogEntry(d.entryId);
  const kind = e?.kind ?? "code";
  const uses = d.uses ?? {};
  const local = d.mode === "local";
  const live = kind === "code" && d.form === "connect";
  const machine = d.machine ?? null;
  const agent = d.agent ?? null;
  const config = {
    kind,
    framework:
      kind === "code"
        ? {
            id: e?.id ?? null,
            name: e?.name ?? null,
            version: agent?.framework_version ?? null,
            language: (e?.languages ?? ["python"])[0],
            tracing: TRACING[e?.badge] ?? "manual",
            graph_source: live ? "connect_sdk" : e?.custom ? "manual" : null,
          }
        : undefined,
    execution: {
      mode: d.mode ?? null,
      label:
        local && live && machine ? machine.name : local ? "This machine" : null,
      machine_id: live ? machine?.id ?? null : null,
      agent_id: live ? agent?.id ?? null : null,
      owner: live && machine?.owner ? machine.owner : null,
      concurrency: Number(d.limits?.concurrency) || 1,
    },
    // N6: undeclared lists are left out (never `[]`; the engine and the
    // identity read a missing list as "none declared").
    models: nonEmpty(modelsOf(uses, d)),
    tools: nonEmpty(toolsOf(uses, d)),
    mcp_servers: nonEmpty(mcpServersOf(d)),
    params: {
      schema: uses.params ?? [],
      values: d.params ?? paramDefaults(uses.params),
      set_name: d.paramSetName ?? "default",
    },
    prompts: (uses.prompts ?? []).map((p) => ({
      name: p.name,
      source: "declared",
    })),
    secrets: {
      model_keys: d.secretsSource ?? (local ? "machine" : "workspace"),
    },
    safety: {
      side_effects: d.safety === "stubbed" ? "stubs" : d.safety ?? "real",
    },
    limits: {
      tries: Number(d.limits?.tries) || 1,
      concurrency: Number(d.limits?.concurrency) || 1,
      max_cost_usd: Number(d.limits?.maxCostUsd) || null,
      timeout_s: Number(d.limits?.timeoutS) || null,
      // I3: a clamp is always named (sandbox.js cappedLimits).
      clamp_reason: d.limits?.clampReason ?? null,
    },
  };
  if (kind === "code")
    config.code = {
      mode: live ? "connect_preview" : "trace_upload",
      entry: e?.agent?.spec ?? null,
      commit: agent?.git?.commit ?? null,
      dirty: { files: agent?.git?.files ?? [] },
      sdk: null,
      diff_snapshot: d.diffSnapshot ?? null,
    };
  // The Rb slot's partial config (recipe) lands on the run config.
  const slot = d.slot ?? {};
  // N3: a harness connected on this machine (the slot's
  // live connection) binds the run to it: Future AGI starts each try there
  // (`Running on alice@mbp-14`), nothing waits for a terminal.
  const bound = boundExecutionOf(kind, d, slot.execution);
  if (bound) config.execution = { ...config.execution, ...bound };
  if (kind === "harness")
    config.harness = { id: e?.id, name: e?.name, ...(slot.harness ?? {}) };
  // C3: where it runs (the slot editors' values reach the run).
  // L3 G6 (fix 11): a harness's sandbox is the one its recipe edits
  // (slot.harness.sandbox), so the identity and the engine read one sandbox.
  if (d.mode === "sandbox")
    config.sandbox = sandboxConfigOf(
      e?.kind === "harness"
        ? slot.harness?.sandbox ?? slot.sandbox
        : slot.sandbox,
      e,
    );
  // L3 G6 (S3): a run's CI job is its own file, fi-experiment-<label>.yml,
  // the path its YAML header, the panel and the identity all name.
  const ciPath = d.runLabel
    ? ciWorkflowPath(d.runLabel)
    : `.github/workflows/${CI_FILE}`;
  if (d.mode === "ci")
    config.ci = {
      provider: slot.ci?.provider ?? "github",
      file: ciPath.split("/").pop(),
      trigger: slot.ci?.trigger ?? "pull_request",
      threshold: slot.ci?.threshold ?? null,
    };
  if (d.mode === "remote" && slot.endpoint) config.endpoint = slot.endpoint;
  if (d.mode === "ci") config.execution.label = ciPath;
  if (d.mode === "sandbox")
    config.execution.label = sandboxSummary(config.sandbox);
  return config;
}

/**
 * The execution a connected harness binds (N3), or null.
 * `slot.execution` = `{machine_id, agent_id, label, owner?}` from
 * HarnessRecipe once its live connection is up.
 */
export function boundExecutionOf(kind, d = {}, exec = null) {
  if (kind !== "harness") return null;
  if (d.mode !== "local" || !exec?.machine_id) return null;
  return {
    mode: "local",
    machine_id: exec.machine_id,
    agent_id: exec.agent_id ?? null,
    label: exec.label ?? exec.machine_name ?? null,
    owner: exec.owner ?? null,
  };
}

// ------------------------------------------------------------ C3 parts
const nonEmpty = (list) =>
  Array.isArray(list) && list.length ? list : undefined;
const WRITE_VERB =
  /^(issue|update|cancel|apply|send|create|schedule|escalate|refund|delete)_/;

/** Models a run uses: observed + declared (+ the harness model). */
function modelsOf(uses, d) {
  const out = (uses.models ?? []).map((m) => ({
    id: m.name,
    role: "main",
    calls: null,
    source:
      m.declared && m.seen ? "both" : m.declared ? "declared" : "observed",
  }));
  const add = (id, role, source) => {
    if (id && !out.some((m) => m.id === id))
      out.push({ id, role, calls: null, source });
  };
  add(d.declared?.model, "main", "declared");
  add(d.slot?.harness?.model, "main", "recipe");
  add(d.slot?.harness?.subcall_model, "helper", "recipe");
  return out;
}

/** Tools: observed + declared, with the write tools marked. */
function toolsOf(uses, d) {
  const out = (uses.tools ?? []).map((t) => ({
    name: t.name,
    declared: t.declared,
    called: t.seen,
    side_effect: Boolean(t.side_effect),
  }));
  (d.declared?.tools ?? []).forEach((name) => {
    if (out.some((t) => t.name === name)) return;
    out.push({
      name,
      declared: true,
      called: false,
      side_effect: WRITE_VERB.test(name),
    });
  });
  return out;
}

/** Declared MCP servers (never `[]` when some are declared). */
function mcpServersOf(d) {
  const sb = d.sandbox ?? null;
  return (d.declared?.mcp ?? []).map((name) =>
    sb?.name === name
      ? {
          name,
          transport: "http",
          url: `$${sb.envVar}/mcp`,
          tools: [...(sb.tools ?? [])],
        }
      : { name, transport: "stdio", command: name, tools: [] },
  );
}

/** C3 sandbox shape from SandboxConfig's value (and its defaults). */
export function sandboxConfigOf(v = {}, entry = null) {
  const ts =
    (entry?.languages ?? []).includes("typescript") &&
    !(entry?.languages ?? []).includes("python");
  const source = v?.source ?? "repo";
  return {
    source,
    image: v?.image ?? null,
    repo: v?.repo ?? null,
    branch: v?.branch ?? null,
    zip: v?.zip ?? null,
    entry: v?.entry ?? entry?.agent?.spec ?? null,
    setup: v?.setup ?? null,
    runtime: v?.runtime ?? (ts ? "node22" : "python3.12"),
    network: v?.network ?? "off",
    allowlist: v?.allowlist ?? [],
    resources: {
      cpu: Number(v?.resources?.cpu ?? v?.cpu) || 2,
      memory_gb: Number(v?.resources?.memory_gb ?? v?.memory_gb) || 4,
      timeout_s: Number(v?.resources?.timeout_s ?? v?.timeout_s) || 900,
    },
  };
}

const RUNTIME_LABEL = { "python3.12": "Python 3.12", node22: "Node 22" };

/**
 * What a sandbox form still lacks or contradicts (L3 G4 fix 9, pure):
 * `block` stops Add (a zip source with no file chosen), `warn` names a
 * runtime that cannot run the entry (`Node 22 runs .js/.ts entries;
 * agent.py needs a Python runtime`).
 * @returns {{block: string|null, warn: string|null}}
 */
export function sandboxIssues(c) {
  if (!c) return { block: null, warn: null };
  const block =
    c.source === "zip" && !c.zip?.name
      ? "Choose a .zip of your project folder · Sandbox"
      : null;
  const file = String(c.entry ?? "").split(":")[0];
  const py = /\.py$/i.test(file);
  const js = /\.(m?[jt]s|tsx?)$/i.test(file);
  const node = String(c.runtime ?? "").startsWith("node");
  const warn =
    node && py
      ? `${RUNTIME_LABEL[c.runtime] ?? c.runtime} runs .js/.ts entries; ${file} needs a Python runtime`
      : !node && js
        ? `${RUNTIME_LABEL[c.runtime] ?? c.runtime} runs .py entries; ${file} needs a Node runtime`
        : null;
  return { block, warn };
}

/**
 * `acme/support-agents@main · agent.py:graph · Python 3.12 · 4 vCPU / 8 GB ·
 * network allowlist (2)` (pure).
 */
export function sandboxSummary(c) {
  if (!c) return null;
  const where =
    c.source === "image"
      ? c.image
      : c.source === "zip"
        ? c.zip?.name ?? "zip: no file yet"
        : `${c.repo}@${c.branch}`;
  const net =
    c.network === "allowlist"
      ? `network allowlist (${(c.allowlist ?? []).length})`
      : `network ${c.network}`;
  return [
    where,
    c.entry,
    RUNTIME_LABEL[c.runtime] ?? c.runtime,
    `${c.resources.cpu} vCPU / ${c.resources.memory_gb} GB`,
    net,
  ]
    .filter(Boolean)
    .join(" · ");
}

/**
 * How experiment.yml starts, by where the run runs (N6): local = the runner
 * command; CI = the
 * workflow file; sandbox = Future AGI starts it in the sandbox; remote =
 * Future AGI calls the endpoint. Pure.
 * @returns {string[]} the comment lines (without `# `)
 */
export function yamlStartLines(config = {}, { lang = "python" } = {}) {
  const exec = config.execution ?? {};
  const mode = exec.mode ?? "local";
  const runner =
    config.kind === "harness"
      ? "harness"
      : lang === "typescript"
        ? "typescript"
        : "python";
  const head = runnerCommand({ lang: runner, config: "experiment.yml" });
  if (mode === "ci")
    return [
      `Runs in ${exec.label ?? `.github/workflows/${config.ci?.file ?? CI_FILE}`}`,
    ];
  // The sandbox runs the same runner with this file; you start nothing.
  if (mode === "sandbox")
    return ["Future AGI starts this in the sandbox; inside it runs", head];
  if (mode === "remote")
    return ["Future AGI calls your endpoint for each row (nothing to run)"];
  return [head, `${head} --dry-run`];
}

/** The allowlist's hosts (strings or `{host}` rows), blanks dropped. */
const hostsList = (v) =>
  (Array.isArray(v) ? v : String(v ?? "").split(/[\s,]+/))
    .map((h) => String(h?.host ?? h ?? "").trim())
    .filter(Boolean);

/**
 * An endpoint's headers for experiment.yml (L3 G4 C7, pure): `{name:
 * {secret: NAME}}` for a header from a workspace secret, `{name: value}`
 * for a plain one; unnamed rows dropped.
 */
export function yamlHeaders(headers) {
  const out = {};
  (headers ?? []).forEach((h) => {
    const name = String(h?.name ?? "").trim();
    if (!name) return;
    out[name] = h.secret ? { secret: String(h.secret) } : String(h.value ?? "");
  });
  return out;
}

/**
 * experiment.yml for `Run as code` (S3): what `--config experiment.yml`
 * reads (agent, run, rows, trials, limits, params, safety, evals). Pure.
 * The start line and the agent line follow the mode (N6): no `agent:` when
 * there is no agent file to run (sandbox, remote, harness).
 * @param {object} config  runConfigFromDraft(draft)
 * @param {{runId, runName, datasetName, rows, split, evals, lang}} o
 */
export function experimentYaml(config = {}, o = {}) {
  const q = (v) =>
    v == null
      ? "null"
      : typeof v === "number" || typeof v === "boolean"
        ? String(v)
        : JSON.stringify(String(v));
  const lim = config.limits ?? {};
  const lang = o.lang === "typescript" ? "typescript" : "python";
  const mode = config.execution?.mode ?? "local";
  const rows = Array.isArray(o.rows) && o.rows.length ? o.rows : null;
  const L = [
    `# experiment.yml · ${q(o.runName ?? "this run")} on ${q(o.datasetName ?? "this dataset")}`,
    ...yamlStartLines(config, { lang }).map((l) => `#   ${l}`),
  ];
  const agentFile =
    mode === "sandbox" || mode === "remote" || config.kind === "harness"
      ? null
      : config.code?.entry ?? null;
  if (agentFile) L.push(`agent: ${q(agentFile)}`);
  if (config.kind === "harness" && config.harness?.id)
    L.push(`harness: ${q(config.harness.id)}`);
  L.push(`run_id: ${q(o.runId ?? null)}`);
  L.push(`mode: ${q(mode)}`);
  if (mode === "sandbox" && config.sandbox) {
    const sb = config.sandbox;
    L.push("sandbox:");
    L.push(`  source: ${q(sb.source)}`);
    if (sb.source === "image") L.push(`  image: ${q(sb.image)}`);
    else if (sb.source === "zip") L.push(`  zip: ${q(sb.zip?.name ?? null)}`);
    else {
      L.push(`  repo: ${q(sb.repo)}`);
      L.push(`  branch: ${q(sb.branch)}`);
    }
    L.push(`  entry: ${q(sb.entry)}`);
    // L3 G4 C7: the whole sandbox (setup, the allowlist's hosts, the
    // per-try time limit); lists and maps are JSON flow values, which the
    // runner's read_config parses (one nested level, stdlib only)
    L.push(`  setup: ${q(sb.setup || null)}`);
    L.push(`  runtime: ${q(sb.runtime)}`);
    L.push(`  network: ${q(sb.network)}`);
    if (sb.network === "allowlist")
      L.push(`  allowlist: ${JSON.stringify(hostsList(sb.allowlist))}`);
    L.push(`  cpu: ${q(sb.resources?.cpu)}`);
    L.push(`  memory_gb: ${q(sb.resources?.memory_gb)}`);
    L.push(`  timeout_s: ${q(sb.resources?.timeout_s ?? null)}`);
  }
  if (mode === "remote") {
    const ep = config.endpoint ?? {};
    L.push("endpoint:");
    L.push(`  url: ${q(ep.url || null)}`);
    L.push(`  method: ${q(ep.method ?? "POST")}`);
    // L3 G4 C7: headers by name; a secret header names its workspace
    // secret (the value never leaves Future AGI), a plain one its value
    L.push(`  headers: ${JSON.stringify(yamlHeaders(ep.headers))}`);
    L.push(`  body: ${q(ep.body_template ?? null)}`);
    L.push(`  output_path: ${q(ep.output_path ?? null)}`);
    L.push(`  streaming: ${q(Boolean(ep.streaming))}`);
    L.push(`  audio: ${q(ep.audio ?? "text")}`);
  }
  if (rows) {
    L.push("rows:");
    rows.forEach((id) => L.push(`  - ${q(id)}`));
  } else L.push(`rows: ${q(o.rowsMode ?? "all")}`);
  L.push(`split: ${q(o.split ?? null)}`);
  L.push(`trials: ${q(lim.tries ?? 1)}`);
  L.push("limits:");
  L.push(`  concurrency: ${q(lim.concurrency ?? 1)}`);
  L.push(`  max_cost_usd: ${q(lim.max_cost_usd ?? null)}`);
  L.push(`  timeout_s: ${q(lim.timeout_s ?? null)}`);
  const values = config.params?.values ?? {};
  const keys = Object.keys(values).filter((k) => values[k] != null);
  if (keys.length) {
    L.push("params:");
    keys.forEach((k) => L.push(`  ${k}: ${q(values[k])}`));
  } else L.push("params: {}");
  L.push("safety:");
  L.push(`  side_effects: ${q(config.safety?.side_effects ?? "real")}`);
  const evals = o.evals ?? [];
  if (evals.length) {
    L.push("evals:");
    evals.forEach((n) => L.push(`  - ${q(n)}`));
  } else L.push("evals: []");
  if (config.models?.length) {
    L.push("models:");
    config.models.forEach((m) => L.push(`  - ${q(m.id)}`));
  }
  if (config.tools?.length) {
    L.push("tools:");
    config.tools.forEach((t) => L.push(`  - ${q(t.name)}`));
  }
  return `${L.join("\n")}\n`;
}

export { RUNNER_FILES };

/** Can a draft be added? `{ok, reason}` (no dead buttons). */
export function draftReadiness(d = {}) {
  const e = catalogEntry(d.entryId);
  if (!e) return { ok: false, reason: "Pick a framework first" };
  if (!String(d.runName ?? "").trim())
    return { ok: false, reason: "Name this run" };
  if (
    e.kind === "code" &&
    d.mode === "local" &&
    d.form === "connect" &&
    !d.agent
  )
    return { ok: false, reason: "Connect your agent first · section 2" };
  if (d.notAllowed) return { ok: false, reason: d.notAllowed };
  // L3 G4 fix 9: a sandbox from a zip needs the zip before Add
  if (d.mode === "sandbox") {
    const sb = d.slot?.harness?.sandbox ?? d.slot?.sandbox ?? null;
    const { block } = sandboxIssues(sb);
    if (block) return { ok: false, reason: block };
  }
  return { ok: true, reason: null };
}
