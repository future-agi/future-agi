// What a run used (R4-SPEC D7, D26; implementation; frozen API). The union of what
// was declared (handshake / form) and observed (spans). One line for lists,
// the rest for X's chips and card. Pure: no React, no clock, no random.
//
//   provenanceOf(run) → {source, models, tools, mcp, prompts, params, where,
//     owner, code, voice, harness, capture, endpoint, safety, warnings, …}
//   provenanceLine(p) → `LangGraph · gpt-4.1-mini +1 · 7 tools · MCP
//     acme-support · alice@mbp-14 · 9f2c1a7`
//   captureLabel(p) → `Model calls only (gateway)` / `Full steps (harness
//     transcript)` / `Final result only (no transcript)` / null
//   agentVersionOf(run), latestVsPrevious(runs)
import {
  catalogEntry,
  entryOfConfig,
  modeLabel,
} from "src/sections/common/RunSources/catalog";
import { VOCAB } from "src/sections/common/vocab";
import { fmtCost } from "src/sections/common/format/fmt";
import { isMoneyValue } from "src/sections/common/format/money";
import { sandboxText } from "./runWhere";
import { runKindOf } from "./runKind";

const SEP = " · ";
const join = (parts) => parts.filter((p) => p != null && p !== "").join(SEP);
const uniq = (list) => [...new Set(list.filter(Boolean))];

export const PERMISSION_LABELS = Object.freeze({
  read_only: "Read only",
  edit: "Edit files",
  full_auto: "Full auto",
});

export const CAPTURE_LABELS = Object.freeze({
  gateway: VOCAB.CAPTURE_GATEWAY,
  transcript: VOCAB.CAPTURE_TRANSCRIPT,
  final_only: VOCAB.CAPTURE_FINAL_ONLY,
});

const PROVIDER_NAMES = {
  // Self-hosted voice connectors (dataset integration P4b); TTS / STT model vendors.
  websocket: "WebSocket",
  webrtc: "WebRTC",
  livekit: "LiveKit Agents",
  pipecat: "Pipecat",
  worker: "Your voice agent",
  elevenlabs: "ElevenLabs",
  cartesia: "Cartesia",
  openai: "OpenAI",
  deepgram: "Deepgram",
  google: "Google",
  azure: "Azure",
};
const providerName = (p) => PROVIDER_NAMES[p] ?? p ?? null;

const shortSha = (sha) => (sha ? String(sha).slice(0, 7) : null);
const hostOf = (url) => {
  try {
    return new URL(url).host;
  } catch {
    return url ?? null;
  }
};

/** Models of a run: R4 `models[]`, else derived from the R2 config / run. */
function modelsOf(c, kind, run) {
  // options-truth L3 G3 (S5, I2): a created run names the model it used
  // (`config.engine.model`). A harness run: that model, its helper apart
  // (the line prints `helper …`); a code run: that model first, marked
  // `seen in this run` when the agent declared another one.
  const used = c.engine?.model ?? null;
  if (kind === "harness" && used)
    return [{ id: used, role: "main", calls: null, source: "seen" }];
  if (Array.isArray(c.models) && c.models.length) {
    const list = c.models.map((m) => ({
      id: m.id,
      role: m.role ?? "main",
      calls: m.calls ?? null,
      source: m.source ?? "declared",
    }));
    if (used && kind === "code" && !list.some((m) => m.id === used))
      return [
        { id: used, role: "main", calls: null, source: "seen" },
        ...list.filter((m) => m.role !== "main"),
      ];
    return list;
  }
  const one = (id, role = "main") =>
    id ? [{ id, role, calls: null, source: "declared" }] : [];
  if (kind === "agent") {
    const seen = new Set();
    const nodes = (c.agent?.nodes ?? [])
      .filter((n) => n.model && !seen.has(n.model) && seen.add(n.model))
      .map((n) => ({
        id: n.model,
        role: `node:${n.id}`,
        calls: null,
        source: "declared",
      }));
    return nodes.length ? nodes : one(run?.profile?.model);
  }
  if (kind === "harness")
    return one(c.harness?.model ?? c.model?.id ?? run?.target?.model);
  if (kind === "code" && used)
    return [{ id: used, role: "main", calls: null, source: "seen" }];
  if (kind === "upload" || kind === "traces" || kind === "http") return [];
  return one(c.model?.id ?? run?.target?.model ?? run?.profile?.model);
}

