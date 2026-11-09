// What changed between two runs (R2-SPEC §1.1, R4-SPEC D7). Pure: diffs two
// `run.config` snapshots (R1 `include=config`) into ordered chips, one per
// change. Used by ConfigDiffStrip (compare headers, the iterate banner), the
// RunProvenance card (`compareTo`) and the R5 `root_causes` mock handler
// (`linked_change`).
//
// Frozen API (R2 manifest row 43): AXES, configDiff(a, b, {all}),
// configDiffSentence(chips), kindDiffLabel(config). R4 (lane X, additive):
// every chip also carries `text`, the short copy the strip shows
// (`gpt-4o-mini → gpt-4.1-mini`, `+search_kb_v2`, `MCP +postgres`); `label`
// keeps the R2 wording (`model gpt-4o-mini → gpt-4.1-mini`) for the R2 / R3
// readers. Runs of different kinds keep their one `kind` chip FIRST and now
// list the other axes after it (`Agent graph → Your code (LangGraph) ·
// gpt-4o-mini → gpt-4.1-mini · +search_kb_v2`).
import { diffArrays } from "diff";
import { fmtCost } from "src/sections/common/format/fmt";
import { provenanceOf } from "./provenanceModel";
import { mcpEnvSecrets } from "src/sections/common/RunSources/endpoint/requestModel";

/** Every axis, in chip order (R4-SPEC D7; `model` = the main model, `models` = the others). */
export const AXES = Object.freeze([
  "kind",
  "framework",
  "prompt",
  "model",
  "models",
  "params",
  "tools",
  "mcp",
  "permissions",
  "capture",
  "safety",
  "agent",
  "code",
  "execution",
  "voice",
  "endpoint",
  "harness",
  "traces",
  "dataset",
  "evals",
  "trials",
]);

// R4 (F3): `Your code`, `Endpoint`, `Voice agent`; options-truth L1 G5 kinds
// `A2A agent` and `MCP tool` (never the Prompt fallback).
const KIND_NAMES = {
  prompt: "Prompt",
  agent: "Agent graph",
  code: "Your code",
  traces: "Linked traces",
  harness: "Agent harness",
  voice: "Voice agent",
  http: "Endpoint",
  a2a: "A2A agent",
  mcp_tool: "MCP tool",
  upload: "Upload",
};

const ARROW = " → ";
const MINUS = "−"; // U+2212, as in the spec labels

const isRunLike = (x) =>
  x && typeof x === "object" && ("run_id" in x || "config" in x);
/** A config from a run (its `.config`) or a config passed directly. */
const configOf = (x) => (isRunLike(x) ? x.config ?? null : x ?? null);

const same = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
const text = (v) =>
  v === null || v === undefined || v === "" ? "none" : String(v);

/** `Prompt` / `Prompt + tools` / `Agent graph` / … for one config. */
export function kindDiffLabel(config) {
  const kind = config?.kind ?? "prompt";
  if (kind === "prompt" && (config?.tools ?? []).length)
    return "Prompt + tools";
  return KIND_NAMES[kind] ?? KIND_NAMES.prompt;
}

const words = (s) =>
  String(s ?? "")
    .split(/\s+/)
    .filter(Boolean);
const promptText = (p) =>
  (p?.messages ?? [])
    .map((m) => (typeof m?.content === "string" ? m.content : ""))
    .join("\n");

/** `{plus, minus}` whole-word counts between two texts. */
export function wordStats(from, to) {
  let plus = 0;
  let minus = 0;
  diffArrays(words(from), words(to)).forEach((part) => {
    if (part.added) plus += part.count ?? part.value.length;
    else if (part.removed) minus += part.count ?? part.value.length;
  });
  return { plus, minus };
}

const statText = ({ plus, minus }) => `+${plus} ${MINUS}${minus}`;

const money = (v) => {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "none";
  return fmtCost(v);
};

/**
 * One chip: `label` (R2 wording, kept for R2 / R3 readers), `text` (the R4
 * short copy the strip shows; defaults to `label`), `detail`.
 */
const chip = (axis, label, detail = {}, text = null) => ({
  axis,
  label,
  text: text ?? label,
  detail,
});
/** `label` minus its leading axis word (`model a → b` → `a → b`). */
const dropWord = (label, word) =>
  String(label).startsWith(`${word} `)
    ? String(label).slice(word.length + 1)
    : String(label);

// ---------------------------------------------------------------- axes

function promptChips(a, b) {
  const pa = a?.prompt ?? null;
  const pb = b?.prompt ?? null;
  if (!pa && !pb) return [];
  const sameText =
    (pa?.text_hash && pb?.text_hash && pa.text_hash === pb.text_hash) ||
    promptText(pa) === promptText(pb);
  if (pa?.id === pb?.id && pa?.version === pb?.version && sameText) return [];
  const sameName = (pa?.name ?? null) === (pb?.name ?? null);
  const side = (p) =>
    p ? (sameName ? text(p.version) : `${p.name} ${p.version}`) : "none";
  const stats = wordStats(promptText(pa), promptText(pb));
  const changedWords = stats.plus + stats.minus > 0;
  let label = `prompt ${side(pa)}${ARROW}${side(pb)}`;
  if (pa && pb && pa.version === pb.version && sameName) label += " (edited)";
  if (pa && pb && changedWords) label += ` (${statText(stats)} words)`;
  return [
    chip("prompt", label, {
      from: side(pa),
      to: side(pb),
      words: stats,
      messages: { from: pa?.messages ?? [], to: pb?.messages ?? [] },
    }),
  ];
}

