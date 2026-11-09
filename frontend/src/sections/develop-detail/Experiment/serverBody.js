// The experiment create / edit body on the real backend (the capability contract(b)).
// Dev's create (ExperimentCreateV2) and edit (ExperimentUpdateV2) are strict:
// a top-level field they do not declare answers 400, a field inside a run
// entry is dropped without a word. So:
//   - a field dev declares travels as it is;
//   - a field dev does not read, holding exactly what dev does anyway (1 try,
//     10 rows at once, a prompt run, {{q}} from column `q`), stays home;
//   - any other value of such a field BLOCKS Run with its reason
//     (serverBlockCopy.js). Nothing is stripped silently.
// Which fields dev declares comes from the generated contract (strictBody),
// never from a list in this file. Prototype mode (the internal preview on
// mocks) sends the whole DSX body, unchanged.
import {
  bodyDefinitionOf,
  droppedNestedFields,
  unknownFields,
} from "src/api/contracts/strictBody";
import { isPrototypeMode } from "src/utils/prototype";
import { sameNameMapping, serverBlockOf } from "./serverBlockCopy";
import { runScopeBody, SCOPE_FIELDS } from "./runScopeBody";
import { isSurfaceShown } from "src/launch/launchSwitch";

export const EXPERIMENT_CREATE_ROUTE = Object.freeze({
  method: "post",
  path: "/model-hub/experiments/v2/",
});
export const EXPERIMENT_UPDATE_ROUTE = Object.freeze({
  method: "put",
  path: "/model-hub/experiments/v2/{experiment_id}/",
});

// Dev starts every V2 experiment with max_concurrent_rows=10
// (ExperimentsTableV2View.post, model_hub/views/experiments.py).
export const DEV_ROWS_AT_ONCE = 10;

const trimmed = (v) => String(v ?? "").trim();

// Values of fields dev does not read that mean what dev does anyway.
const SAME_AS_DEV = {
  trials: (v) => Number(v) === 1,
  max_concurrency: (v) => Number(v) === DEV_ROWS_AT_ONCE,
  record: (v) => v !== false,
  // The edit body repeats the name and description: unchanged = not sent.
  name: (v, ctx) => ctx.isEditing && trimmed(v) === trimmed(ctx.initial?.name),
  description: (v, ctx) =>
    !trimmed(v) ||
    (ctx.isEditing && trimmed(v) === trimmed(ctx.initial?.description)),
  // Dev tells a prompt run from an agent run by its ids.
  "prompt_config[].target_type": (v) =>
    v == null || v === "prompt" || v === "agent",
  // Edit: dev finds a run by its entry `id`, which travels.
  "prompt_config[].run_id": (_v, ctx) => ctx.isEditing,
  "prompt_config[].variable_mapping": (v, ctx) =>
    Object.entries(v ?? {}).every(([name, value]) =>
      sameNameMapping(name, value, ctx.columns),
    ),
};

const isEmpty = (v) =>
  v == null ||
  v === "" ||
  (Array.isArray(v) && v.length === 0) ||
  (typeof v === "object" && !Array.isArray(v) && Object.keys(v).length === 0);

/** `["prompt_config", 0, "target_type"]` -> `prompt_config[].target_type`. */
export const fieldIdOf = (path) =>
  path
    .map((p) => (typeof p === "number" ? "[]" : p))
    .join(".")
    .replace(/\.\[\]/g, "[]");

function sameAsDev(field, value, ctx) {
  const rule = SAME_AS_DEV[field];
  if (rule) return Boolean(rule(value, ctx));
  return isEmpty(value);
}

function withoutPaths(body, paths) {
  const out = JSON.parse(JSON.stringify(body ?? {}));
  paths.forEach((path) => {
    let node = out;
    for (let i = 0; i < path.length - 1; i += 1) {
      node = node?.[path[i]];
      if (node == null) return;
    }
    if (node && typeof node === "object") delete node[path[path.length - 1]];
  });
  return out;
}