/** Tools with declared / called flags (R2 configs: declared = used). */
function toolsOf(c) {
  return (c.tools ?? []).map((t) => ({
    name: t.name,
    mode: t.mode ?? null,
    declared: t.declared ?? true,
    called: t.called ?? true,
    side_effect: Boolean(t.side_effect),
  }));
}

/**
 * How many tools an MCP server has. A harness config (C4) lists the names in
 * `tools` with `tool_count` beside them; older configs and the recipe editor
 * carry the count in `tools`. null when neither says.
 */
export function mcpToolCount(m) {
  if (Number.isFinite(m?.tool_count)) return m.tool_count;
  if (Array.isArray(m?.tools)) return m.tools.length;
  return Number.isFinite(m?.tools) ? m.tools : null;
}

function mcpOf(c) {
  const list = c.mcp_servers?.length ? c.mcp_servers : c.harness?.mcp_servers;
  return (list ?? []).map((m) => ({
    name: m.name,
    tools: mcpToolCount(m),
    transport: m.transport ?? null,
    permission: m.permission ?? null,
  }));
}

/** Where the run ran: `{mode, label, text, …}` (text = the line's words). */
function whereOf(c, kind, entry) {
  const e = c.execution;
  if (e?.mode) {
    let text = e.label;
    // options-truth L2 R2: a created sandbox run names what it ran
    // (`Future AGI sandbox · repo@branch · entry · network Off · 1 vCPU`).
    // A seeded run's `execution` block stays in Run details (runSourceLines),
    // so the one-line provenance keeps its D7 shape.
    if (e.mode === "sandbox")
      text = sandboxText(c, { fromExecution: false }) ?? "Future AGI sandbox";
    else if (e.mode === "ci") text = e.runner ?? e.label ?? "CI";
    else if (e.mode === "platform") text = null;
    else if (e.mode === "remote" && kind === "voice") text = "Remote endpoint";
    else if (e.mode === "remote" && kind === "http")
      text = hostOf(c.endpoint?.url) ?? e.label;
    return {
      mode: e.mode,
      label: e.label ?? modeLabel(e.mode, entry),
      text,
      machineId: e.machine_id ?? null,
      agentId: e.agent_id ?? null,
      runner: e.runner ?? null,
      image: e.image ?? null,
      network: e.network ?? null,
      concurrency: e.concurrency ?? null,
    };
  }
  // R2 configs: derive.
  if (kind === "code" && c.code?.host)
    return { mode: "local", label: c.code.host, text: c.code.host };
  if (kind === "harness" && c.harness?.sandbox) {
    const first = String(c.harness.sandbox).split(SEP)[0];
    return { mode: "local", label: c.harness.sandbox, text: first };
  }
  if (kind === "http") {
    const host = hostOf(c.endpoint?.url ?? c.http?.url);
    return { mode: "remote", label: host, text: host };
  }
  if (kind === "prompt" || kind === "agent")
    return { mode: "platform", label: "Future AGI", text: null };
  if (kind === "upload" && c.code) {
    const text =
      String(c.code.runtime ?? "").split(SEP)[0] || c.code.host || null;
    return { mode: "ci", label: text, text };
  }
  return { mode: null, label: null, text: null };
}

// options-truth L1 finalize: an A2A agent card or an MCP server tool run is
// an endpoint run (`target_type` http) whose config names the protocol.
const endpointProtocol = (c) =>
  c.kind === "a2a"
    ? "a2a"
    : c.kind === "mcp_tool"
      ? "mcp"
      : c.endpoint?.protocol ?? "http";
const PROTOCOL_NAMES = { a2a: "A2A agent", mcp: "MCP tool" };

const sourceName = (kind, entry, c) => {
  if (kind === "prompt") return "Prompt";
  if (kind === "agent") return "Agent graph";
  if (kind === "http") return PROTOCOL_NAMES[endpointProtocol(c)] ?? "Endpoint";
  if (kind === "traces") return "Traces";
  if (kind === "upload") return "Upload";
  return (
    c.framework?.name ??
    entry?.name ??
    c.harness?.name ??
    (String(c.code?.framework ?? "").replace(/\s+[\d.]+.*$/, "") || null)
  );
};

