import { formatExactMoney } from "src/sections/common/format/money";
// options-truth L7b G2 (RC2): what makes a run the run it is, read the same
// way from a create / edit body entry and from a run record (target +
// config). Edit and Duplicate (experimentFormValues.js) and the mock's edit
// path (experimentsCreate.js) both ask here whether a kept card changed and
// what changed, so the confirm, the toast and the server agree. Pure.
//
// Identity: kind; prompt (id, version, model, params, tools name + mode,
// max_tool_turns, on_unrecorded); agent (graph, version); http (url,
// method, body, output path, headers, transform); a2a (card URL, skill,
// part columns, auth); mcp_tool (server, transport, env, tool, arguments,
// side effects); limits (tries, cost cap); variable_mapping.
//
// An entry leaves a field `undefined` when it does not carry it (a kept
// card whose tools were never loaded); `filledFrom(after, before)` keeps the
// run's value for those, so leaving a field out never reads as a change.

import { autoMapVariables } from "src/sections/develop-detail/Experiment/variableMapping";
import { requestModelParams } from "src/sections/develop-detail/Experiment/modelParamNames";
import { mcpEnvSecrets } from "src/sections/common/RunSources/endpoint/requestModel";

/** Kinds this module models; other kinds compare by their own targets. */
export const IDENTITY_KINDS = Object.freeze([
  "prompt",
  "agent",
  "http",
  "a2a",
  "mcp_tool",
]);

const KIND_NAMES = {
  prompt: "prompt",
  agent: "agent graph",
  http: "HTTP endpoint",
  a2a: "A2A",
  mcp_tool: "MCP tool",
};

/** `an A2A run`, `an HTTP endpoint run` (for refusals). */
export const kindWords = (kind) => {
  const n = KIND_NAMES[kind] ?? String(kind ?? "other");
  return `${/^[aeiouAEIOU]|^HTTP|^MCP/.test(n) ? "an" : "a"} ${n} run`;
};

const blank = (v) => v == null || v === "";
const str = (v) => (blank(v) ? null : String(v).trim() || null);
const num = (v) => {
  const n = Number(v);
  return blank(v) || !Number.isFinite(n) || n <= 0 ? null : n;
};

/** Plain JSON with sorted keys and no empty values (stable compare). */
export function stable(v) {
  if (Array.isArray(v)) return v.map(stable);
  if (v && typeof v === "object") {
    const out = {};
    Object.keys(v)
      .sort()
      .forEach((k) => {
        const x = v[k];
        if (x === undefined || x === null || x === "") return;
        out[k] = stable(x);
      });
    return out;
  }
  return v;
}

const json = (v) => JSON.stringify(stable(v ?? null));
const emptyObj = (v) =>
  !v || (typeof v === "object" && !Object.keys(stable(v)).length);

// Model params as values only (the picker's providers / logoUrl are not
// part of what ran).
function paramsOf(p) {
  if (!p || typeof p !== "object") return {};
  const { providers: _p, logoUrl: _l, ...rest } = p;
  return stable(requestModelParams(rest));
}

const isRecord = (x) =>
  Boolean(x?.config) ||
  (Boolean(x?.target) && (x?.letter != null || x?.label != null));

/** The kind of an entry or run record (a2a / mcp_tool ride as http). */
export function identityKindOf(x = {}) {
  const src = x?.target?.source;
  if (src === "a2a" || src === "mcp_tool") return src;
  const cfgKind = x?.config?.kind;
  if (cfgKind === "a2a" || cfgKind === "mcp_tool") return cfgKind;
  const t = x?.target_type ?? x?.type ?? null;
  if (t === "a2a" || t === "mcp_tool") return t;
  if (cfgKind === "code" || cfgKind === "harness" || cfgKind === "voice")
    return cfgKind;
  if (x?.r4) return x.r4?.config?.kind ?? "code";
  if (t === "prompt" || t === "agent" || t === "http") return t;
  if (cfgKind) return cfgKind;
  if (x?.agent_id) return "agent";
  if (x?.prompt_id || x?.prompt_version) return "prompt";
  return t;
}

const toolsOf = (list) =>
  Array.isArray(list)
    ? list
        .map((t) => ({
          name: str(t?.name ?? t),
          mode: str(t?.mode) ?? "mock",
        }))
        .filter((t) => t.name)
        .sort((a, b) => a.name.localeCompare(b.name))
    : undefined;

const headersOf = (list) =>
  Array.isArray(list)
    ? list
        .filter((h) => str(h?.name))
        .map((h) => stable({ ...h, name: str(h.name) }))
        .sort((a, b) => a.name.localeCompare(b.name))
    : undefined;

