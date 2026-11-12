// The declarative response transform of an HTTP endpoint (W6 B2). Pure, no
// code runs: every field is a JSON path read with the one path reader
// (common/Values/faPath.js).
//   output      a path (`$.reply`) or a template over paths
//               (`{{ $.reply }} (ref {{ $.ticket.id }})`)
//   tool_calls  a path to an array of `{name, arguments}`
//   usage       paths to `prompt_tokens` and `completion_tokens` (the try's
//               token count; the endpoint's own cost is not read: the run
//               costs $0 in Future AGI and the estimator prices it at 0)
// The browser Test, Future AGI's endpoint test and every try of the run
// apply the same transform to the same response body.
import { resolvePath } from "src/sections/common/Values/faPath";

const VAR_RE = /\{\{\s*([^{}]+?)\s*\}\}/g;
export const USAGE_FIELDS = Object.freeze([
  { id: "prompt_tokens", label: "Input tokens" },
  { id: "completion_tokens", label: "Output tokens" },
]);

/** The value at a path of a response body: `{found, value}`. `$` is the body. */
export function readPath(json, path) {
  const p = String(path ?? "").trim();
  if (!p) return { found: false };
  if (p === "$") return { found: json !== undefined, value: json };
  const got = resolvePath(json, p);
  return got.found ? { found: true, value: got.value } : { found: false };
}

const textOf = (v) =>
  v == null ? "" : typeof v === "string" ? v : JSON.stringify(v);

/** True when the output field is a template (it has `{{ … }}`). */
export const isTemplate = (s) => /\{\{[^{}]+\}\}/.test(String(s ?? ""));

/** A transform from a card's fields (empty fields dropped). */
export function transformOf(endpoint = {}) {
  const t = endpoint.transform ?? {};
  const output =
    String(t.output ?? "").trim() ||
    String(endpoint.output_path ?? endpoint.outputPath ?? "").trim() ||
    "$.reply";
  const toolCalls = String(t.tool_calls ?? "").trim();
  const usage = Object.fromEntries(
    USAGE_FIELDS.map((f) => [
      f.id,
      String(t.usage?.[f.id] ?? "").trim(),
    ]).filter(([, v]) => v),
  );
  return {
    output,
    ...(toolCalls ? { tool_calls: toolCalls } : {}),
    ...(Object.keys(usage).length ? { usage } : {}),
  };
}

/** Problems of a transform before it runs (a path must start with `$`). */
export function transformIssues(transform = {}) {
  const out = [];
  const pathOk = (p) => /^\$/.test(String(p).trim());
  const o = String(transform.output ?? "");
  if (isTemplate(o)) {
    o.replace(VAR_RE, (m, p) => {
      if (!pathOk(p))
        out.push(`${p} in the output template is not a path like $.reply`);
      return m;
    });
  } else if (o && !pathOk(o))
    out.push(`Output ${o} is not a path like $.reply`);
  if (transform.tool_calls && !pathOk(transform.tool_calls))
    out.push(`Tool calls ${transform.tool_calls} is not a path like $.actions`);
  Object.entries(transform.usage ?? {}).forEach(([k, p]) => {
    if (!pathOk(p)) out.push(`${k} ${p} is not a path like $.usage.${k}`);
  });
  return out;
}

const callOf = (c) => {
  if (!c || typeof c !== "object") return null;
  const fn = c.function && typeof c.function === "object" ? c.function : null;
  const name = c.name ?? c.tool ?? fn?.name;
  if (!name) return null;
  let args = c.arguments ?? c.args ?? c.input ?? fn?.arguments ?? {};
  if (typeof args === "string") {
    try {
      args = JSON.parse(args);
    } catch {
      /* keep the text */
    }
  }
  return { name: String(name), arguments: args };
};

/**
 * Apply a transform to a response body. Only a missing output is an error
 * (the try has no output to score); a reply with no tool-call list made no
 * tool calls, and a usage path that misses is a note.
 * @returns {{output: string|null, tool_calls: object[]|null,
 *   usage: object|null, errors: string[], notes: string[]}}
 */
export function applyTransform(json, transform = {}) {
  const errors = [];
  const notes = [];
  const t = transform.output ? transform : { ...transform, output: "$.reply" };
  let output = null;
  if (isTemplate(t.output)) {
    let missed = false;
    const text = String(t.output).replace(VAR_RE, (m, p) => {
      const got = readPath(json, p);
      if (!got.found) {
        errors.push(`Output path ${p.trim()} not found in the response`);
        missed = true;
        return "";
      }
      return textOf(got.value);
    });
    output = missed ? null : text;
  } else {
    const got = readPath(json, t.output);
    if (!got.found)
      errors.push(
        `Output path ${String(t.output).trim()} not found in the response`,
      );
    else output = textOf(got.value);
  }
  let toolCalls = null;
  if (t.tool_calls) {
    const got = readPath(json, t.tool_calls);
    if (!got.found) {
      toolCalls = [];
      notes.push(`No tool calls: nothing at ${t.tool_calls} in this response`);
    } else if (!Array.isArray(got.value)) {
      toolCalls = [];
      notes.push(`Tool calls at ${t.tool_calls} is not a list`);
    } else toolCalls = got.value.map(callOf).filter(Boolean);
  }
  let usage = null;
  if (t.usage && Object.keys(t.usage).length) {
    usage = {};
    Object.entries(t.usage).forEach(([k, p]) => {
      const got = readPath(json, p);
      const n = Number(got.value);
      if (!got.found || got.value === null || !Number.isFinite(n))
        notes.push(`Usage ${k}: no number at ${p} in this response`);
      else usage[k] = n;
    });
    if (!Object.keys(usage).length) usage = null;
  }
  return { output, tool_calls: toolCalls, usage, errors, notes };
}

/** `Output $.reply · tool calls $.actions · usage 3 paths` (summary line). */
export function transformSummary(transform = {}) {
  const parts = [`Output ${transform.output ?? "$.reply"}`];
  if (transform.tool_calls) parts.push(`tool calls ${transform.tool_calls}`);
  const u = Object.keys(transform.usage ?? {}).length;
  if (u) parts.push(`usage ${u} path${u === 1 ? "" : "s"}`);
  return parts.join(" · ");
}