/** The provenance of a bare config (memoised per config object). */
const provCache = new WeakMap();
function provOf(c) {
  if (!c || typeof c !== "object") return null;
  if (!provCache.has(c)) provCache.set(c, provenanceOf({ config: c }));
  return provCache.get(c);
}

/** The run's main model: R2 `model.id` / harness model, else R4 `models` main, voice LLM, agent's first node. */
const modelOf = (c) =>
  c?.model?.id ??
  c?.harness?.model ??
  c?.voice?.llm?.model ??
  provOf(c)?.models?.find((m) => m.role === "main")?.id ??
  provOf(c)?.models?.find((m) => !SIDE_ROLES.has(m.role))?.id ??
  null;
const SIDE_ROLES = new Set(["stt", "tts", "caller"]);

function modelChips(a, b) {
  const ma = modelOf(a);
  const mb = modelOf(b);
  if (ma === mb) return [];
  return [
    chip(
      "model",
      `model ${text(ma)}${ARROW}${text(mb)}`,
      { from: text(ma), to: text(mb) },
      `${text(ma)}${ARROW}${text(mb)}`,
    ),
  ];
}

const roleText = (role) =>
  String(role ?? "").startsWith("node:")
    ? String(role).slice(5)
    : role && role !== "main"
      ? role
      : null;

/**
 * Models other than the main one (`+claude-haiku-4-5 (sub-calls)`): R4
 * `models[]` minus STT / TTS / caller (the voice axis) and per-node models
 * of agent graphs (the agent axis).
 */
function modelsChips(a, b) {
  const others = (c) =>
    (provOf(c)?.models ?? []).filter(
      (m) =>
        m.role !== "main" &&
        !SIDE_ROLES.has(m.role) &&
        !String(m.role ?? "").startsWith("node:") &&
        m.id,
    );
  const oa = others(a);
  const ob = others(b);
  if (!oa.length && !ob.length) return [];
  const mains = new Set([modelOf(a), modelOf(b)]);
  const idsA = new Set(oa.map((m) => m.id));
  const idsB = new Set(ob.map((m) => m.id));
  const side = (m) => (roleText(m.role) ? ` (${roleText(m.role)})` : "");
  return [
    ...ob
      .filter((m) => !idsA.has(m.id) && !mains.has(m.id))
      .map((m) =>
        chip(
          "models",
          `models +${m.id}${side(m)}`,
          { added: m.id, role: m.role },
          `+${m.id}${side(m)}`,
        ),
      ),
    ...oa
      .filter((m) => !idsB.has(m.id) && !mains.has(m.id))
      .map((m) =>
        chip(
          "models",
          `models ${MINUS}${m.id}${side(m)}`,
          { removed: m.id, role: m.role },
          `${MINUS}${m.id}${side(m)}`,
        ),
      ),
  ];
}

const PARAM_NAMES = {
  max_tokens: "max tokens",
  top_p: "top p",
  frequency_penalty: "frequency penalty",
  presence_penalty: "presence penalty",
  response_format: "response format",
};

/** `0.2 → 0.0`: a whole number next to a fraction keeps one decimal. */
const fracDigits = (v) =>
  typeof v === "number" && !Number.isInteger(v)
    ? String(v).split(".")[1]?.length ?? 0
    : 0;
function paramPair(from, to, unset = "none") {
  const show = (v, other) => {
    if (v === null || v === undefined || v === "") return unset;
    if (typeof v === "object") return JSON.stringify(v);
    if (
      typeof v === "number" &&
      Number.isInteger(v) &&
      typeof other === "number" &&
      fracDigits(other) > 0
    )
      return `${v}.0`;
    return String(v);
  };
  return [show(from, to), show(to, from)];
}

const lineCount = (s) => String(s ?? "").split("\n").length;
/** `system_prompt +3 lines` / `+3 −1 lines` from two prompt texts. */
function linesText(from, to) {
  if (from == null || from === "") return "added";
  if (to == null || to === "") return "removed";
  let plus = 0;
  let minus = 0;
  diffArrays(String(from).split("\n"), String(to).split("\n")).forEach(
    (part) => {
      if (part.added) plus += part.count ?? part.value.length;
      else if (part.removed) minus += part.count ?? part.value.length;
    },
  );
  if (!plus && !minus) return "edited";
  const word = plus + minus === 1 ? "line" : "lines";
  if (plus && minus) return `+${plus} ${MINUS}${minus} ${word}`;
  return plus ? `+${plus} ${word}` : `${MINUS}${minus} ${word}`;
}

// Declared params another axis already shows (main model, voice fields).
const COVERED_PARAMS = new Set([
  "model",
  "llm.model",
  "stt.model",
  "tts.voice",
  "turn_detection",
]);

