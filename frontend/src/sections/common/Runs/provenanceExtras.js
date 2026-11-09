// What F's provenanceModel leaves to the card (R4 implementation, MUST 1): the
// facts the `chips` and `card` variants print, as plain data. Pure: no
// React, no clock. Every number comes from the run's config or its tries.
//
//   chipsOf(p) → [{key, text, tooltip?, warn?}]
//   modelRows(p), toolRows(p), paramRows(p), whereRows(p), codeRows(p, tries),
//   voiceRows(p, run), harnessRows(p), safetyText(p), keysText(p),
//   triesOnShaText({sha, bySha}), changedSections(compareTo, run)
import { fmtCost, fmtMs } from "src/sections/common/format/fmt";
import { configDiff } from "./configDiff";
import { captureLabel } from "./provenanceModel";

const SEP = " · ";
const join = (parts) => parts.filter((x) => x != null && x !== "").join(SEP);
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** Where a model id was seen: declared by the agent, seen in the run's spans, or both. */
export function declaredSeen(model) {
  const s = model?.source ?? "declared";
  return {
    declared: s === "declared" || s === "both",
    seen: s === "observed" || s === "both",
  };
}

const ROLE_WORDS = {
  main: "main",
  stt: "speech to text",
  tts: "text to speech",
  caller: "simulated caller",
  "sub-calls": "sub-calls",
};
const roleWords = (role) =>
  String(role ?? "").startsWith("node:")
    ? `node ${String(role).slice(5)}`
    : ROLE_WORDS[role] ?? role ?? "main";

/** Models section rows: `{id, role, calls, declared, seen}`. */
export function modelRows(p) {
  return (p?.models ?? []).map((m) => ({
    id: m.id,
    role: roleWords(m.role),
    calls: m.calls ?? null,
    ...declaredSeen(m),
  }));
}

/** Tools section rows with the amber warning text (D7). */
export function toolRows(p) {
  const warnings = p?.warnings ?? [];
  return (p?.tools ?? []).map((t) => ({
    name: t.name,
    mode: t.mode ?? null,
    sideEffect: Boolean(t.side_effect),
    warning: warnings.find((w) => w.tool === t.name)?.text ?? null,
  }));
}

const valueText = (v) =>
  v === null || v === undefined
    ? "—"
    : typeof v === "object"
      ? JSON.stringify(v)
      : String(v);

/**
 * Parameters (`name = value`); `changed` when the value differs from the
 * code default (the card prints it bold with the default in the tooltip).
 */
export function paramRows(p) {
  const schema = p?.params?.schema ?? [];
  const values = p?.params?.values ?? {};
  const names = [
    ...schema.map((s) => s.name),
    ...Object.keys(values).filter((k) => !schema.some((s) => s.name === k)),
  ];
  return names.map((name) => {
    const def = schema.find((s) => s.name === name);
    const value = values[name] ?? def?.default ?? null;
    const isPrompt = def?.type === "prompt";
    const changed =
      def?.default !== undefined &&
      JSON.stringify(value) !== JSON.stringify(def.default);
    return {
      name,
      label: def?.label ?? name,
      value: isPrompt
        ? plural(String(value ?? "").length, "char")
        : valueText(value),
      full: valueText(value),
      changed,
      defaultText: def?.default !== undefined ? valueText(def.default) : null,
      source: def?.source ?? null,
    };
  });
}

const MODE_WORDS = {
  local: "This machine",
  ci: "CI",
  sandbox: "Future AGI sandbox",
  remote: "Remote endpoint",
  platform: "Runs in Future AGI",
};