export const SIDE_EFFECT_LABELS = Object.freeze({
  stubs: "Stubbed side effects",
  dry_run: "Dry run",
});
const REPLAY_LABELS = Object.freeze({
  score_only: "Replay · score only",
  failing_turn: "Replay · failing turn",
  rollout: "Replay · rollout",
  full: "Replay · full re-run",
});
// Preserve the supplied decimal cap all the way to the shared renderer.
const usd = fmtCost;

/**
 * options-truth L1 C7: the run settings every kind shows, from the config
 * (`limits`, `safety`, `replay`, prompt params). Null parts are dropped.
 * @param {object} c  run config
 */
export function settingsOf(c = {}) {
  const l = c.limits ?? null;
  const tries = Number(l?.tries) || null;
  const conc = Number(l?.concurrency ?? c.execution?.concurrency) || null;
  return {
    tries: l ? tries : null,
    concurrency: l ? conc : null,
    // options-truth L3 G3: the per-try cap each try really ran with.
    max_cost_per_try_usd: isMoneyValue(l?.max_cost_per_try_usd)
      ? l.max_cost_per_try_usd
      : null,
    max_cost_usd: isMoneyValue(l?.max_cost_usd) ? l.max_cost_usd : null,
    timeout_s: Number(l?.timeout_s) || null,
    side_effects:
      c.safety?.side_effects && c.safety.side_effects !== "real"
        ? c.safety.side_effects
        : null,
    replay: c.replay?.mode ?? null,
  };
}

/** `4 tries · 2 at a time · cap $0.30 · timeout 30 s · Dry run` (pure). */
export function settingsText(c = {}) {
  const s = settingsOf(c);
  return join([
    s.tries ? `${s.tries} ${s.tries === 1 ? "try" : "tries"}` : null,
    s.concurrency ? `${s.concurrency} at a time` : null,
    s.max_cost_per_try_usd != null
      ? `${usd(s.max_cost_per_try_usd)} per try`
      : null,
    s.max_cost_usd != null ? `cap ${usd(s.max_cost_usd)}` : null,
    s.timeout_s ? `timeout ${s.timeout_s} s` : null,
    s.side_effects
      ? SIDE_EFFECT_LABELS[s.side_effects] ?? s.side_effects
      : null,
    s.replay ? REPLAY_LABELS[s.replay] ?? `Replay · ${s.replay}` : null,
  ]);
}

/** Declared params that differ from their default: `retriever_k=2`. */
function changedParamsText(c = {}) {
  const schema = c.params?.schema ?? [];
  const values = c.params?.values ?? {};
  const out = schema
    .filter(
      (p) =>
        p?.name &&
        values[p.name] != null &&
        p.default !== undefined &&
        String(values[p.name]) !== String(p.default),
    )
    .map((p) => `${p.name}=${values[p.name]}`);
  return out.length ? out.join(", ") : null;
}

/** Prompt model params: `temp 0.9 · max 150 tokens` (only what is set). */
function promptParamsText(c = {}) {
  const m = c.model?.params ?? {};
  return join([
    m.temperature != null ? `temp ${m.temperature}` : null,
    m.max_tokens != null ? `max ${m.max_tokens} tokens` : null,
    m.top_p != null ? `top_p ${m.top_p}` : null,
  ]);
}

/** Tool modes of a prompt run: `tools mock` / `2 recorded, 1 live`. */
function toolModesText(c = {}) {
  const counts = {};
  (c.tools ?? []).forEach((t) => {
    if (t?.mode) counts[t.mode] = (counts[t.mode] ?? 0) + 1;
  });
  const parts = Object.entries(counts);
  if (!parts.length) return null;
  if (parts.length === 1) return `tools ${parts[0][0]}`;
  return parts.map(([m, n]) => `${n} ${m}`).join(", ");
}

/**
 * Everything a run used.
 * @param {object} run  canonical Run with `config`
 */