function paramChips(a, b) {
  const pa = a?.model?.params ?? {};
  const pb = b?.model?.params ?? {};
  const keys = [...new Set([...Object.keys(pa), ...Object.keys(pb)])];
  const r2 = keys
    .filter((k) => !same(pa[k], pb[k]))
    .map((k) => {
      // options-truth L5 G2: a model param the run never set ran at the
      // model's default (`temperature default → 0.9`).
      const [from, to] = paramPair(pa[k], pb[k], "default");
      return chip(
        "params",
        `${PARAM_NAMES[k] ?? k} ${from}${ARROW}${to}`,
        { param: k, from, to },
        `${k} ${from}${ARROW}${to}`,
      );
    });
  // R4 declared parameters (`params.values` + `params.schema`).
  const va = a?.params?.values ?? {};
  const vb = b?.params?.values ?? {};
  const schema = [...(a?.params?.schema ?? []), ...(b?.params?.schema ?? [])];
  const typeOf = (k) => schema.find((p) => p.name === k)?.type ?? null;
  const defaultOf = (k) =>
    (b?.params?.schema ?? a?.params?.schema ?? []).find((p) => p.name === k)
      ?.default;
  const r4 = [...new Set([...Object.keys(va), ...Object.keys(vb)])]
    .filter((k) => !COVERED_PARAMS.has(k) && !same(va[k], vb[k]))
    .map((k) => {
      if (typeOf(k) === "prompt") {
        const what = linesText(va[k], vb[k]);
        return chip(
          "params",
          `${k} ${what}`,
          {
            param: k,
            from: va[k] ?? null,
            to: vb[k] ?? null,
            default: defaultOf(k),
            lines: { from: lineCount(va[k]), to: lineCount(vb[k]) },
          },
          `${k} ${what}`,
        );
      }
      const [from, to] = paramPair(va[k], vb[k]);
      return chip(
        "params",
        `${k} ${from}${ARROW}${to}`,
        { param: k, from, to, default: defaultOf(k) },
        `${k} ${from}${ARROW}${to}`,
      );
    });
  // L3 G9: a declared param a rerun changed also lands in `model.params`
  // (where the engine reads it) with no value before; its `params.values`
  // chip already says `0.2 → 0.9`, so the `none → 0.9` twin goes.
  const declared = new Set(r4.map((c) => c.detail.param));
  return [
    ...r2.filter(
      (c) => !(declared.has(c.detail.param) && c.detail.from === "default"),
    ),
    ...r4,
  ];
}

/** Base name of a versioned tool (`search_kb_v2` → `search_kb`). */
const toolBase = (name) => String(name).replace(/_v\d+$/, "");

function toolChips(a, b) {
  const ta = a?.tools ?? [];
  const tb = b?.tools ?? [];
  const byA = new Map(ta.map((t) => [t.name, t]));
  const byB = new Map(tb.map((t) => [t.name, t]));
  // A new version of something the other run already had reads first
  // (`+search_kb_v2` next to an agent graph's `search_kb` node).
  const knownA = new Set([
    ...ta.map((t) => toolBase(t.name)),
    ...(a?.agent?.nodes ?? []).map((n) => toolBase(n.id)),
  ]);
  const added = tb
    .filter((t) => !byA.has(t.name))
    .map((t) => t.name)
    .sort(
      (x, y) =>
        Number(knownA.has(toolBase(y))) - Number(knownA.has(toolBase(x))),
    );
  const removed = ta.filter((t) => !byB.has(t.name)).map((t) => t.name);
  const out = [
    ...added.map((n) =>
      chip(
        "tools",
        `tools +${n}`,
        {
          added: [n],
          removed: [],
          from: ta.map((t) => t.name),
          to: tb.map((t) => t.name),
        },
        `+${n}`,
      ),
    ),
    ...removed.map((n) =>
      chip(
        "tools",
        `tools ${MINUS}${n}`,
        {
          added: [],
          removed: [n],
          from: ta.map((t) => t.name),
          to: tb.map((t) => t.name),
        },
        `${MINUS}${n}`,
      ),
    ),
  ];
  tb.forEach((t) => {
    const prev = byA.get(t.name);
    if (!prev) return;
    if ((prev.mode ?? null) !== (t.mode ?? null))
      out.push(
        chip("tools", `${t.name} ${text(prev.mode)}${ARROW}${text(t.mode)}`, {
          tool: t.name,
          field: "mode",
          from: text(prev.mode),
          to: text(t.mode),
        }),
      );
    if (prev.schema_hash && t.schema_hash && prev.schema_hash !== t.schema_hash)
      out.push(
        chip("tools", `${t.name} schema changed`, {
          tool: t.name,
          field: "schema",
          from: prev.schema_hash,
          to: t.schema_hash,
        }),
      );
  });
  if ((a?.max_tool_turns ?? null) !== (b?.max_tool_turns ?? null))
    out.push(
      chip(
        "tools",
        `max tool turns ${text(a?.max_tool_turns)}${ARROW}${text(b?.max_tool_turns)}`,
        {
          field: "max_tool_turns",
          from: text(a?.max_tool_turns),
          to: text(b?.max_tool_turns),
        },
      ),
    );
  return out;
}

// ------------------------------------------------------------- R4 axes

/** `LangGraph → CrewAI` / `LangGraph 0.4.8 → 0.5` (code runs). */
function frameworkChips(a, b) {
  const fa = a?.framework ?? null;
  const fb = b?.framework ?? null;
  if (!fa && !fb) return [];
  const nameA = fa?.name ?? null;
  const nameB = fb?.name ?? null;
  if (nameA !== nameB) {
    const t = `${text(nameA)}${ARROW}${text(nameB)}`;
    return [chip("framework", `framework ${t}`, { from: nameA, to: nameB }, t)];
  }
  if ((fa?.version ?? null) !== (fb?.version ?? null)) {
    const t = `${nameB} ${text(fa?.version)}${ARROW}${text(fb?.version)}`;
    return [
      chip(
        "framework",
        `framework ${t}`,
        {
          field: "version",
          from: fa?.version ?? null,
          to: fb?.version ?? null,
        },
        t,
      ),
    ];
  }
  return [];
}

// options-truth L7b G4 (knob:mcp:env-secret): an MCP server tool run's env
// secrets (`ACME_SANDBOX_TOKEN ← FI_PROJECT_KEY`), sorted, or null.
const mcpEnvText = (c) => {
  const env = mcpEnvSecrets(c?.mcp_tool?.server ?? {})
    .filter((x) => x.name && x.secret)
    .map((x) => `${x.name} ← ${x.secret}`)
    .sort();
  return env.length ? env.join(", ") : null;
};

