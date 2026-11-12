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
import { getExperimentDefaultValue } from "./utils";
import { experimentBodyOfValues } from "./experimentBody";
import { entryOfCard, storedScoringOf } from "./experimentFormValues";
import {
  mappingAsChanged,
  runIdentityOf,
  stable,
} from "src/sections/common/Runs/runIdentity";
import { HTTP_RESEND_COPY } from "src/sections/common/Runs/httpResendCopy";
import { EXPERIMENT_STEP } from "./constants";
import { sameNameMapping, serverBlockOf } from "./serverBlockCopy";
import { runScopeBody, SCOPE_FIELDS } from "./runScopeBody";
import { isSurfaceShown } from "src/launch/launchSwitch";
import { a2aAuthProblem } from "src/sections/common/RunSources/endpoint/a2aMessage";

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

// exp.rename: dev saves an edit's name and description on their own (a body
// that mixes them with run settings answers 400), so an edit never puts them
// in the run-settings body: what changed travels as `metadata`, a separate
// PUT (editSave.js). A create sends them with the rest, as before.
export const METADATA_FIELDS = Object.freeze(["name", "description"]);

// Values of fields dev does not read that mean what dev does anyway.
const SAME_AS_DEV = {
  trials: (v) => Number(v) === 1,
  max_concurrency: (v) => Number(v) === DEV_ROWS_AT_ONCE,
  record: (v) => v !== false,
  // error_policy has no rule: with no policy the server gives an errored try
  // no score, so "zero" and "exclude" are both choices it must store. Empty
  // values (none chosen, metadata={}) follow the generic isEmpty rule below.
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
    v != null && typeof v === "object" && !Array.isArray(v) &&
    Object.entries(v).every(([name, value]) =>
      sameNameMapping(name, value, ctx.columns),
    ),
};

// The keys of an A2A agent entry. The server's A2A serializer is strict: it
// refuses any other key with a 400, even one the contract declares for
// another run type (http, variable_mapping, model...).
const A2A_ENTRY_KEYS = Object.freeze(["id", "name", "target_type", "a2a"]);
// Where Discover found the card: shown on the card, never a run setting. The
// server's A2A settings take no such field and would refuse the run.
const A2A_DISPLAY_ONLY = Object.freeze(["fetched_from"]);

// What an A2A agent run sets that the server does not send yet: each one
// blocks Run with its reason (serverBlockCopy.js), nothing is dropped.
function a2aBlockFields(a2a) {
  const out = [];
  if (!a2a?.card || typeof a2a.card !== "object") out.push("prompt_config[].a2a.card");
  if (!a2a?.text_column) out.push("prompt_config[].a2a.text_column");
  if ((a2a?.data_columns ?? []).length) out.push("prompt_config[].a2a.data_columns");
  if (a2a?.context_column) out.push("prompt_config[].a2a.context_column");
  if (a2aAuthProblem(a2a?.auth)) out.push("prompt_config[].a2a.auth");
  return out;
}

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
 *   columns?: {id: string, name: string}[], currentRowsChoice?: boolean}} ctx
 *   currentRowsChoice: the Data step offers Current rows as the version.
 * @returns {{body: object, blocks: {field, step, text, generic}[],
 *   definition: string|null, metadata: object|null}}
 *   body: only what dev reads; blocks: one per setting dev cannot honour;
 *   metadata: an edit's changed name / description (null on create).
 */