export function provenanceOf(run) {
  const c = run?.config ?? {};
  const kind = runKindOf(run);
  const entry = entryOfConfig({ ...c, kind });
  const tools = toolsOf(c);
  const harness =
    kind === "harness"
      ? {
          id: c.harness?.id ?? entry?.id ?? null,
          name: c.harness?.name ?? entry?.name ?? null,
          version: c.harness?.version ?? null,
          permissions: c.harness?.permissions ?? null,
          permissionsLabel: PERMISSION_LABELS[c.harness?.permissions] ?? null,
          builtin_tools: c.harness?.builtin_tools ?? null,
          budget: c.harness?.budget ?? null,
          artifacts: c.harness?.artifacts ?? [],
          subagents: c.harness?.subagents ?? [],
        }
      : null;
  const capture =
    kind === "harness"
      ? c.harness?.capture ?? {
          ...(entry?.capture ?? { model_calls: "gateway", steps: null }),
          auth: "api_key",
        }
      : null;
  const v = c.voice;
  const warnings = [
    ...tools
      .filter((t) => t.declared && !t.called)
      .map((t) => ({
        kind: "declared_unused",
        tool: t.name,
        text: `${t.name} is declared but this run never called it`,
      })),
    ...tools
      .filter((t) => t.called && !t.declared)
      .map((t) => ({
        kind: "undeclared_used",
        tool: t.name,
        text: `${t.name} was called but the agent never declared it`,
      })),
  ];
  return {
    kind,
    source: {
      kind,
      entryId: entry?.id ?? null,
      name: sourceName(kind, entry, c),
      badge: entry?.badge ?? null,
      status: entry?.status ?? "today",
    },
    target:
      kind === "agent"
        ? {
            name: c.agent?.name ?? run?.target?.graph_name ?? null,
            version: c.agent?.version ?? run?.target?.version_name ?? null,
            draft: (c.agent?.status ?? run?.target?.status) === "draft",
            nodes: c.agent?.nodes?.length ?? null,
          }
        : kind === "prompt"
          ? {
              name: c.prompt?.name ?? run?.target?.prompt_name ?? null,
              version:
                c.prompt?.version ?? run?.target?.prompt_version_name ?? null,
              draft: false,
              nodes: null,
            }
          : null,
    models: modelsOf(c, kind, run),
    tools,
    mcp: mcpOf(c),
    prompts: c.prompts ?? [],
    params: c.params ?? null,
    where: whereOf(c, kind, entry),
    owner: c.execution?.owner ?? null,
    code:
      (kind === "code" || kind === "upload") && c.code
        ? {
            repo: c.code.repo ?? null,
            branch: c.code.branch ?? null,
            commit: c.code.commit ?? null,
            sha: shortSha(c.code.commit),
            dirty: c.code.dirty ?? null,
            entry: c.code.entry ?? null,
            runtime: c.code.runtime ?? null,
            sdk: c.code.sdk ?? null,
            diff_snapshot: c.code.diff_snapshot ?? null,
          }
        : null,
    voice: v
      ? {
          provider: v.provider,
          providerName: entry?.name ?? providerName(v.provider),
          depth: v.depth ?? null,
          stt: v.stt ?? null,
          llm: v.llm ?? null,
          tts: v.tts ?? null,
          turn_detection: v.turn_detection ?? null,
          agent_definition_id: v.agent_definition_id ?? null,
          agent_version: v.agent_version ?? null,
        }
      : null,
    harness,
    capture,
    endpoint:
      kind === "http"
        ? {
            protocol: endpointProtocol(c),
            url: c.endpoint?.url ?? c.http?.url ?? run?.target?.url ?? null,
            host: hostOf(c.endpoint?.url ?? c.http?.url ?? run?.target?.url),
            // L7c g2 (knob:http:method): an HTTP run's method (null for
            // A2A / MCP, whose method the protocol fixes).
            method:
              c.kind === "a2a" || c.kind === "mcp_tool"
                ? null
                : String(
                    c.endpoint?.method ??
                      c.http?.method ??
                      run?.target?.method ??
                      "",
                  ).toUpperCase() || null,
            agent:
              [c.a2a?.agent ?? c.a2a?.card?.name, c.a2a?.version]
                .filter(Boolean)
                .join(" ") || null,
            skill: c.a2a?.skill_id ?? run?.target?.skill ?? null,
            server: c.mcp_tool?.server?.name ?? run?.target?.server ?? null,
            tool: c.mcp_tool?.tool ?? run?.target?.tool ?? null,
          }
        : null,
    safety: {
      side_effects: c.safety?.side_effects ?? null,
      tool_stubs: c.safety?.tool_stubs ?? [],
      sideEffectTools: tools.filter((t) => t.side_effect).map((t) => t.name),
    },
    secrets: c.secrets ?? null,
    // options-truth L1 C7: settings, helper model, version and the parts
    // the one line adds per kind.
    settings: settingsOf(c),
    settingsText: settingsText(c),
    helperModel: c.harness?.subcall_model ?? null,
    // Created runs (the run engine stamps `config.engine`) name the harness
    // version; seeded runs keep their D7 lines.
    harnessVersion: c.engine ? c.harness?.version ?? null : null,
    harnessCommand: c.harness?.command ?? null,
    changedParams: changedParamsText(c),
    promptParams: kind === "prompt" ? promptParamsText(c) : null,
    toolModes: kind === "prompt" ? toolModesText(c) : null,
    maxTurns:
      kind === "prompt" && c.tools?.length ? c.max_tool_turns ?? null : null,
    platform: c.execution?.mode === "platform",
    // TTS prompt runs (VX #2): the voice the opening line is spoken in.
    tts: c.voice_tts ?? null,
    warnings,
  };
}

