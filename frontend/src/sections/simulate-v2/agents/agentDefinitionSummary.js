/**
 * What we read about an agent version, as rows: where it starts, its
 * prompts, tools, model and how it runs. Each row points at the file it was
 * read from, so the summary and the file viewer below it agree.
 *
 * A part the source can't show stays as a row with `hidden` and the reason
 * — the reader should see the gap, not a missing line.
 */

import { CONFIG_AREAS } from "../_mock/rsiModel";
import { PROVIDER_LABELS } from "../_mock/agentRegistry";
import { configAreasFor } from "./configAreas";
import { reachOf, retellKindOf } from "./agentProfile";

const linesOf = (text) => String(text || "").split("\n").length;
const fileName = (path) => path.split("/").pop();

const entryPointOf = ({ definition, files, source }) => {
  const config = files.find((f) => f.group === "Config")?.path;
  const prompt = files.find((f) => f.group === "Prompts")?.path;
  switch (definition.kind) {
    case "repo":
    case "upload":
      return { value: "src/agent.py:agent", mono: true, path: files.some((f) => f.path === "src/agent.py") ? "src/agent.py" : config };
    case "framework":
      return { value: definition.entry || "Not recorded", mono: true, path: definition.entry ? definition.entry.split(":")[0] : config, note: `Wrapped by the ${source.label} adapter` };
    case "platform":
      return { value: source.where ? `${source.label} assistant · ${source.where}` : `${source.label} assistant`, path: config };
    case "prompt":
      return { value: "Its prompt, run in Future AGI", path: prompt };
    case "mcp":
      return { value: source.runtime.where || "MCP server", mono: !!source.runtime.where, path: config };
    default:
      return source.runtime.where
        ? { value: source.runtime.where, mono: true, path: config, note: "A black box — we see what it does, not how it's built" }
        : { value: "Not recorded", hidden: true };
  }
};

export const definitionSummaryOf = ({ agent, definition, files, source }) => {
  const prompts = files.filter((f) => f.group === "Prompts");
  const tools = files.filter((f) => f.group === "Tools");
  const knowledge = files.filter((f) => f.group === "Knowledge");
  const { model, voice } = definition;
  const rt = source.runtime;
  /* A framework agent's shape — its graph, agents and handoffs, or crew. */
  const st = definition.structure;
  const retell = retellKindOf(agent);
  return [
    { id: "entry", label: "Entry point", ...entryPointOf({ definition, files, source }) },
    /* Retell agents come in two kinds, and an improvement changes them differently. */
    ...(retell ? [{ id: "retellKind", label: "Retell agent", value: retell.label, note: `${retell.note}${retell.recorded ? "" : " Not recorded at connect — read as single prompt."}` }] : []),
    ...(st ? [{ id: "structure", label: st.label, value: st.summary, mono: true, path: definition.entry?.split(":")[0], note: st.items.map((x) => x.name).join(" · ") }] : []),
    /* Several agents working as one — a squad, a flow, handoffs in code. */
    ...(definition.members?.length ? [{
      id: "members",
      label: "Members",
      value: `${source.label === "Vapi" ? "Vapi squad" : "Works as a team"} · ${definition.members.length} members`,
      note: definition.members.map((m) => m.name).join(" · "),
    }] : []),
    definition.instructions.visible && prompts.length
      ? {
        id: "prompts",
        label: "Prompts",
        items: prompts.map((f) => ({ label: `${f.path} · ${linesOf(f.content)} lines`, path: f.path, mono: true })),
        note: definition.instructions.origin,
      }
      : { id: "prompts", label: "Prompts", value: definition.instructions.why || "Not visible", hidden: true },
    {
      id: "tools",
      label: `Tools · ${tools.length}`,
      items: tools.map((f) => ({ label: fileName(f.path).replace(/\.(json|py)$/, ""), path: f.path, mono: true })),
      empty: definition.tools.observed ? "No tool calls observed yet" : "No tools declared",
      note: definition.tools.observed ? "Seen in runs, not declared" : null,
    },
    model.visible
      ? {
        id: "model",
        label: "Model",
        value: [model.name, model.temperature != null && `temperature ${model.temperature}`, model.maxTokens && `max ${model.maxTokens} tokens`].filter(Boolean).join(" · "),
        path: files.find((f) => f.group === "Config")?.path,
      }
      : { id: "model", label: "Model", value: model.why, hidden: true },
    ...(voice
      ? [voice.visible
        ? { id: "voice", label: "Voice", value: [voice.voice, voice.tts, voice.stt].filter(Boolean).join(" · ") }
        : { id: "voice", label: "Voice", value: voice.why, hidden: true }]
      : []),
    definition.knowledge.visible
      ? {
        id: "knowledge",
        label: `Knowledge · ${knowledge.length}`,
        items: knowledge.map((f) => ({ label: fileName(f.path), path: f.path })),
        empty: "No knowledge attached",
      }
      : { id: "knowledge", label: "Knowledge", value: definition.knowledge.why, hidden: true },
    rt.where
      ? { id: "runs", label: "How it runs", value: `${rt.label} · ${rt.where}${rt.by ? ` · ${rt.by}` : ""}` }
      : { id: "runs", label: "How it runs", value: "Not set — a simulation needs somewhere to reach it", hidden: true, setUp: true },
    /* The configuration areas the rows above don't cover. */
    ...(agent ? MORE_AREAS.filter((id) => configAreasFor(agent).some((a) => a.id === id)).map((id) => {
      const a = areaValueOf({ agent, definition, source, id });
      return { id: `area-${id}`, label: AREA_LABEL[id], value: a.value, hidden: a.hidden, note: a.note };
    }) : []),
  ];
};