export function serverExperimentBody(body, ctx = {}) {
  const c = {
    isEditing: Boolean(ctx.isEditing),
    initial: ctx.initial ?? null,
    columns: ctx.columns ?? [],
    devRowsAtOnce: DEV_ROWS_AT_ONCE,
    currentRowsChoice: Boolean(ctx.currentRowsChoice),
  };
  const route = c.isEditing ? EXPERIMENT_UPDATE_ROUTE : EXPERIMENT_CREATE_ROUTE;
  const definition = bodyDefinitionOf(route.method, route.path);
  const blocks = [];
  const scopedBody = c.isEditing ? body : runScopeBody(body);
  const unknown = new Set(unknownFields(scopedBody, definition));
  const top = {};
  const metadata = {};
  Object.entries(scopedBody ?? {}).forEach(([key, value]) => {
    if (value === undefined) return;
    // A launch hold also applies to stale/preselected values while the cloud
    // contract still advertises scope. Block submission; never widen the run.
    if (!isSurfaceShown("exp.new.scope") && SCOPE_FIELDS.includes(key)) {
      if (!sameAsDev(key, value, c)) blocks.push(serverBlockOf(key, value, c));
      return;
    }
    if (c.isEditing && METADATA_FIELDS.includes(key)) {
      if (unknown.has(key)) {
        if (!sameAsDev(key, value, c))
          blocks.push(serverBlockOf(key, value, c));
      } else if (trimmed(value) !== trimmed(c.initial?.[key])) {
        metadata[key] = value;
      }
      return;
    }
    // A blank description is what dev saves when none is sent.
    if (key === "description" && !trimmed(value)) return;
    if (!unknown.has(key)) {
      top[key] = value;
      return;
    }
    if (!sameAsDev(key, value, c)) blocks.push(serverBlockOf(key, value, c));
  });
  // PromptConfigEntry declares HTTP fields for schema generation, but only
  // the HTTP branch consumes them. Known-to-the-schema is not admission for
  // another discriminator. In particular a prompt mapping must not become
  // accepted just because the HTTP contract now declares variable_mapping.
  const removed = [];
  const blockedRuns = new Set();
  (top.prompt_config ?? []).forEach((entry, index) => {
    const root = ["prompt_config", index];
    const kind = entry?.target_type;
    // A new A2A agent run travels as it is: the server saves it as an HTTP
    // source (http.protocol "a2a") and sends one message/send per row. An
    // edit reads it back as an HTTP source, so an A2A entry on edit still blocks.
    if (kind === "a2a" && !c.isEditing) {
      a2aBlockFields(entry?.a2a).forEach((field) =>
        blocks.push(serverBlockOf(field, entry?.a2a, c)),
      );
      A2A_DISPLAY_ONLY.forEach((key) => {
        if (entry?.a2a && Object.hasOwn(entry.a2a, key))
          removed.push([...root, "a2a", key]);
      });
      // Keys of another run type: an empty one is what the server does
      // without it (stays home); any other value blocks Run. Per-run limits
      // keep their own line.
      Object.keys(entry ?? {})
        .filter((key) => !A2A_ENTRY_KEYS.includes(key) && entry[key] !== undefined)
        .forEach((key) => {
          removed.push([...root, key]);
          if (isEmpty(entry[key])) return;
          blocks.push(
            serverBlockOf(
              key === "limits" ? "prompt_config[].limits" : "prompt_config[].a2a.other",
              entry[key],
              c,
            ),
          );
        });
      return;
    }
    if (kind != null && !["prompt", "agent", "http"].includes(kind)) {
      blockedRuns.add(root.join("."));
      blocks.push(serverBlockOf("prompt_config[].target_type", kind, c));
      removed.push([...root, "target_type"]);
      return;
    }
    if (kind === "http") {
      // The card uses run_id; the HTTP serializer identifies a saved source
      // by its EDT id. Never silently replace a different explicit identity.
      if (c.isEditing && entry.run_id != null) {
        if (entry.id != null && entry.id !== entry.run_id) {
          blocks.push(serverBlockOf("prompt_config[].id", entry.id, c));
        } else {
          top.prompt_config = [...top.prompt_config];
          top.prompt_config[index] = { ...entry, id: entry.run_id };
        }
        removed.push([...root, "run_id"]);
      }
      return;
    }
    // Prompt/agent identity already travels in their existing identifier keys.
    if (kind != null) removed.push([...root, "target_type"]);
    if (Object.hasOwn(entry ?? {}, "variable_mapping")) {
      if (!sameAsDev("prompt_config[].variable_mapping", entry.variable_mapping, c))
        blocks.push(serverBlockOf("prompt_config[].variable_mapping", entry.variable_mapping, { ...c, kind }));
      removed.push([...root, "variable_mapping"]);
    }
    if (Object.hasOwn(entry ?? {}, "http")) {
      blocks.push(serverBlockOf("prompt_config[].http", entry.http, c));
      removed.push([...root, "http"]);
    }
  });
  const admitted = withoutPaths(top, removed);
  const dropped = droppedNestedFields(admitted, definition);
  // A run of a kind dev cannot start blocks once; its other keys add nothing.
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
    if (blockedRuns.has(path.slice(0, 2).join("."))) return;
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
  const out = {
    body: withoutPaths(
      admitted,
      dropped.map((d) => d.path),
    ),
    blocks: unique,
    definition,
    metadata: c.isEditing ? metadata : null,
  };
  return c.isEditing && ctx.httpEdit ? httpEditWire(out, ctx.httpEdit) : out;
}

const HTTP_EDIT_BLOCK = Object.freeze({
  field: "prompt_config",
  step: EXPERIMENT_STEP.CONFIGURATION,
  text: HTTP_RESEND_COPY.editHttpOnly,
  generic: false,
});

const httpCardsOf = (values) =>
  (values?.promptConfig ?? []).filter((card) => card?.targetType === "http");

// The prompt and agent entries an Edit would send, as dev reads them.
function otherRunsKey(values, ctx) {
  const wire = serverExperimentBody(
    experimentBodyOfValues(values, { isEditing: true, datasetId: ctx.datasetId }),
    { isEditing: true, columns: ctx.columns, initial: ctx.initial },
  );
  return JSON.stringify(
    stable((wire.body?.prompt_config ?? []).filter((e) => e?.target_type !== "http")),
  );
}