function promptIdentity(x, record) {
  const t = x.target ?? {};
  const cfg = x.config ?? {};
  const tools = record ? toolsOf(cfg.tools ?? []) : toolsOf(x.tools);
  const has = (tools ?? []).length > 0;
  const turns = record ? cfg.max_tool_turns : x.max_tool_turns;
  const onUnrecorded = record ? cfg.on_unrecorded : x.on_unrecorded;
  const model =
    t.model ?? (typeof x.model === "string" ? x.model : x.model?.name);
  return {
    id: str(t.prompt_id ?? x.prompt_id),
    version: str(t.prompt_version_id ?? x.prompt_version),
    model: str(model),
    params: paramsOf(t.model_params ?? x.model_params),
    tools,
    // Tool turns and unrecorded calls only mean something with tools.
    max_tool_turns: tools === undefined ? undefined : has ? num(turns) : null,
    on_unrecorded:
      tools === undefined
        ? undefined
        : has
          ? str(onUnrecorded) ?? "mock"
          : null,
  };
}

function agentIdentity(x) {
  const t = x.target ?? {};
  return {
    graph: str(t.graph_id ?? x.agent_id),
    version: str(t.version_id ?? x.agent_version),
  };
}

function httpIdentity(x, record) {
  const cfg = x.config ?? {};
  const e = record
    ? cfg.endpoint ?? cfg.http ?? x.http ?? x.target ?? {}
    : x.http ?? {};
  const transform = record
    ? e.transform ?? null
    : x.endpoint?.transform ?? e.transform ?? null;
  return {
    url: str(e.url),
    method: (str(e.method) ?? "POST").toUpperCase(),
    body_template: str(e.body_template ?? e.bodyTemplate) ?? "",
    output_path: str(e.output_path ?? e.outputPath) ?? "",
    headers: record ? headersOf(e.headers ?? []) : headersOf(e.headers),
    transform: transform ? stable(transform) : null,
  };
}

function a2aIdentity(x, record) {
  const a = (record ? x.config?.a2a : x.a2a) ?? {};
  const auth = a.auth && typeof a.auth === "object" ? stable(a.auth) : null;
  return {
    card_url: str(a.card_url ?? a.fetched_from ?? a.card?.url),
    skill_id: str(a.skill_id),
    text_column: str(a.text_column),
    data_columns: [...(a.data_columns ?? [])].map(String).sort(),
    context_column: str(a.context_column),
    auth: auth && Object.keys(auth).length ? auth : null,
  };
}

function mcpIdentity(x, record) {
  const m = (record ? x.config?.mcp_tool : x.mcp_tool) ?? {};
  const s = m.server ?? {};
  const transport = str(s.transport) ?? "stdio";
  return {
    server: {
      transport,
      command: transport === "http" ? null : str(s.command),
      url: transport === "http" ? str(s.url) : null,
      // L7c g2 (knob:mcp:env-secret): the env rows `[{name, secret}]` (or
      // the older single env_secret) as `NAME ← secret SECRET`, so Edit's
      // What changed names the env secret (String() of the rows read
      // `[object Object]`).
      // L7c merge: the older single env_secret keeps its own word (`env
      // secret ACME_KEY → OTHER`), never `ACME_KEY ← secret ACME_KEY`.
      env: Array.isArray(s.env)
        ? mcpEnvSecrets(s)
            .filter((x) => x.name && x.secret)
            .map((x) => `${x.name} ← secret ${x.secret}`)
            .join(", ") || null
        : str(s.env_secret),
    },
    tool: str(m.tool),
    arguments: stable(m.arguments ?? {}),
    side_effects:
      str(m.side_effects ?? x.config?.safety?.side_effects) ?? "stubs",
  };
}

/**
 * The identity of a body entry or a run record (pure).
 * @param {object} x  a prompt_config entry, or a run (target + config)
 * @param {{trials?: number}} [ctx]  the experiment's Trials: a run without
 *   its own tries plays these, so `tries` is what the run plays
 */
export function runIdentityOf(x, { trials } = {}) {
  if (!x || typeof x !== "object") return null;
  const record = isRecord(x);
  const kind = identityKindOf(x);
  const cfg = x.config ?? {};
  const lim = (record ? cfg.limits : x.limits) ?? {};
  const fallbackTries = num(trials) ?? (record ? num(x.trials) : null) ?? 1;
  const ownTries = num(lim.tries);
  const mapping = record ? cfg.variable_mapping : x.variable_mapping;
  const out = {
    kind,
    limits: {
      tries: ownTries ?? fallbackTries,
      max_cost_usd: num(lim.max_cost_usd ?? lim.maxCostUsd),
    },
    variable_mapping:
      mapping === undefined && !record
        ? undefined
        : emptyObj(mapping)
          ? null
          : stable(mapping),
  };
  if (kind === "prompt") out.prompt = promptIdentity(x, record);
  else if (kind === "agent") out.agent = agentIdentity(x);
  else if (kind === "http") out.http = httpIdentity(x, record);
  else if (kind === "a2a") out.a2a = a2aIdentity(x, record);
  else if (kind === "mcp_tool") out.mcp_tool = mcpIdentity(x, record);
  return out;
}

