// Trajectory normalisation shared by every alignment (R3 G0 §B9).
//
// COPIES of `stepFromSpan`, `flattenTrace`, `stableJson`, `sameValue` and
// `normalizeSteps` from experiment-detail/results/drawer/alignSteps.js,
// behaviour identical (a parity test pins it). common/Align/milestones.js
// imports ONLY from here, never from alignSteps.js: implementation makes
// alignSteps.js delegate to milestones.js, and the reverse import would be
// circular. Pure: no React, no I/O.

const spanOf = (entry) => entry?.observation_span ?? entry ?? null;

const timeOf = (iso) => {
  const t = Date.parse(iso ?? "");
  return Number.isFinite(t) ? t : null;
};

const normType = (t) => {
  const v = String(t ?? "").toLowerCase();
  if (v === "retrieval" || v === "retriever") return "retriever";
  if (v === "generation") return "llm";
  return v || "unknown";
};

/** One step from a span. */
export function stepFromSpan(span, order = 0) {
  const start = timeOf(span.start_time);
  const end = timeOf(span.end_time);
  const ms =
    span.latency_ms ?? (start != null && end != null ? end - start : null);
  const isError = String(span.status ?? "").toUpperCase() === "ERROR";
  const errorOut = span.output?.error;
  return {
    id: span.id ?? `step-${order}`,
    order,
    type: normType(span.observation_type),
    name: span.name ?? "step",
    key: `${normType(span.observation_type)}:${span.name ?? "step"}`,
    start,
    ms: Number.isFinite(ms) ? ms : null,
    model: span.model ?? null,
    provider: span.provider ?? null,
    input: span.input ?? null,
    output: isError && errorOut ? null : span.output ?? null,
    tokens: span.total_tokens ?? null,
    promptTokens: span.prompt_tokens ?? null,
    completionTokens: span.completion_tokens ?? null,
    cost: span.cost ?? null,
    error: isError
      ? errorText(errorOut) || span.status_message || "Error"
      : null,
    loop: Boolean(span.span_attributes?.loop_iteration),
    // R2 (additive): the tool's mode (`mock` / `recorded` / `live`).
    mode: span.span_attributes?.["tool.mode"] ?? null,
  };
}

function errorText(err) {
  if (!err) return null;
  if (typeof err === "string") return err;
  const code = err.code != null ? `${err.code} ` : "";
  return `${code}${err.message ?? ""}`.trim() || null;
}

/**
 * Flatten a trace body (`{observation_spans: [{observation_span, children}]}`)
 * into steps ordered by start time. The top-level span(s) are the root and
 * are excluded; when a trace has only a root, the root itself is the one
 * step (a single LLM call).
 */
export function flattenTrace(trace) {
  const roots = trace?.observation_spans ?? [];
  const out = [];
  const walk = (entries) =>
    (entries ?? []).forEach((entry) => {
      const span = spanOf(entry);
      if (span) out.push(span);
      walk(entry?.children);
    });
  roots.forEach((root) => walk(root?.children));
  if (!out.length)
    roots
      .map(spanOf)
      .filter(Boolean)
      .forEach((s) => out.push(s));
  return out
    .map((span, i) => ({ span, i }))
    .sort((x, y) => {
      const a = timeOf(x.span.start_time);
      const b = timeOf(y.span.start_time);
      if (a == null || b == null || a === b) return x.i - y.i;
      return a - b;
    })
    .map(({ span }, order) => stepFromSpan(span, order));
}

// Stable JSON for equality (object keys sorted).
export function stableJson(value) {
  if (value === undefined) return "null";
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  return `{${Object.keys(value)
    .sort()
    .map((k) => `${JSON.stringify(k)}:${stableJson(value[k])}`)
    .join(",")}}`;
}

export const sameValue = (a, b) => stableJson(a) === stableJson(b);

const MCP_RE = /^mcp__[^_]+(?:_[^_]+)*?__(.+)$/;

const countWords = (v) => {
  const text = typeof v === "string" ? v : v == null ? "" : JSON.stringify(v);
  return text.trim() ? text.trim().split(/\s+/).length : 0;
};

/**
 * Steps with `nkind`, a display `name` and flags (`mcp`, `words`,
 * `changes`). Pass `{harness: true}` for harness runs (their tools are MCP
 * calls and their assistant turns collapse to `assistant · 212 words`).
 */
export function normalizeSteps(steps, { harness = false } = {}) {
  return (steps ?? []).map((s) => {
    const raw = String(s.name ?? "");
    const lower = raw.toLowerCase();
    const mcpMatch = MCP_RE.exec(raw);
    if (lower === "end state") {
      const changes = Array.isArray(s.output?.changes)
        ? s.output.changes.length
        : 0;
      return {
        ...s,
        nkind: "end",
        name: `End state: ${changes} ${changes === 1 ? "change" : "changes"}`,
        changes,
      };
    }
    if (lower.startsWith("customer"))
      return { ...s, nkind: "customer", name: "customer", type: "customer" };
    const toolLike =
      s.type === "tool" ||
      s.type === "retriever" ||
      Boolean(s.mode) ||
      Boolean(mcpMatch) ||
      (s.type === "chain" && Array.isArray(s.output));
    if (toolLike) {
      const name = mcpMatch ? mcpMatch[1] : raw;
      return {
        ...s,
        nkind: "tool",
        name,
        toolName: name,
        // A chain that returns a list is a retrieval (search_kb), not a tool call.
        type:
          s.type === "chain"
            ? Array.isArray(s.output) && !s.mode
              ? "retriever"
              : "tool"
            : s.type,
        mcp: Boolean(mcpMatch) || harness,
      };
    }
    const words =
      harness && lower === "assistant" ? countWords(s.output) : null;
    return {
      ...s,
      nkind: "llm",
      name: words != null ? `assistant · ${words} words` : raw,
      words,
    };
  });
}

/**
 * IT-R1 G4 · a harness tool name for display: MCP tools carry their server
 * (`mcp__acme-support__lookup_customer`) → `{name: "lookup_customer",
 * server: "acme-support", label: "lookup_customer · MCP acme-support"}`, so
 * Claude Code and Codex lanes line up on `lookup_customer`. Other names come
 * back unchanged (server null). Pure.
 * @param {string} name
 */
export function toolDisplayParts(name) {
  const raw = String(name ?? "");
  const m = /^mcp__([^_](?:[^_]|_(?!_))*)__(.+)$/.exec(raw);
  if (!m) return { name: raw, server: null, label: raw };
  return { name: m[2], server: m[1], label: `${m[2]} · MCP ${m[1]}` };
}