/** Where it ran: `{label, value}` rows. */
export function whereRows(p) {
  const w = p?.where ?? {};
  // L7c g5 (after:run-details-where): a run that ran on a machine names
  // whose machine it was from what the run recorded (`Ravi Menon's
  // machine`), never "This machine" (the viewer's) for someone's worker.
  const owner = p?.owner?.name ?? null;
  const mode =
    w.mode === "local"
      ? owner
        ? `${owner}'s machine`
        : "A machine"
      : MODE_WORDS[w.mode] ?? null;
  return [
    { label: "Mode", value: mode },
    // `Runs in Future AGI` says it all for prompt and agent runs.
    {
      label: w.mode === "local" ? "Machine" : w.mode === "ci" ? "Runner" : "On",
      // `On Future AGI sandbox` would repeat the mode.
      value:
        w.mode === "platform"
          ? null
          : w.mode === "local"
            ? w.label
            : (w.runner ?? w.label) === MODE_WORDS[w.mode]
              ? null
              : w.runner ?? w.label ?? null,
      machineId: w.mode === "local" ? w.machineId ?? null : null,
    },
    { label: "Owner", value: p?.owner?.name ?? null },
    { label: "Image", value: w.image ?? null },
    { label: "Network", value: w.network ?? null },
    {
      label: "At a time",
      value: w.concurrency ? `${w.concurrency} at a time` : null,
    },
  ].filter((r) => r.value);
}

/**
 * `All 90 tries ran on 9f2c1a7`, or `84 of 90 tries ran on 9f2c1a7 · 6 on
 * a41c2e0` (watch mode reloaded mid-run). Null without tries.
 * @param {{sha?: string, bySha?: Array<{sha: string, n: number}>}} t
 */
export function triesOnShaText({ sha, bySha } = {}) {
  const list = (bySha ?? []).filter((x) => x?.sha && x.n > 0);
  if (!list.length) return null;
  const total = list.reduce((s, x) => s + x.n, 0);
  const short = (s) => String(s).slice(0, 7);
  if (list.length === 1)
    return `All ${plural(total, "try", "tries")} ran on ${short(list[0].sha)}`;
  const sorted = [...list].sort(
    (a, b) => Number(b.sha === sha) - Number(a.sha === sha) || b.n - a.n,
  );
  const [first, ...rest] = sorted;
  return join([
    `${first.n} of ${plural(total, "try", "tries")} ran on ${short(first.sha)}`,
    ...rest.map((x) => `${x.n} on ${short(x.sha)}`),
  ]);
}

/** Code section rows: repo @ branch, sha, dirty files, diff snapshot, tries. */
export function codeRows(p, tries) {
  const c = p?.code;
  if (!c) return [];
  const files = c.dirty?.files ?? [];
  return [
    {
      label: "Repo",
      value: c.repo ? `${c.repo}${c.branch ? ` @ ${c.branch}` : ""}` : null,
    },
    { label: "Entry", value: c.entry ?? null, mono: true },
    { label: "Commit", value: c.sha ?? null, copy: c.commit ?? c.sha },
    {
      label: "Changes",
      value: files.length
        ? `${plural(files.length, "file")} not committed`
        : c.sha
          ? "None (clean tree)"
          : null,
      files: files.map((f) => f.path ?? String(f)),
    },
    {
      label: "Diff snapshot",
      value: files.length
        ? c.diff_snapshot
          ? "Diff snapshot saved"
          : "Not saved: this run can't be reproduced from the sha alone"
        : null,
      warn: files.length > 0 && !c.diff_snapshot,
    },
    { label: "Tries", value: triesOnShaText({ sha: c.sha, bySha: tries }) },
    { label: "Runtime", value: c.runtime ?? null },
  ].filter((r) => r.value);
}

// Self-hosted voice connectors (dataset integration P4b) and TTS vendors.
const PROVIDERS = {
  websocket: "WebSocket",
  webrtc: "WebRTC",
  livekit: "LiveKit",
  pipecat: "Pipecat",
  elevenlabs: "ElevenLabs",
  cartesia: "Cartesia",
};

/**
 * Where a voice run's latency numbers come from (D16): `From LiveKit
 * metrics · Preview`, `Reported by LiveKit`, `Estimated from the recording`;
 * text-only runs have none.
 * @returns {{text: string, preview: boolean}|null}
 */