const modelsText = (p, { roles } = {}) => {
  const ids = uniq(
    p.models
      .filter((m) =>
        roles
          ? roles.includes(m.role)
          : !["stt", "tts", "caller"].includes(m.role),
      )
      .map((m) => m.id),
  );
  if (!ids.length) return null;
  // A code run whose model differs from what the agent declared.
  const seen =
    p.kind === "code" &&
    p.models.find((m) => m.id === ids[0])?.source === "seen"
      ? " (seen in this run)"
      : "";
  return ids.length > 1
    ? `${ids[0]}${seen} +${ids.length - 1}`
    : `${ids[0]}${seen}`;
};
const toolsText = (p) => {
  const n = p.tools.length;
  if (!n) return null;
  return `${n} ${n === 1 ? "tool" : "tools"}`;
};
const mcpText = (p, { counts } = {}) => {
  if (!p.mcp.length) return null;
  const [first, ...rest] = p.mcp;
  const name =
    counts && first.tools != null
      ? `${first.name} (${first.tools})`
      : first.name;
  return `MCP ${name}${rest.length ? ` +${rest.length}` : ""}`;
};
const versionText = (t) => {
  const text = [t?.name, t?.version].filter(Boolean).join(" ");
  return text ? `${text}${t.draft ? " (draft)" : ""}` : null;
};

/**
 * The one-line provenance (D7 strings), e.g.
 * `LangGraph · gpt-4.1-mini +1 · 7 tools · MCP acme-support · alice@mbp-14 · 9f2c1a7`
 * `Claude Code · claude-sonnet-4-5 +1 · MCP acme-support (12) · Edit files · Future AGI sandbox`
 * `WebSocket · gpt-4o-mini · nova-2 · ElevenLabs Rachel · Your machine`
 * `Prompt · Support Ops Reply V1 · gpt-4o-mini · 6 tools`
 * `Agent graph · Support Agent v3 · 6 nodes · gpt-4o-mini +1`
 * `Endpoint · HTTP · api.acme.ai`
 * @param {ReturnType<typeof provenanceOf>} p
 */