/** `MCP env none → ACME_SANDBOX_TOKEN ← FI_PROJECT_KEY` (MCP tool runs). */
function mcpEnvChips(a, b) {
  if (a?.kind !== "mcp_tool" && b?.kind !== "mcp_tool") return [];
  const ea = mcpEnvText(a);
  const eb = mcpEnvText(b);
  if (ea === eb) return [];
  const t = `MCP env ${ea ?? "none"}${ARROW}${eb ?? "none"}`;
  return [chip("mcp", t, { env: true, from: ea, to: eb }, t)];
}

/** `output json → string` (the run's recorded output format). */
function outputFormatChips(a, b) {
  const fa = a?.output_format ?? null;
  const fb = b?.output_format ?? null;
  if (fa === fb) return [];
  return [
    chip("params", `output ${fa ?? "default"}${ARROW}${fb ?? "default"}`, {
      key: "output_format",
      from: fa,
      to: fb,
    }),
  ];
}

/** `MCP +postgres`, `MCP −acme-support`, `MCP acme-support 12 → 14 tools`. */
function mcpChips(a, b) {
  const env = mcpEnvChips(a, b);
  const ma = provOf(a)?.mcp ?? [];
  const mb = provOf(b)?.mcp ?? [];
  if (!ma.length && !mb.length) return env;
  const byA = new Map(ma.map((m) => [m.name, m]));
  const byB = new Map(mb.map((m) => [m.name, m]));
  const out = [];
  mb.filter((m) => !byA.has(m.name)).forEach((m) => {
    const t = `MCP +${m.name}`;
    out.push(chip("mcp", t, { added: m.name }, t));
  });
  ma.filter((m) => !byB.has(m.name)).forEach((m) => {
    const t = `MCP ${MINUS}${m.name}`;
    out.push(chip("mcp", t, { removed: m.name }, t));
  });
  mb.forEach((m) => {
    const prev = byA.get(m.name);
    if (!prev || prev.tools == null || m.tools == null) return;
    if (prev.tools === m.tools) return;
    const t = `MCP ${m.name} ${prev.tools}${ARROW}${m.tools} tools`;
    out.push(
      chip("mcp", t, { server: m.name, from: prev.tools, to: m.tools }, t),
    );
  });
  return [...out, ...env];
}

/** `Permissions Edit files → Full auto` (harness runs). */
function permissionsChips(a, b) {
  const la = provOf(a)?.harness?.permissionsLabel ?? null;
  const lb = provOf(b)?.harness?.permissionsLabel ?? null;
  if (!la || !lb || la === lb) return [];
  const t = `Permissions ${la}${ARROW}${lb}`;
  return [chip("permissions", t, { from: la, to: lb }, t)];
}

const CAPTURE_SHORT = {
  transcript: "full steps",
  final_only: "final result only",
  gateway: "model calls only",
};
const captureKey = (cap) =>
  !cap
    ? null
    : cap.steps === "transcript" || cap.steps === "final_only"
      ? cap.steps
      : cap.model_calls === "gateway"
        ? "gateway"
        : null;

/** `Capture: full steps → model calls only` (harness runs). */
function captureChips(a, b) {
  const ka = captureKey(provOf(a)?.capture);
  const kb = captureKey(provOf(b)?.capture);
  if (!ka || !kb || ka === kb) return [];
  const t = `Capture: ${CAPTURE_SHORT[ka]}${ARROW}${CAPTURE_SHORT[kb]}`;
  return [chip("capture", t, { from: ka, to: kb }, t)];
}

const SIDE_EFFECT_WORDS = {
  real: "real",
  stubbed: "tool stubs",
  dry_run: "dry run",
};
/** `Side effects: real → tool stubs`. */
function safetyChips(a, b) {
  const sa = a?.safety?.side_effects ?? null;
  const sb = b?.safety?.side_effects ?? null;
  if (!sa || !sb || sa === sb) return [];
  const t = `Side effects: ${SIDE_EFFECT_WORDS[sa] ?? sa}${ARROW}${SIDE_EFFECT_WORDS[sb] ?? sb}`;
  return [chip("safety", t, { from: sa, to: sb }, t)];
}

const whereWords = (c) => {
  const w = provOf(c)?.where;
  return w?.label ?? w?.text ?? null;
};
/** `Runs on alice@mbp-14 → Future AGI sandbox`. */
function executionChips(a, b) {
  const wa = whereWords(a);
  const wb = whereWords(b);
  if (!wa || !wb || wa === wb) return [];
  const t = `Runs on ${wa}${ARROW}${wb}`;
  return [
    chip("execution", t, { from: wa, to: wb, short: `${wa}${ARROW}${wb}` }, t),
  ];
}

const DEPTH_WORDS = {
  text_only: "Text only",
  full_voice: "Full voice",
  replay: "Replay production calls",
};
/** `WebSocket → LiveKit`, `Text only → Full voice`, `STT nova-2 → nova-3`, `TTS Rachel → Sarah`, `Turn detection provider default → semantic`. */
function voiceChips(a, b) {
  const va = provOf(a)?.voice ?? null;
  const vb = provOf(b)?.voice ?? null;
  if (!va || !vb) return [];
  const out = [];
  const push = (field, t, from, to) =>
    out.push(chip("voice", t, { field, from, to }, t));
  if ((va.provider ?? null) !== (vb.provider ?? null))
    push(
      "provider",
      `${text(va.providerName ?? va.provider)}${ARROW}${text(vb.providerName ?? vb.provider)}`,
      va.provider,
      vb.provider,
    );
  if ((va.depth ?? null) !== (vb.depth ?? null))
    push(
      "depth",
      `${DEPTH_WORDS[va.depth] ?? text(va.depth)}${ARROW}${DEPTH_WORDS[vb.depth] ?? text(vb.depth)}`,
      va.depth,
      vb.depth,
    );
  const textOnly = va.depth === "text_only" || vb.depth === "text_only";
  if (!textOnly && (va.stt?.model ?? null) !== (vb.stt?.model ?? null))
    push(
      "stt",
      `STT ${text(va.stt?.model)}${ARROW}${text(vb.stt?.model)}`,
      va.stt?.model ?? null,
      vb.stt?.model ?? null,
    );
  if (!textOnly && (va.tts?.voice ?? null) !== (vb.tts?.voice ?? null))
    push(
      "tts",
      `TTS ${text(va.tts?.voice)}${ARROW}${text(vb.tts?.voice)}`,
      va.tts?.voice ?? null,
      vb.tts?.voice ?? null,
    );
  if ((va.turn_detection ?? null) !== (vb.turn_detection ?? null))
    push(
      "turn_detection",
      `Turn detection ${text(va.turn_detection)}${ARROW}${text(vb.turn_detection)}`,
      va.turn_detection ?? null,
      vb.turn_detection ?? null,
    );
  return out;
}