/**
 * SRC S1b: whether an Edit of an experiment with saved HTTP sources goes
 * beyond those sources: a new or removed HTTP run, or a changed, new or
 * removed prompt or agent run. Pure; `initial` and `current` are form values.
 * @returns {{blocked: boolean}}
 */
export function httpEditChanges(initial, current, ctx = {}) {
  const saved = httpCardsOf(initial).map((card) => card.runId).filter(Boolean);
  const ids = httpCardsOf(current).map((card) => card.runId);
  const blocked =
    ids.some((id) => !id || !saved.includes(id)) ||
    new Set(ids).size !== ids.length ||
    saved.some((id) => !ids.includes(id)) ||
    otherRunsKey(initial, ctx) !== otherRunsKey(current, ctx);
  return { blocked };
}

/**
 * SRC S1b: Edit of an experiment with saved HTTP sources saves those sources
 * in place and runs nothing. Only its HTTP entries travel (prompt and agent
 * runs stay as saved); `changes.blocked` (httpEditChanges) blocks Save with
 * the reason, so nothing else is dropped without a word. Pure.
 */
export function httpEditWire(wire, changes) {
  const entries = wire?.body?.prompt_config;
  return {
    ...wire,
    body: Array.isArray(entries)
      ? {
          ...wire.body,
          prompt_config: entries.filter((e) => e?.target_type === "http"),
        }
      : wire?.body,
    blocks: changes?.blocked
      ? [...(wire?.blocks ?? []), HTTP_EDIT_BLOCK]
      : wire?.blocks ?? [],
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


/** Restore the core HTTP readback without requiring the optional run catalog. */
export function hydrateHttpSourceValues(values, detail, { isEditing = false } = {}) {
  if (!Array.isArray(detail?.http_configs) || !detail.http_configs.length) return values;

  // `order` counts runs, not cards. Rebuild each saved prompt/agent separately:
  // grouping two models before merging can move an HTTP or agent run between them.
  const classic = [
    ...(detail.prompt_configs ?? []).map((source) => ({ source, kind: "prompt_configs" })),
    ...(detail.agent_configs ?? []).map((source) => ({ source, kind: "agent_configs" })),
  ].map(({ source, kind }) => ({
    order: source.order,
    card: {
      ...getExperimentDefaultValue({
        ...detail,
        prompt_configs: [],
        agent_configs: [],
        [kind]: [source],
      }).promptConfig[0],
      _itemId: `${kind}-${source.id}`,
    },
  }));
  // Callers without classic readback can still supply already-built cards.
  // Flatten those too; never interpret a run position as a multi-model card index.
  if (!Array.isArray(detail.prompt_configs) && !Array.isArray(detail.agent_configs)) {
    (values.promptConfig ?? []).filter((card) => card.targetType !== "http").forEach((card) => {
      const cards = !card.agentId && (!card.targetType || card.targetType === "prompt") && card.model?.length
        ? card.model.map((model) => ({ ...card, model: [model] }))
        : [card];
      cards.forEach((item) => classic.push({ order: classic.length, card: item }));
    });
  }
  const sources = detail.http_configs.map((source) => {
    const http = JSON.parse(JSON.stringify(source.http ?? {}));
    if (Array.isArray(http.headers)) {
      // SecretHeaders edits only `secret`. Do not leave an invisible old alias
      // behind when the picker is replaced or cleared.
      http.headers = http.headers.map(({ secret_ref, ...header }) => ({
        ...header,
        ...(secret_ref != null && { secret: header.secret || secret_ref }),
      }));
    }
    const variableMapping = Object.fromEntries(
      Object.entries(JSON.parse(JSON.stringify(source.variable_mapping ?? {}))).map(([name, binding]) => [
        name,
        // The generic auto-mapper treats an empty constant as unfinished input.
        // A stored HTTP constant is explicit, including an omitted/empty value.
        binding?.kind === "constant" && (binding.value === "" || binding.value === undefined)
          ? { kind: "empty" }
          : binding,
      ]),
    );
    const variables = [...new Set(
      [...`${http.url ?? ""} ${http.body_template ?? ""}`.matchAll(/\{\{\s*([\w.-]+)\s*\}\}/g)]
        .map((match) => match[1])
        .filter((name) => !["row_id", "trial", "trace_id", "span_id"].includes(name)),
    )];
    const card = {
      _itemId: `http-${source.id}`,
      experimentType: "llm",
      targetType: "http",
      name: source.name || "HTTP endpoint",
      outputFormat: "string",
      variables,
      variableMapping,
      http: { ...http, bodyTemplate: http.body_template ?? "", outputPath: http.output_path ?? "" },
    };
    if (isEditing && source.id) {
      // Edit keeps the source's identity and what it was saved with: the
      // confirm compares against it (ranWith) and an unchanged source is sent
      // back exactly as read (withHttpSourceSettings), never re-normalized.
      const entry = entryOfCard(card);
      Object.assign(card, {
        runId: source.id,
        ranWith: runIdentityOf(entry, { trials: detail.trials ?? values.trials ?? 1 }),
        httpStored: {
          name: source.name ?? "",
          http: JSON.parse(JSON.stringify(source.http ?? {})),
          variable_mapping: JSON.parse(JSON.stringify(source.variable_mapping ?? {})),
          entry: JSON.parse(JSON.stringify(entry)),
        },
      });
    }
    return { order: source.order, card };
  });
  const cards = [...classic, ...sources]
    .sort((a, b) => (a.order ?? Number.MAX_SAFE_INTEGER) - (b.order ?? Number.MAX_SAFE_INTEGER))
    .map(({ card }) => card);
  const scope = detail.run_scope ?? {};
  const sample = scope.sample ?? detail.sample;
  const scoring = storedScoringOf([detail, ...(detail.runs ?? [])]);
  return {
    ...values,
    // Keep experiment-level settings on the classic fallback too. Unsupported
    // values must reach serverExperimentBody's visible block, not become defaults.
    description: detail.description ?? values.description ?? "",
    datasetVersionId: detail.dataset_version?.id ?? detail.dataset_version_id ?? values.datasetVersionId ?? null,
    splitIds: (scope.split_ids ?? detail.splits ?? values.splitIds ?? []).map((split) =>
      typeof split === "string" ? split : split?.id,
    ),
    rowIds: scope.row_ids ?? detail.row_ids ?? values.rowIds ?? [],
    ...(sample && { sample: { ...sample, on: true, n: sample.n ?? sample.size } }),
    trials: detail.trials ?? values.trials ?? 1,
    maxConcurrency: detail.runs?.[0]?.concurrency ?? values.maxConcurrency ?? DEV_ROWS_AT_ONCE,
    metadata: detail.metadata == null ? values.metadata ?? [] : Object.entries(detail.metadata)
      .filter(([key]) => key !== "source")
      .map(([key, value]) => ({ key, value: String(value ?? "") })),
    ...scoring,
    promptConfig: cards,
  };
}

/**
 * What an HTTP entry sends, as the edit confirm compares it: URL, method,
 * body, output path, headers and the mapping (same-name column mappings the
 * form adds by itself count as unchanged).
 */
function httpSettingsKey(entry, columns) {
  const identity = runIdentityOf(entry) ?? {};
  return JSON.stringify(stable({
    http: identity.http ?? null,
    variable_mapping: mappingAsChanged(identity.variable_mapping ?? null, columns),
  }));
}

/**
 * Edit of a saved HTTP source: keep its identity (run_id travels as id) and,
 * when its settings are unchanged, send exactly what was read back (secret
 * reference aliases, explicit empty constants, timeout), so saving other runs
 * or evaluations never changes this source. A changed source sends the edit.
 * An unchanged name is sent as stored.
 */
function savedHttpEntry(entry, card, columns) {
  const saved = card?.httpStored;
  if (!saved || entry.run_id == null) return entry;
  const out = { ...entry };
  const cardName = String(card.name ?? "").trim();
  const savedName = String(saved.name ?? "").trim();
  if (cardName === (savedName || "HTTP endpoint")) {
    if (savedName) out.name = saved.name;
    else delete out.name;
  }
  if (httpSettingsKey(entry, columns) !== httpSettingsKey(saved.entry, columns)) return out;
  out.http = JSON.parse(JSON.stringify(saved.http));
  if (Object.keys(saved.variable_mapping ?? {}).length)
    out.variable_mapping = JSON.parse(JSON.stringify(saved.variable_mapping));
  else delete out.variable_mapping;
  return out;
}

/** Preserve stored HTTP settings omitted by the legacy form-to-body helper. */
export function withHttpSourceSettings(body, cards = [], { columns = [] } = {}) {
  const httpCards = (cards ?? []).filter((card) => card?.targetType === "http");
  let index = 0;
  return {
    ...body,
    prompt_config: (body?.prompt_config ?? []).map((entry) => {
      if (entry?.target_type !== "http") return entry;
      const card = httpCards[index++];
      const settings = card?.http;
      if (!settings) return entry;
      const { bodyTemplate, outputPath, ...stored } = settings;
      return savedHttpEntry({
        ...entry,
        http: {
          ...stored,
          ...entry.http,
          body_template: bodyTemplate ?? stored.body_template ?? entry.http?.body_template ?? "",
          output_path: outputPath ?? stored.output_path ?? entry.http?.output_path ?? "",
        },
      }, card, columns);
    }),
  };
}