/**
 * @param {object} body  the DSX body (experimentBody.js)
 * @param {{isEditing?: boolean, initial?: {name?: string, description?: string},
 *   columns?: {id: string, name: string}[]}} ctx
 * @returns {{body: object, blocks: {field, step, text, generic}[],
 *   definition: string|null}}
 *   body: only what dev reads; blocks: one per setting dev cannot honour.
 */
export function serverExperimentBody(body, ctx = {}) {
  const c = {
    isEditing: Boolean(ctx.isEditing),
    initial: ctx.initial ?? null,
    columns: ctx.columns ?? [],
    devRowsAtOnce: DEV_ROWS_AT_ONCE,
  };
  const route = c.isEditing ? EXPERIMENT_UPDATE_ROUTE : EXPERIMENT_CREATE_ROUTE;
  const definition = bodyDefinitionOf(route.method, route.path);
  const blocks = [];
  const scopedBody = c.isEditing ? body : runScopeBody(body);
  const unknown = new Set(unknownFields(scopedBody, definition));
  const top = {};
  Object.entries(scopedBody ?? {}).forEach(([key, value]) => {
    if (value === undefined) return;
    // A launch hold also applies to stale/preselected values while the cloud
    // contract still advertises scope. Block submission; never widen the run.
    if (!isSurfaceShown("exp.new.scope") && SCOPE_FIELDS.includes(key)) {
      blocks.push(serverBlockOf(key, value, c));
      return;
    }
    if (!unknown.has(key)) {
      top[key] = value;
      return;
    }
    if (!sameAsDev(key, value, c)) blocks.push(serverBlockOf(key, value, c));
  });
  const dropped = droppedNestedFields(top, definition);
  // A run of a kind dev cannot start blocks once; its other keys add nothing.
  const blockedRuns = new Set();
  dropped.forEach(({ path, value }) => {
    const field = fieldIdOf(path);
    if (field.endsWith("[].target_type") && !sameAsDev(field, value, c)) {
      blockedRuns.add(path.slice(0, -1).join("."));
      blocks.push(serverBlockOf(field, value, c));
    }
  });
  dropped.forEach(({ path, value }) => {
    const field = fieldIdOf(path);
    if (field.endsWith("[].target_type")) return;
    if (blockedRuns.has(path.slice(0, -1).join("."))) return;
    if (!sameAsDev(field, value, c))
      blocks.push(serverBlockOf(field, value, c));
  });
  const seen = new Set();
  const unique = blocks.filter((b) => {
    const k = `${b.field}|${b.text}`;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
  return {
    body: withoutPaths(
      top,
      dropped.map((d) => d.path),
    ),
    blocks: unique,
    definition,
  };
}

/**
 * What the wizard sends: the strict body on the real backend, the whole DSX
 * body in prototype mode (whose mocks read every field).
 */
export function wireExperimentBody(
  body,
  ctx = {},
  mockMode = isPrototypeMode(),
) {
  if (mockMode) return { body, blocks: [], definition: null };
  return serverExperimentBody(body, ctx);
}

/**
 * The wizard's step issues with the server blocks merged in, each on the
 * step where its setting lives (pure; `issues` is stepIssues' array).
 */
export function withServerBlocks(issues, blocks = []) {
  const out = (issues ?? []).map((list) => [...(list ?? [])]);
  (blocks ?? []).forEach((b) => {
    const i = Math.min(Math.max(0, b.step ?? out.length - 1), out.length - 1);
    if (i >= 0 && !out[i].includes(b.text)) out[i].push(b.text);
  });
  return out;
}

/**
 * The first server block that lives on `step` (the same step rule as
 * withServerBlocks; pure). The wizard shows its text on that step, so a
 * disabled Continue never hides its reason in a tooltip.
 */
export function serverBlockOnStep(blocks = [], step, stepCount) {
  const last = Math.max(0, (stepCount ?? 1) - 1);
  return (
    (blocks ?? []).find(
      (b) => Math.min(Math.max(0, b.step ?? last), last) === step,
    ) ?? null
  );
}