/** `Endpoint api.acme.ai → api2.acme.ai`. */
function endpointChips(a, b) {
  const ea = provOf(a)?.endpoint ?? null;
  const eb = provOf(b)?.endpoint ?? null;
  if (!ea || !eb) return [];
  const out = [];
  if ((ea.protocol ?? null) !== (eb.protocol ?? null)) {
    const t = `Endpoint ${text(ea.protocol)}${ARROW}${text(eb.protocol)}`;
    out.push(chip("endpoint", t, { field: "protocol" }, t));
  }
  // L7c g2 (knob:http:method): Compare's What changed names a method change.
  if (ea.method && eb.method && ea.method !== eb.method) {
    const t = `Method ${text(ea.method)}${ARROW}${text(eb.method)}`;
    out.push(chip("endpoint", t, { field: "method" }, t));
  }
  if ((ea.url ?? null) !== (eb.url ?? null)) {
    const from = ea.host ?? ea.url;
    const to = eb.host === ea.host ? eb.url : eb.host ?? eb.url;
    const t = `Endpoint ${text(from)}${ARROW}${text(to)}`;
    out.push(
      chip("endpoint", t, { field: "url", from: ea.url, to: eb.url }, t),
    );
  }
  return out;
}

const agentVersionText = (g) =>
  g ? `${g.version ?? ""}${g.status === "draft" ? " (draft)" : ""}`.trim() : "";

function agentChips(a, b) {
  const ga = a?.agent ?? null;
  const gb = b?.agent ?? null;
  if (!ga && !gb) return [];
  if (same(ga, gb)) return [];
  const out = [];
  const nodesA = new Map((ga?.nodes ?? []).map((n) => [n.id, n]));
  const nodePairs = (gb?.nodes ?? [])
    .map((n) => ({ id: n.id, from: nodesA.get(n.id) ?? null, to: n }))
    .filter((p) => !same(p.from, p.to));
  (ga?.nodes ?? []).forEach((n) => {
    if (!(gb?.nodes ?? []).some((m) => m.id === n.id))
      nodePairs.push({ id: n.id, from: n, to: null });
  });
  const versionChanged =
    ga?.graph_id !== gb?.graph_id ||
    ga?.version !== gb?.version ||
    ga?.status !== gb?.status ||
    nodePairs.length > 0;
  if (versionChanged) {
    const sameName = (ga?.name ?? null) === (gb?.name ?? null);
    const from = ga
      ? `${sameName ? "" : `${ga.name} `}${agentVersionText(ga)}`.trim()
      : "none";
    const to = gb
      ? `${sameName ? "" : `${gb.name} `}${agentVersionText(gb)}`.trim()
      : "none";
    let label = `agent ${sameName && gb?.name ? `${gb.name} ` : ""}${from}${ARROW}${to}`;
    if (nodePairs.length === 1) {
      const p = nodePairs[0];
      const what =
        !p.from || !p.to
          ? p.to
            ? "added"
            : "removed"
          : (p.from.prompt ?? null) !== (p.to.prompt ?? null)
            ? "prompt changed"
            : (p.from.model ?? null) !== (p.to.model ?? null)
              ? "model changed"
              : "changed";
      label += `: ${p.id} ${what}`;
    } else if (nodePairs.length > 1) {
      label += `: ${nodePairs.length} nodes changed`;
    }
    out.push(
      chip(
        "agent",
        label,
        { from, to, nodes: nodePairs },
        dropWord(label, "agent"),
      ),
    );
  }
  const ia = ga?.limits?.max_iterations ?? null;
  const ib = gb?.limits?.max_iterations ?? null;
  if (ia !== ib)
    out.push(
      chip("agent", `max iterations ${text(ia)}${ARROW}${text(ib)}`, {
        field: "max_iterations",
        from: text(ia),
        to: text(ib),
      }),
    );
  return out;
}

const filesText = (files) => {
  const list = (files ?? []).filter((f) => f?.path);
  if (!list.length) return "";
  const shown = list
    .slice(0, 2)
    .map((f) => `${f.path} +${f.plus ?? 0} ${MINUS}${f.minus ?? 0}`);
  const more = list.length > 2 ? `, +${list.length - 2} files` : "";
  return ` (${shown.join(", ")}${more})`;
};