export function latencySourceOf(p, run) {
  const v = p?.voice;
  if (!v || v.depth === "text_only") return null;
  const src = run?.voice_metrics?.latency_sources;
  if (src && src.provider > 0 && !src.recording)
    return {
      text: `Reported by ${PROVIDERS[v.provider] ?? v.providerName}`,
      preview: false,
    };
  return { text: "Estimated from the recording", preview: true };
}

/** Voice section rows. */
export function voiceRows(p, run) {
  const v = p?.voice;
  if (!v) return [];
  const textOnly = v.depth === "text_only";
  const tts = [
    v.tts?.provider ? PROVIDERS[v.tts.provider] ?? v.tts.provider : null,
    v.tts?.voice,
  ]
    .filter(Boolean)
    .join(" ");
  const src = latencySourceOf(p, run);
  return [
    { label: "Provider", value: v.providerName ?? v.provider },
    {
      label: "Depth",
      value:
        v.depth === "text_only"
          ? "Text only"
          : v.depth === "replay"
            ? "Replay production calls"
            : "Full voice",
      term: textOnly ? "textOnlyDepth" : "fullVoiceDepth",
    },
    { label: "STT", value: textOnly ? null : v.stt?.model ?? null },
    { label: "LLM", value: v.llm?.model ?? null },
    { label: "TTS", value: textOnly ? null : tts || null },
    { label: "Turn detection", value: v.turn_detection ?? null },
    {
      label: "Latency",
      value:
        src?.text ?? (textOnly ? "Text only · no audio, so no latency" : null),
      preview: src?.preview ?? false,
    },
  ].filter((r) => r.value);
}

const BUILTIN = {
  shell: "Shell",
  edit: "Edit",
  web_fetch: "Web fetch",
  web_search: "Web search",
};

/** Harness section rows (permissions, built-in tools, budget, capture). */
export function harnessRows(p) {
  const h = p?.harness;
  if (!h) return [];
  const on = Object.entries(h.builtin_tools ?? {})
    .filter(([, v]) => v)
    .map(([k]) => BUILTIN[k] ?? k);
  const b = h.budget ?? {};
  return [
    { label: "Harness", value: join([h.name, h.version]) },
    { label: "Permissions", value: h.permissionsLabel, term: null },
    { label: "Built-in tools", value: on.length ? on.join(", ") : null },
    {
      label: "Budget",
      value: join([
        b.max_turns != null ? plural(b.max_turns, "turn") : null,
        b.max_cost_usd != null ? `${fmtCost(b.max_cost_usd)} per try` : null,
        b.timeout_s != null ? `${fmtMs(b.timeout_s * 1000)} timeout` : null,
      ]),
    },
    {
      label: "Capture",
      value: captureLabel(p),
      term: "captureMode",
      note:
        p?.capture?.steps === "transcript"
          ? "Steps come from the harness transcript; model calls through the gateway."
          : p?.capture?.model_calls === "gateway"
            ? "Only model calls are captured (the gateway needs an API key; a subscription login can't be redirected)."
            : null,
    },
    {
      label: "Sub-agents",
      value: h.subagents?.length ? h.subagents.join(", ") : null,
    },
  ].filter((r) => r.value);
}

/** `Calls real systems: issue_refund · ran for real` / `Tool stubs: issue_refund`. */
export function safetyText(p) {
  const s = p?.safety ?? {};
  const tools = s.sideEffectTools ?? [];
  if (s.side_effects === "stubbed")
    return {
      text: `Tool stubs${tools.length ? `: ${tools.join(", ")}` : ""}`,
      warn: false,
    };
  if (s.side_effects === "dry_run")
    return { text: "Dry run: no tool touched a real system", warn: false };
  if (s.side_effects === "real" && tools.length)
    return {
      text: `Calls real systems: ${tools.join(", ")} · ran for real`,
      warn: true,
    };
  if (s.side_effects === "real")
    return { text: "No tool with side effects", warn: false };
  return null;
}