export function provenanceLine(p) {
  if (!p) return "";
  switch (p.kind) {
    case "prompt":
      return join([
        "Prompt",
        versionText(p.target),
        modelsText(p, { roles: ["main", "tts"] }),
        p.tts
          ? [providerName(p.tts.provider), p.tts.voice]
              .filter(Boolean)
              .join(" ")
          : null,
        p.platform ? p.promptParams : null,
        toolsText(p),
        p.platform ? p.toolModes : null,
        p.platform && p.maxTurns ? `max ${p.maxTurns} turns` : null,
        p.platform ? "Runs in Future AGI" : null,
        p.platform ? p.settingsText : null,
      ]);
    case "agent":
      return join([
        "Agent graph",
        versionText(p.target),
        p.target?.nodes ? `${p.target.nodes} nodes` : null,
        modelsText(p),
        p.platform ? "Runs in Future AGI" : null,
        p.platform ? p.settingsText : null,
      ]);
    case "harness":
      return join([
        [p.harness?.name ?? p.source.name, p.harnessVersion]
          .filter(Boolean)
          .join(" "),
        modelsText(p),
        p.helperModel ? `helper ${p.helperModel}` : null,
        mcpText(p, { counts: true }),
        p.harness?.permissionsLabel,
        p.where.text,
        p.harnessCommand ? `\`${p.harnessCommand}\`` : null,
        p.settingsText,
      ]);
    case "voice": {
      const v = p.voice ?? {};
      const textOnly = v.depth === "text_only";
      return join([
        v.providerName ?? p.source.name,
        v.llm?.model ?? modelsText(p),
        textOnly ? "Text only" : v.stt?.model,
        textOnly
          ? null
          : [providerName(v.tts?.provider), v.tts?.voice]
              .filter(Boolean)
              .join(" ") || null,
        p.where.text,
      ]);
    }
    case "http": {
      // L7c g5 (identity:provenance-line): an endpoint run's line names the
      // tries and cost cap it ran with (`· 3 tries · 5 at a time · cap $2`),
      // the same settings every other kind's line shows.
      const e = p.endpoint ?? {};
      if (e.protocol === "a2a")
        return join([
          "A2A agent",
          e.agent,
          e.skill ? `skill ${e.skill}` : null,
          e.host,
          p.settingsText,
        ]);
      if (e.protocol === "mcp")
        return join(["MCP tool", e.server, e.tool, p.settingsText]);
      return join(["Endpoint", "HTTP", e.host, p.settingsText]);
    }
    case "traces":
      return join([p.source.name, p.where.text, p.code?.sha]);
    case "upload":
      return join([
        "Uploaded results",
        p.where.mode === "ci" ? "CI" : "SDK",
        p.where.text,
        p.code?.sha,
      ]);
    default:
      return join([
        p.source.name,
        modelsText(p),
        toolsText(p),
        mcpText(p),
        p.where.text,
        p.code?.sha,
        p.changedParams,
        p.settingsText,
      ]);
  }
}

/**
 * How much of a harness run was captured: `Full steps (harness transcript)`
 * (Claude Code / Codex CLI transcripts), `Final result only (no
 * transcript)`, or `Model calls only (gateway)`. Null for other kinds.
 */
export function captureLabel(p) {
  const cap = p?.capture;
  if (!cap) return null;
  if (cap.steps === "transcript") return CAPTURE_LABELS.transcript;
  if (cap.steps === "final_only") return CAPTURE_LABELS.final_only;
  if (cap.model_calls === "gateway") return CAPTURE_LABELS.gateway;
  return null;
}

// ------------------------------------------------------------ versions

const numParts = (v) =>
  String(v ?? "")
    .replace(/^[^\d]*/, "")
    .split(/[^\d]+/)
    .filter(Boolean)
    .map(Number);
const timeOf = (run) => {
  const t = Date.parse(run?.created_at ?? "");
  return Number.isFinite(t) ? t : 0;
};
const entryFile = (entry) => String(entry ?? "").split(":")[0] || null;

/**
 * The agent a run tests and its version (D26): agent graph id + graph
 * version; prompt template + prompt version; code framework + entry file +
 * commit; harness + harness version; voice definition + definition version.
 * `order` sorts versions (numeric parts; code and harness by run time).
 * Upload / traces / endpoint runs have none (null).
 * @returns {{agentKey, name, version, order: number[], draft, text}|null}
 */