function codeChips(a, b) {
  const ca = a?.code ?? null;
  const cb = b?.code ?? null;
  if (!ca && !cb) return [];
  if (same(ca, cb)) return [];
  const out = [];
  const dirtyA = (ca?.dirty?.files ?? []).length > 0;
  const dirtyB = (cb?.dirty?.files ?? []).length > 0;
  if (
    ca?.commit !== cb?.commit ||
    !same(ca?.dirty ?? null, cb?.dirty ?? null)
  ) {
    const from = `${text(ca?.commit)}${dirtyA && ca?.commit === cb?.commit ? " (dirty)" : ""}`;
    const files = cb?.diff?.files ?? cb?.dirty?.files ?? [];
    const label = `code ${from}${ARROW}${text(cb?.commit)}${dirtyB || cb?.diff ? filesText(files) : ""}`;
    out.push(
      chip(
        "code",
        label,
        {
          from: text(ca?.commit),
          to: text(cb?.commit),
          files,
          repo: cb?.repo ?? ca?.repo ?? null,
        },
        dropWord(label, "code"),
      ),
    );
  }
  if ((ca?.entry ?? null) !== (cb?.entry ?? null) && ca?.entry && cb?.entry)
    out.push(
      chip("code", `entry ${ca.entry}${ARROW}${cb.entry}`, {
        field: "entry",
        from: ca.entry,
        to: cb.entry,
      }),
    );
  return out;
}

function tracesChips(a, b) {
  const ta = a?.traces ?? null;
  const tb = b?.traces ?? null;
  if (!ta && !tb) return [];
  const out = [];
  if ((ta?.project_id ?? null) !== (tb?.project_id ?? null))
    out.push(
      chip(
        "traces",
        `traces ${text(ta?.project)}${ARROW}${text(tb?.project)}`,
        { field: "project", from: text(ta?.project), to: text(tb?.project) },
      ),
    );
  if ((ta?.version ?? null) !== (tb?.version ?? null))
    out.push(
      chip(
        "traces",
        `traces ${text(ta?.version)}${ARROW}${text(tb?.version)}`,
        { field: "version", from: text(ta?.version), to: text(tb?.version) },
      ),
    );
  if ((ta?.output_path ?? null) !== (tb?.output_path ?? null))
    out.push(
      chip(
        "traces",
        `output path ${text(ta?.output_path)}${ARROW}${text(tb?.output_path)}`,
        {
          field: "output_path",
          from: text(ta?.output_path),
          to: text(tb?.output_path),
        },
      ),
    );
  if (!same(ta?.match, tb?.match))
    out.push(
      chip("traces", "trace matching changed", {
        field: "match",
        from: ta?.match ?? null,
        to: tb?.match ?? null,
      }),
    );
  return out;
}

const HARNESS_FIELDS = [
  ["max_turns", "max turns", text],
  ["timeout_s", "timeout", (v) => (v == null ? "none" : `${v} s`)],
  ["max_cost_usd", "max cost", money],
  ["sandbox", "sandbox", text],
  ["instructions_file", "instructions", text],
  // options-truth L1 C7: every harness knob a run can change.
  ["subcall_model", "helper model", text],
  ["command", "command", text],
  ["collect", "collect", collectText],
];

/**
 * `git diff, final message` from a harness's collect list or flags (L3 G9:
 * never `[object Object]`). Pure.
 */
export function collectText(v) {
  if (v === null || v === undefined || v === "") return "none";
  const words = (list) =>
    list.map((k) => String(k).replace(/_/g, " ")).join(", ") || "none";
  if (Array.isArray(v)) return words(v);
  if (typeof v === "object")
    return words(Object.keys(v).filter((k) => Boolean(v[k])));
  return text(v);
}

function harnessChips(a, b) {
  const ha = a?.harness ?? null;
  const hb = b?.harness ?? null;
  if (!ha && !hb) return [];
  const out = [];
  if ((ha?.name ?? null) !== (hb?.name ?? null))
    out.push(
      chip(
        "harness",
        `harness ${text(ha?.name)}${ARROW}${text(hb?.name)}`,
        { field: "name", from: text(ha?.name), to: text(hb?.name) },
        `${text(ha?.name)}${ARROW}${text(hb?.name)}`,
      ),
    );
  if (
    (ha?.name ?? null) === (hb?.name ?? null) &&
    (ha?.version ?? null) !== (hb?.version ?? null)
  )
    out.push(
      chip(
        "harness",
        `${text(hb?.name)} ${text(ha?.version)}${ARROW}${text(hb?.version)}`,
        { field: "version", from: text(ha?.version), to: text(hb?.version) },
      ),
    );
  HARNESS_FIELDS.forEach(([key, name, fmt]) => {
    const va = ha?.[key] ?? null;
    const vb = hb?.[key] ?? null;
    if (va === vb || (Array.isArray(va) && same(va, vb))) return;
    // Two spellings of one setting (a list and its flags) are no change.
    if (fmt(va) === fmt(vb)) return;
    out.push(
      chip("harness", `${name} ${fmt(va)}${ARROW}${fmt(vb)}`, {
        field: key,
        from: fmt(va),
        to: fmt(vb),
      }),
    );
  });
  // MCP servers moved to the `mcp` axis (R4).
  if (!same(ha?.user_sim ?? null, hb?.user_sim ?? null))
    out.push(
      chip(
        "harness",
        `user simulator ${text(ha?.user_sim?.model)}${ARROW}${text(hb?.user_sim?.model)}`,
        {
          field: "user_sim",
          from: ha?.user_sim ?? null,
          to: hb?.user_sim ?? null,
        },
      ),
    );
  return out;
}

const versionText = (v) => {
  if (v === null || v === undefined || v === "") return "none";
  const s = String(v);
  return /^\d+$/.test(s) ? `v${s}` : s;
};