/**
 * `after` with every field it leaves undefined taken from `before` (a kept
 * card that does not carry a field keeps what its run had). Pure.
 */
export function filledFrom(after, before) {
  if (after === undefined) return before;
  if (!after || !before || typeof after !== "object" || Array.isArray(after))
    return after;
  const out = { ...after };
  Object.keys(before).forEach((k) => {
    if (out[k] === undefined) out[k] = before[k];
    // An explicit variable map replaces the saved map. Recursively filling it
    // resurrects removed bindings, including same-name bindings normalized away
    // by forCompare, and makes the change summary say only "setup changed".
    else if (
      k !== "variable_mapping" &&
      out[k] &&
      typeof out[k] === "object" &&
      !Array.isArray(out[k]) &&
      before[k] &&
      typeof before[k] === "object"
    )
      out[k] = filledFrom(out[k], before[k]);
  });
  return out;
}

/**
 * A mapping as what it changes: variables left unmapped (`""`) and
 * variables mapped to the column of their own name (what the form maps by
 * itself, variableMapping.autoMapVariables) read the same as no mapping, so
 * a run that recorded none and its card's by-name mapping are the same run.
 * @param {object|null} mapping
 * @param {object[]} [columns]  the dataset's columns ({id, name})
 */
export function mappingAsChanged(mapping, columns = []) {
  if (!mapping || typeof mapping !== "object") return null;
  const keys = Object.keys(mapping);
  const byName = columns?.length ? autoMapVariables(keys, columns, {}) : {};
  const out = {};
  keys.forEach((k) => {
    const v = mapping[k];
    if (v === "" || v == null) return;
    if (byName[k] && json(byName[k]) === json(v)) return;
    out[k] = v;
  });
  return Object.keys(out).length ? stable(out) : null;
}

/**
 * The two identities an edit compares: `after` filled from `before`, both
 * mappings read as what they change. Returns `[before, after]`. Pure.
 */
export function forCompare(before, after, { columns = [] } = {}) {
  if (!before || !after) return [before, after];
  const a = filledFrom(after, before);
  return [
    {
      ...before,
      variable_mapping: mappingAsChanged(before.variable_mapping, columns),
    },
    { ...a, variable_mapping: mappingAsChanged(a.variable_mapping, columns) },
  ];
}

/** Whether two identities are the same run (pure). */
export const sameRunIdentity = (a, b) => json(a) === json(b);

const shownValue = (v) =>
  v == null ||
  v === "" ||
  (typeof v === "object" && !Object.keys(stable(v)).length)
    ? "–"
    : typeof v === "object"
      ? JSON.stringify(stable(v))
      : String(v);

const money = (v) => formatExactMoney(v, { missing: "–" });

const mappingText = (m) =>
  m
    ? Object.entries(m)
        .map(([k, v]) =>
          v?.kind === "constant"
            ? `${k} = "${v.value ?? ""}"`
            : v?.kind === "empty"
              ? `${k} empty`
              : `${k} ← ${typeof v === "object" ? v?.column ?? JSON.stringify(v) : v}`,
        )
        .join(", ")
    : "–";

/**
 * What changed between a run and its edited card, in words
 * (`output path $.reply → $.output.text`, `tries 2 → 4`,
 * `cost cap – → $0.50`, `+ header X-Tier`). `skip` leaves out fields the
 * caller words itself (prompt version / model / params). Pure.
 */