export function agentVersionOf(run) {
  const c = run?.config ?? {};
  const kind = runKindOf(run);
  const draftOf = (s) => s === "draft";
  if (kind === "agent") {
    const version =
      c.agent?.version ??
      run?.target?.version_name ??
      (run?.profile?.agentVersion != null
        ? `v${run.profile.agentVersion}`
        : null);
    if (!version) return null;
    const draft = draftOf(c.agent?.status ?? run?.target?.status);
    const key = c.agent?.graph_id ?? run?.target?.graph_id ?? c.agent?.name;
    return {
      agentKey: `agent:${key}`,
      name: c.agent?.name ?? run?.target?.graph_name ?? "Agent",
      version,
      order: numParts(version),
      draft,
      text: `${version}${draft ? " (draft)" : ""}`,
    };
  }
  if (kind === "prompt") {
    const version = c.prompt?.version ?? run?.target?.prompt_version_name;
    const key = c.prompt?.id ?? run?.target?.prompt_id ?? c.prompt?.name;
    if (!version || !key) return null;
    return {
      agentKey: `prompt:${key}`,
      name: c.prompt?.name ?? run?.target?.prompt_name ?? "Prompt",
      version,
      order: numParts(version),
      draft: false,
      text: version,
    };
  }
  if (kind === "code") {
    const fw = c.framework?.id ?? entryOfConfig({ ...c, kind })?.id ?? "code";
    const file = entryFile(c.code?.entry ?? run?.target?.entry);
    const sha = shortSha(c.code?.commit ?? run?.target?.commit);
    if (!sha) return null;
    return {
      agentKey: `code:${fw}|${file ?? ""}`,
      name: run?.name ?? catalogEntry(fw)?.name ?? "Your code",
      version: sha,
      order: [timeOf(run)],
      draft: Boolean(c.code?.dirty?.files?.length),
      text: sha,
    };
  }
  if (kind === "harness") {
    const id =
      c.harness?.id ?? entryOfConfig({ ...c, kind })?.id ?? c.harness?.name;
    const version = c.harness?.version ?? null;
    if (!id || !version) return null;
    return {
      agentKey: `harness:${id}`,
      name: c.harness?.name ?? catalogEntry(id)?.name ?? "Harness",
      version,
      order: [timeOf(run)],
      draft: false,
      text: version,
    };
  }
  if (kind === "voice") {
    const def = c.voice?.agent_definition_id;
    const version = c.voice?.agent_version;
    if (!def || !version) return null;
    return {
      agentKey: `voice:${def}`,
      name: String(run?.name ?? "Voice agent").split(SEP)[0],
      version,
      order: numParts(version),
      draft: false,
      text: version,
    };
  }
  return null;
}

const cmpOrder = (a, b) => {
  const n = Math.max(a.length, b.length);
  for (let i = 0; i < n; i += 1) {
    const d = (a[i] ?? -1) - (b[i] ?? -1);
    if (d) return d;
  }
  return 0;
};
const laterRun = (a, b) =>
  timeOf(a) - timeOf(b) ||
  String(a?.label ?? "").localeCompare(String(b?.label ?? ""));

/**
 * The default comparison (D26): the newest version of the agent with the
 * most runs vs the version before it (drafts included and labelled). Ties on
 * run count prefer an agent with two or more versions, then the most recent
 * activity. Each side is the latest run of its version. Hidden runs
 * (`listed: false`) are skipped. Null when the agent has one version only.
 * @param {object[]} runs
 * @returns {{latest: object, previous: object, agentKey: string,
 *   latestVersion: object, previousVersion: object}|null}
 */
export function latestVsPrevious(runs) {
  const groups = new Map();
  (runs ?? [])
    .filter((r) => r && r.listed !== false)
    .forEach((run) => {
      const v = agentVersionOf(run);
      if (!v) return;
      if (!groups.has(v.agentKey)) groups.set(v.agentKey, []);
      groups.get(v.agentKey).push({ run, v });
    });
  const summaries = [...groups.entries()].map(([key, list]) => {
    const byVersion = new Map();
    list.forEach((x) => {
      const cur = byVersion.get(x.v.version);
      if (!cur || laterRun(x.run, cur.run) > 0) byVersion.set(x.v.version, x);
    });
    const versions = [...byVersion.values()].sort(
      (a, b) => cmpOrder(a.v.order, b.v.order) || laterRun(a.run, b.run),
    );
    const last = Math.max(...list.map((x) => timeOf(x.run)));
    return { key, count: list.length, versions, last };
  });
  summaries.sort(
    (a, b) =>
      b.count - a.count ||
      Number(b.versions.length >= 2) - Number(a.versions.length >= 2) ||
      b.last - a.last,
  );
  const top = summaries[0];
  if (!top || top.versions.length < 2) return null;
  const latest = top.versions[top.versions.length - 1];
  const previous = top.versions[top.versions.length - 2];
  return {
    latest: latest.run,
    previous: previous.run,
    agentKey: top.key,
    latestVersion: latest.v,
    previousVersion: previous.v,
  };
}

/**
 * options-truth L7b G4 (connect:pill): the machine a local run ran on, as
 * its config recorded it: `Ran on voice-worker@acme-gpu-01 (Ravi Menon)`,
 * or null for a run that did not run on a machine. Pure.
 * @param {object|null} prov  provenanceOf(run)
 */
export function recordedMachineText(prov) {
  const w = prov?.where;
  if (w?.mode !== "local" || !w.label) return null;
  const owner = prov?.owner?.name;
  return `Ran on ${w.label}${owner ? ` (${owner})` : ""}`;
}