function datasetChips(a, b) {
  const da = a?.dataset ?? null;
  const db = b?.dataset ?? null;
  if (!da && !db) return [];
  const out = [];
  const va = versionText(da?.version);
  const vb = versionText(db?.version);
  if (va !== vb) {
    let label = `dataset ${va}${ARROW}${vb}`;
    const ra = da?.rows;
    const rb = db?.rows;
    if (Number.isFinite(ra) && Number.isFinite(rb) && ra !== rb) {
      const n = Math.abs(rb - ra);
      const where = rb > ra ? vb : va;
      label += ` (+${n} ${n === 1 ? "row" : "rows"} only in ${where}, excluded)`;
    }
    out.push(
      chip("dataset", label, {
        from: va,
        to: vb,
        rows: { from: da?.rows ?? null, to: db?.rows ?? null },
      }),
    );
  }
  if ((da?.split ?? null) !== (db?.split ?? null))
    out.push(
      chip(
        "dataset",
        `split ${da?.split ?? "all rows"}${ARROW}${db?.split ?? "all rows"}`,
        { field: "split", from: da?.split ?? null, to: db?.split ?? null },
      ),
    );
  return out;
}

function evalChips(a, b) {
  const ea = a?.evals ?? [];
  const eb = b?.evals ?? [];
  if (same(ea, eb)) return [];
  const byA = new Map(ea.map((e) => [e.id, e]));
  const byB = new Map(eb.map((e) => [e.id, e]));
  const added = eb.filter((e) => !byA.has(e.id)).map((e) => e.id);
  const removed = ea.filter((e) => !byB.has(e.id)).map((e) => e.id);
  const out = [];
  if (added.length || removed.length)
    out.push(
      chip(
        "evals",
        `evals ${[...added.map((n) => `+${n}`), ...removed.map((n) => `${MINUS}${n}`)].join(" ")}`,
        { added, removed },
      ),
    );
  eb.forEach((e) => {
    const prev = byA.get(e.id);
    if (prev && (prev.version ?? null) !== (e.version ?? null))
      out.push(
        chip(
          "evals",
          `${e.id} ${text(prev.version)}${ARROW}${text(e.version)}`,
          {
            eval: e.id,
            from: text(prev.version),
            to: text(e.version),
          },
        ),
      );
  });
  return out;
}

// options-truth L1 C7: run limits (tries per run, concurrency, cost cap,
// timeout) and the replay mode.
const LIMIT_FIELDS = [
  ["tries", "tries", text],
  ["concurrency", "at a time", text],
  ["max_cost_usd", "cost cap", money],
  ["timeout_s", "timeout", (v) => (v == null ? "none" : `${v} s`)],
];
function limitChips(a, b) {
  const la = a?.limits ?? null;
  const lb = b?.limits ?? null;
  const out = [];
  if (la || lb)
    LIMIT_FIELDS.forEach(([key, name, fmt]) => {
      const va = la?.[key] ?? null;
      const vb = lb?.[key] ?? null;
      if (va === vb) return;
      out.push(
        chip("trials", `${name} ${fmt(va)}${ARROW}${fmt(vb)}`, {
          field: key,
          from: fmt(va),
          to: fmt(vb),
        }),
      );
    });
  const ra = a?.replay?.mode ?? null;
  const rb = b?.replay?.mode ?? null;
  if (ra !== rb)
    out.push(
      chip("trials", `replay ${text(ra)}${ARROW}${text(rb)}`, {
        field: "replay",
        from: text(ra),
        to: text(rb),
      }),
    );
  return out;
}

function trialChips(a, b) {
  const ta = a?.trials ?? null;
  const tb = b?.trials ?? null;
  if (ta === tb) return [];
  return [
    chip("trials", `trials ${text(ta)}${ARROW}${text(tb)}`, {
      from: text(ta),
      to: text(tb),
    }),
  ];
}

const AXIS_CHIPS = {
  framework: frameworkChips,
  prompt: promptChips,
  model: modelChips,
  models: modelsChips,
  params: (a, b) => [...paramChips(a, b), ...outputFormatChips(a, b)],
  tools: toolChips,
  mcp: mcpChips,
  permissions: permissionsChips,
  capture: captureChips,
  safety: safetyChips,
  agent: agentChips,
  code: codeChips,
  execution: executionChips,
  voice: voiceChips,
  endpoint: endpointChips,
  harness: harnessChips,
  traces: tracesChips,
  dataset: datasetChips,
  evals: evalChips,
  trials: (a, b) => [...trialChips(a, b), ...limitChips(a, b)],
};

// Across kinds, the blocks only one side has (prompt, agent, code, harness,
// voice …) are what the kind chip already says; these axes still compare.
const CROSS_KIND_AXES = new Set([
  "model",
  "models",
  "tools",
  "mcp",
  "safety",
  "execution",
  "dataset",
  "evals",
  "trials",
]);

/** `Your code (LangGraph)`, `Agent harness (the coding harness Code)`, `Voice agent (LiveKit)`, else kindDiffLabel. */
function kindName(config) {
  const kind = config?.kind ?? "prompt";
  const name = provOf(config)?.source?.name ?? null;
  if (["code", "harness", "voice"].includes(kind) && name)
    return `${KIND_NAMES[kind]} (${name})`;
  return kindDiffLabel(config);
}

/**
 * Ordered change chips between a baseline and a candidate config.
 *
 * @param {object} a  baseline run (with `.config`) or a config
 * @param {object} b  candidate run (with `.config`) or a config
 * @param {{all?: boolean}} [opts]  `all`: also return one `unchanged` entry
 *   per unchanged axis (the `Show all` list)
 * @returns {Array<{axis: string, label: string, text: string,
 *   detail: object, unchanged?: boolean}>}  `[]` when either side has no
 *   config. Runs of different kinds give the `kind` chip first (its
 *   `detail.differ` = the number of axes that differ) and then the chips of
 *   the axes both kinds share (model, tools, MCP, where it ran, …).
 */