export function runIdentityChanges(before, after, { skip = [] } = {}) {
  const out = [];
  if (!before || !after) return out;
  const b = before;
  const a = filledFrom(after, before);
  const diff = (label, x, y, fmt = shownValue) => {
    if (json(x) !== json(y)) out.push(`${label} ${fmt(x)} → ${fmt(y)}`);
  };
  if (b.kind !== a.kind) {
    out.push(
      `${KIND_NAMES[b.kind] ?? b.kind} → ${KIND_NAMES[a.kind] ?? a.kind}`,
    );
    return out;
  }
  if (a.prompt && b.prompt) {
    if (!skip.includes("prompt")) {
      diff("version", b.prompt.version, a.prompt.version);
      diff("model", b.prompt.model, a.prompt.model);
      const keys = [
        ...new Set([
          ...Object.keys(b.prompt.params ?? {}),
          ...Object.keys(a.prompt.params ?? {}),
        ]),
      ].filter(
        (k) => json(b.prompt.params?.[k]) !== json(a.prompt.params?.[k]),
      );
      if (keys.length)
        out.push(
          `params ${keys.map((k) => `${k} ${shownValue(b.prompt.params?.[k])} → ${shownValue(a.prompt.params?.[k])}`).join(", ")}`,
        );
    }
    const names = (l) => (l ?? []).map((t) => t.name);
    const added = names(a.prompt.tools).filter(
      (n) => !names(b.prompt.tools).includes(n),
    );
    const gone = names(b.prompt.tools).filter(
      (n) => !names(a.prompt.tools).includes(n),
    );
    added.forEach((n) => out.push(`+ tool ${n}`));
    gone.forEach((n) => out.push(`− tool ${n}`));
    (a.prompt.tools ?? []).forEach((t) => {
      const was = (b.prompt.tools ?? []).find((x) => x.name === t.name);
      if (was && was.mode !== t.mode)
        out.push(`${t.name} ${was.mode} → ${t.mode}`);
    });
    diff("max tool turns", b.prompt.max_tool_turns, a.prompt.max_tool_turns);
    diff("unrecorded calls", b.prompt.on_unrecorded, a.prompt.on_unrecorded);
  }
  if (a.agent && b.agent && !skip.includes("agent"))
    diff("version", b.agent.version, a.agent.version);
  if (a.http && b.http) {
    diff("URL", b.http.url, a.http.url);
    diff("method", b.http.method, a.http.method);
    if (b.http.body_template !== a.http.body_template) out.push("body changed");
    diff("output path", b.http.output_path, a.http.output_path);
    const hn = (l) => (l ?? []).map((h) => h.name);
    hn(a.http.headers)
      .filter((n) => !hn(b.http.headers).includes(n))
      .forEach((n) => out.push(`+ header ${n}`));
    hn(b.http.headers)
      .filter((n) => !hn(a.http.headers).includes(n))
      .forEach((n) => out.push(`− header ${n}`));
    (a.http.headers ?? []).forEach((h) => {
      const was = (b.http.headers ?? []).find((x) => x.name === h.name);
      if (was && json(was) !== json(h)) out.push(`header ${h.name} changed`);
    });
    if (json(b.http.transform) !== json(a.http.transform))
      out.push("response transform changed");
  }
  if (a.a2a && b.a2a) {
    diff("agent card", b.a2a.card_url, a.a2a.card_url);
    diff("skill", b.a2a.skill_id, a.a2a.skill_id);
    diff("text part", b.a2a.text_column, a.a2a.text_column);
    diff("data parts", b.a2a.data_columns, a.a2a.data_columns, (v) =>
      v?.length ? v.join(", ") : "–",
    );
    diff("context id", b.a2a.context_column, a.a2a.context_column);
    if (json(b.a2a.auth) !== json(a.a2a.auth)) out.push("auth changed");
  }
  if (a.mcp_tool && b.mcp_tool) {
    const sb = b.mcp_tool.server ?? {};
    const sa = a.mcp_tool.server ?? {};
    diff("transport", sb.transport, sa.transport);
    diff("server", sb.command ?? sb.url, sa.command ?? sa.url);
    diff("env secret", sb.env, sa.env);
    diff("tool", b.mcp_tool.tool, a.mcp_tool.tool);
    if (json(b.mcp_tool.arguments) !== json(a.mcp_tool.arguments))
      out.push("arguments changed");
    diff("side effects", b.mcp_tool.side_effects, a.mcp_tool.side_effects);
  }
  diff("tries", b.limits?.tries, a.limits?.tries);
  diff("cost cap", b.limits?.max_cost_usd, a.limits?.max_cost_usd, money);
  if (json(b.variable_mapping) !== json(a.variable_mapping))
    out.push(
      `mapping ${mappingText(b.variable_mapping)} → ${mappingText(a.variable_mapping)}`,
    );
  return out;
}

/**
 * The edit refusal when an entry would turn a run into another kind
 * (`#9B is an A2A run; edit it as an A2A card`), else null. Pure.
 */
export function kindChangeError(label, before, after) {
  if (!before || !after) return null;
  if (
    !IDENTITY_KINDS.includes(before.kind) ||
    !IDENTITY_KINDS.includes(after.kind)
  )
    return null;
  if (before.kind === after.kind) return null;
  const n = KIND_NAMES[before.kind];
  return `${label} is ${kindWords(before.kind)}; edit it as ${/^[aeiouAEIOU]|^HTTP|^MCP/.test(n) ? "an" : "a"} ${n} card`;
}