/* ── configuration areas, read for an agent or one of its members ───────── */

const AREA_LABEL = Object.fromEntries(CONFIG_AREAS.map((a) => [a.id, a.label]));
/* Not covered by a summary row of their own. */
const MORE_AREAS = ["opening", "turnTaking", "variables", "analysis", "account"];

/* What we can read for a source: hosted platforms and agents built here hold
   their settings; code shows what's in the repository; an endpoint shows nothing. */
const readsSettings = (kind) => ["platform", "builtin", "prompt", "traces"].includes(kind);
const readsCode = (kind) => ["repo", "upload", "framework"].includes(kind);

const VARIABLES = {
  voice: ["customer_name", "order_id", "callback_number"],
  chat: ["customer_name", "account_tier", "locale"],
};

/**
 * One configuration area's value: { value, hidden?, note? }. Mock values are
 * derived from the agent's type and identity, so the same agent always reads
 * the same; a member's own field (member.opening, member.variables…) wins.
 */
export const areaValueOf = ({ agent, definition, source, id, member }) => {
  const kind = source?.kind || definition?.kind;
  const voice = configAreasFor(agent).some((a) => a.id === "speechIn");
  const reach = reachOf(agent);
  /* Instructions, model and tools are read below; the rest a member may carry itself. */
  const own = ["instructions", "model", "tools", "structure"].includes(id) ? null : member?.[id];
  if (typeof own === "string" && own) return { value: own };
  if (Array.isArray(own) && own.length) return { value: own.join(", ") };
  if (kind === "endpoint" && !["instructions", "tools"].includes(id)) {
    return { value: "Not visible — an endpoint shows what the agent does, not how it's set up", hidden: true };
  }
  const name = member?.name || agent?.name || "the agent";
  const provider = PROVIDER_LABELS[agent?.values?.provider];
  switch (id) {
    case "instructions": {
      const text = member?.instructions || definition?.instructions?.text || "";
      const first = text.split(/(?<=\.)\s/)[0] || "";
      return text ? { value: first.length > 140 ? `${first.slice(0, 137)}…` : first } : { value: "None", hidden: true };
    }
    case "opening":
      if (member && member.index > 0) return { value: "Takes over mid-conversation — no greeting of its own" };
      return voice
        ? { value: `First message "Thanks for calling — how can I help?" · ${reach.whoSpeaksFirst === "agent" ? "agent" : "caller"} speaks first · ends on "Anything else?"`, ...(readsCode(kind) && { note: "Read from the prompt in code" }) }
        : { value: `Greets with "Hi — how can I help today?" · ${reach.whoWritesFirst === "agent" ? "agent" : "user"} writes first`, ...(readsCode(kind) && { note: "Read from the prompt in code" }) };
    case "model":
      return { value: member?.model || (definition?.model?.visible ? definition.model.name : "Not visible"), hidden: !member?.model && !definition?.model?.visible };
    case "speechIn":
      return definition?.voice?.visible ? { value: member?.stt || definition.voice.stt } : { value: definition?.voice?.why || "Not visible", hidden: true };
    case "speechOut":
      return definition?.voice?.visible ? { value: [member?.voice || definition.voice.voice, member?.tts || definition.voice.tts].filter(Boolean).join(" · ") } : { value: definition?.voice?.why || "Not visible", hidden: true };
    case "turnTaking":
      if (!readsSettings(kind)) return { value: "Set by the runtime — not in what we read", hidden: true };
      return { value: `End of turn after 700 ms of silence · interruptions ${definition?.voice?.interruptions ? definition.voice.interruptions.replace(/^allowed/i, "allowed").replace(/(\d)ms/, "$1 ms") : "allowed after 300 ms"} · calls end after 15 min` };
    case "tools": {
      const list = member?.tools || (definition?.tools?.items || []).map((t) => t.name);
      return list.length ? { value: list.join(", ") } : { value: "None" };
    }
    case "knowledge":
      if (!definition?.knowledge?.visible) return { value: definition?.knowledge?.why || "Not visible", hidden: true };
      return { value: (definition.knowledge.items || []).map((k) => k.name).join(", ") || "None attached" };
    case "structure":
      return member ? { value: member.role || `One of ${definition?.members?.length || 1} members` } : { value: definition?.structure?.summary || "Single agent" };
    case "variables": {
      const vars = VARIABLES[voice ? "voice" : "chat"];
      const n = member ? Math.max(1, vars.length - 1 - (member.index || 0)) : vars.length;
      return { value: `${vars.slice(0, n).map((x) => `{{${x}}}`).join(", ")} · defaults set for ${Math.max(1, n - 1)}`, ...(readsCode(kind) && { note: "Read from the code that fills them" }) };
    }
    case "analysis":
      return { value: voice ? "Call summary · success when resolved without a transfer · 3 structured fields" : "Conversation summary · success when the question is answered · 2 structured fields" };
    case "account":
      if (kind === "builtin") return { value: "This Future AGI workspace — nothing of yours to reach" };
      if (kind === "traces") return { value: "Not known yet — its traces don't say where it runs. Connect it there to test it live or sealed." };
      if (kind === "platform") return { value: `${provider || "Platform"} account · API key by reference${voice ? ` · up to ${reach.maxConcurrent} calls at once` : ""}` };
      return { value: "Your deployment's credentials — never read" };
    default:
      return { value: "—", hidden: true };
  }
};

/**
 * Every configuration area for each member of a squad or flow:
 *   [{ member, areas: [{ id, label, value, hidden }] }]
 * Analysis and Account are the agent's, shared by every member.
 */
export const memberAreasOf = ({ agent, definition, source }) => {
  const raw = (agent?.definition?.members || []);
  return (definition?.members || []).map((m, index) => {
    const member = { ...(raw.find((r) => r.id === m.id) || {}), ...m, index };
    return {
      member: m,
      areas: configAreasFor(agent)
        .filter((a) => !["analysis", "account"].includes(a.id))
        .map((a) => ({ id: a.id, label: a.label, ...areaValueOf({ agent, definition, source, id: a.id, member }) })),
    };
  });
};