export function configDiff(a, b, { all = false } = {}) {
  const ca = configOf(a);
  const cb = configOf(b);
  if (!ca || !cb) return [];
  const byAxis = Object.fromEntries(
    Object.entries(AXIS_CHIPS).map(([axis, fn]) => [axis, fn(ca, cb)]),
  );
  const kindA = ca.kind ?? "prompt";
  const kindB = cb.kind ?? "prompt";
  if (kindA !== kindB) {
    const differ =
      1 + Object.values(byAxis).filter((chips) => chips.length > 0).length;
    const from = kindDiffLabel(ca);
    const to = kindDiffLabel(cb);
    const nameA = kindName(ca);
    const nameB = kindName(cb);
    const srcA = provOf(ca)?.source?.name ?? from;
    const srcB = provOf(cb)?.source?.name ?? to;
    const framework = ["code", "harness", "voice"].some(
      (k) => k === kindA || k === kindB,
    );
    return [
      chip(
        "kind",
        `kind ${from}${ARROW}${to}`,
        {
          from,
          to,
          differ,
          axes: Object.keys(byAxis).filter((k) => byAxis[k].length),
          noun: framework ? "framework" : "kind",
          short: `${srcA}${ARROW}${srcB}`,
        },
        `${nameA}${ARROW}${nameB}`,
      ),
      // A side that simply has no such setting is what the kind chip says.
      ...AXES.filter((axis) => CROSS_KIND_AXES.has(axis))
        .flatMap((axis) => byAxis[axis] ?? [])
        .filter((c) => c.detail?.from !== "none" && c.detail?.to !== "none"),
    ];
  }
  const out = [];
  AXES.forEach((axis) => {
    if (axis === "kind") {
      if (all)
        out.push({
          axis,
          label: `kind ${kindDiffLabel(cb)}`,
          text: kindName(cb),
          detail: { from: kindDiffLabel(ca), to: kindDiffLabel(cb) },
          unchanged: true,
        });
      return;
    }
    const chips = byAxis[axis];
    if (chips.length) out.push(...chips);
    else if (all)
      out.push({
        axis,
        label: `${axis} unchanged`,
        text: `${AXIS_NOUNS[axis] ?? axis} unchanged`,
        detail: {},
        unchanged: true,
      });
  });
  return out;
}

/** How the sentence names each axis. */
export const AXIS_NOUNS = Object.freeze({
  kind: "kind",
  framework: "framework",
  prompt: "prompt",
  model: "model",
  models: "other models",
  params: "parameters",
  tools: "tools",
  mcp: "MCP",
  permissions: "permissions",
  capture: "capture",
  safety: "side effects",
  agent: "agent",
  code: "code",
  execution: "where it runs",
  voice: "voice",
  endpoint: "endpoint",
  harness: "harness",
  traces: "traces",
  dataset: "dataset",
  evals: "evals",
  trials: "tries",
});

const MAX_SENTENCE_PARTS = 4;
const MAX_PART_ITEMS = 2;

/** The words a chip adds to the sentence (without its axis prefix). */
function sentenceWords(c) {
  if (c.detail?.short) return c.detail.short;
  const t = String(c.text ?? c.label);
  const prefix = {
    mcp: "MCP ",
    permissions: "Permissions ",
    capture: "Capture: ",
    safety: "Side effects: ",
    endpoint: "Endpoint ",
    trials: "trials ",
    dataset: "dataset ",
    evals: "evals ",
    traces: "traces ",
  }[c.axis];
  return prefix && t.startsWith(prefix) ? t.slice(prefix.length) : t;
}

const more = (n) => `and ${n} more`;

/**
 * The plain sentence of what changed, or null when nothing did:
 * `Changed: framework (Agent graph → LangGraph), model (gpt-4o-mini →
 * gpt-4.1-mini), tools (+search_kb_v2).` Every word comes from the chips
 * (no number is written here; §C10).
 */
export function configDiffSentence(chips) {
  const changes = (chips ?? []).filter((c) => !c.unchanged);
  if (!changes.length) return null;
  const groups = [];
  changes.forEach((c) => {
    const noun =
      c.axis === "kind"
        ? c.detail?.noun ?? "kind"
        : AXIS_NOUNS[c.axis] ?? c.axis;
    let g = groups.find((x) => x.noun === noun);
    if (!g) {
      g = { noun, words: [] };
      groups.push(g);
    }
    g.words.push(sentenceWords(c));
  });
  const partOf = (g) => {
    const shown = g.words.slice(0, MAX_PART_ITEMS).join(", ");
    const rest = g.words.length - MAX_PART_ITEMS;
    return `${g.noun} (${shown}${rest > 0 ? ` ${more(rest)}` : ""})`;
  };
  const parts = groups.slice(0, MAX_SENTENCE_PARTS).map(partOf);
  const restGroups = groups.length - MAX_SENTENCE_PARTS;
  const tail =
    restGroups > 0
      ? `, ${more(restGroups)} ${restGroups === 1 ? "setting" : "settings"}`
      : "";
  return `Changed: ${parts.join(", ")}${tail}.`;
}

/**
 * Which run the `What changed` strip pairs with the baseline: `cand=` when
 * it is in view, else the run the drawer is focused on (`run=`), else the
 * first run that is not the baseline. `null` with no baseline (`none`).
 */
export function stripCandidate({ cand, focused, baselineId, runIds = [] }) {
  if (!baselineId || baselineId === "none") return null;
  const ok = (id) => Boolean(id) && id !== baselineId && runIds.includes(id);
  if (ok(cand)) return cand;
  if (ok(focused)) return focused;
  return runIds.find((id) => id !== baselineId) ?? null;
}