/** `from alice@mbp-14's environment` / `Workspace secrets` / `Future AGI gateway`. */
export function keysText(p) {
  const k = p?.secrets?.model_keys;
  if (!k) return null;
  if (k === "machine")
    return `from ${p?.where?.label ?? "the machine"}'s environment${p.secrets.ref ? ` (${p.secrets.ref})` : ""}`;
  if (k === "workspace")
    return `Workspace secrets${p.secrets.ref ? ` (${p.secrets.ref})` : ""}`;
  if (k === "gateway") return "Future AGI gateway";
  return String(k);
}

/**
 * The short chips (lane headers, drawer cards): source, models, tools,
 * MCP, where, sha / capture. `warn` = a declared-vs-observed tool warning.
 */
export function chipsOf(p) {
  if (!p) return [];
  const mains = (p.models ?? []).filter(
    (m) => !["stt", "tts", "caller"].includes(m.role),
  );
  const ids = [...new Set(mains.map((m) => m.id))];
  const tools = p.tools?.length ?? 0;
  const v = p.voice;
  return [
    {
      key: "source",
      text: p.harness?.name ?? p.voice?.providerName ?? p.source?.name,
    },
    ids.length
      ? {
          key: "models",
          text: ids.length > 1 ? `${ids[0]} +${ids.length - 1}` : ids[0],
          tooltip: ids.join(", "),
        }
      : null,
    v && v.depth !== "text_only" && v.stt?.model
      ? { key: "stt", text: v.stt.model, tooltip: "Speech to text" }
      : null,
    v?.depth === "text_only" ? { key: "depth", text: "Text only" } : null,
    tools
      ? {
          key: "tools",
          text: plural(tools, "tool"),
          tooltip: p.tools.map((t) => t.name).join(", "),
          warn: (p.warnings ?? []).length > 0,
          warnText: (p.warnings ?? []).map((w) => w.text).join(" · ") || null,
        }
      : null,
    p.mcp?.length
      ? {
          key: "mcp",
          text: `MCP ${p.mcp[0].name}${p.mcp[0].tools != null ? ` (${p.mcp[0].tools})` : ""}${p.mcp.length > 1 ? ` +${p.mcp.length - 1}` : ""}`,
        }
      : null,
    p.harness?.permissionsLabel
      ? { key: "permissions", text: p.harness.permissionsLabel }
      : null,
    p.where?.label && p.where?.mode !== "platform"
      ? {
          key: "where",
          text: p.where.text ?? p.where.label,
          tooltip: p.where.label,
        }
      : null,
    p.code?.sha
      ? {
          key: "code",
          text: `${p.code.sha}${p.code.dirty?.files?.length ? "*" : ""}`,
          tooltip: p.code.dirty?.files?.length
            ? `Uncommitted changes in ${p.code.dirty.files.length} files`
            : `Commit ${p.code.sha}`,
        }
      : null,
    captureLabel(p) ? { key: "capture", text: captureLabel(p) } : null,
  ].filter((c) => c && c.text);
}

/** Card sections a what-changed axis belongs to (D7: changed facts first). */
const AXIS_SECTION = {
  kind: "header",
  framework: "header",
  model: "models",
  models: "models",
  prompt: "prompts",
  params: "params",
  tools: "tools",
  mcp: "mcp",
  permissions: "harness",
  capture: "harness",
  harness: "harness",
  safety: "safety",
  agent: "header",
  code: "code",
  execution: "where",
  voice: "voice",
  endpoint: "endpoint",
};

/**
 * Which card sections changed vs another run, with the chip texts:
 * `Map<section, string[]>` (empty without `compareTo`).
 */
export function changedSections(compareTo, run) {
  const out = new Map();
  if (!compareTo?.config || !run?.config) return out;
  configDiff(compareTo, run).forEach((c) => {
    const s = AXIS_SECTION[c.axis];
    if (!s) return;
    if (!out.has(s)) out.set(s, []);
    out.get(s).push(c.text ?? c.label);
  });
  return out;
}
