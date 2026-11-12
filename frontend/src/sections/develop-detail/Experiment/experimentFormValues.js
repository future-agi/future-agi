import { DEFAULT_HARNESS as PREVIEW_HARNESS } from "src/test-fixtures/build-data/harnessDefaults";

const PROTOTYPE_BUILD =
  import.meta.env.VITE_ENVIRONMENT === "local" &&
  import.meta.env.VITE_PROTOTYPE_MODE === "true";
const INCLUDE_PREVIEW = PROTOTYPE_BUILD || import.meta.env.MODE === "test";

// Form values of the New / Edit experiment drawer and the extra keys the
// create / edit body carries (PRODUCT-SPEC §3.8, §6.1). Pure.
import { getRandomId } from "src/utils/utils";
import { parseReducer } from "src/sections/common/Analysis/trialReducers";
import { runTriesOf } from "./estimateInputs";
import { DEFAULT_CONCURRENCY, PROMPT_CONFIG_TYPE } from "./constants";
import { promptToolsPayload } from "./toolSetup";
import {
  escapeModelKey,
  modelParamsFor,
  requestModelParams,
} from "./modelParamNames";
import {
  entryOfConfig,
  isOffered,
} from "src/sections/common/RunSources/catalog";
import { recipeFromConfig } from "src/sections/common/RunSources/recipe/recipeModel";
import { withCardExtras } from "src/sections/common/RunSettings/cardExtras";
import {
  forCompare,
  runIdentityChanges,
  runIdentityOf,
  sameRunIdentity,
  stable,
} from "src/sections/common/Runs/runIdentity";

export const DEFAULT_HTTP = {
  url: "",
  method: "POST",
  bodyTemplate: '{\n  "message": "{{customer_message}}"\n}',
  outputPath: "$.reply",
};

export const emptyExperimentValues = () => ({
  columnId: null,
  name: "",
  description: "",
  outputFormat: "string",
  experimentType: "llm",
  promptConfig: [],
  userEvalMetrics: [],
  datasetVersionId: null,
  splitIds: [],
  trials: 1,
  maxConcurrency: DEFAULT_CONCURRENCY,
  metadata: [],
});

/** Defaults of the R2 cards (field names frozen, R2 manifest row 42c). */
export const DEFAULT_CODE = { mode: "upload", host_hint: "", trials: null };
export const DEFAULT_TRACES = {
  project_id: null,
  project_version_id: null,
  match: { by: "metadata", key: "row_id" },
  output_path: "$.root.output",
  max_trials: 1,
};
export const DEFAULT_HARNESS = INCLUDE_PREVIEW
  ? PREVIEW_HARNESS
  : {
      name: "",
      model: "",
      sandbox: null,
      mcp_servers: [],
      user_sim: null,
      limits: { max_turns: 30, timeout_s: 600, max_cost_usd: 0.5 },
      instructions: "",
      secrets: [],
    };

/** Defaults of the A2A and MCP tool cards (options-truth L1 G5). */
export const DEFAULT_A2A = {
  card_url: "",
  card: null,
  skill_id: null,
  text_column: null,
  data_columns: [],
  context_column: null,
  auth: { name: "Authorization", secret: "" },
};
export const DEFAULT_MCP_TOOL = {
  server: { transport: "stdio", command: "", url: "", env_secret: "" },
  tools: [],
  tool: null,
  arguments: {},
  side_effects: "stubs",
};

/** A new From code / HTTP / My code / Traces / Harness / A2A / MCP card. */
export function newRunCard(type) {
  const base = {
    _itemId: getRandomId(),
    experimentType: "llm",
    targetType: type,
    outputFormat: "string",
    variables: [],
    variableMapping: {},
  };
  if (type === PROMPT_CONFIG_TYPE.EXTERNAL)
    return { ...base, name: "From code", runName: "" };
  if (type === PROMPT_CONFIG_TYPE.HTTP)
    return { ...base, name: "HTTP endpoint", http: { ...DEFAULT_HTTP } };
  if (type === PROMPT_CONFIG_TYPE.A2A)
    return {
      ...base,
      name: "A2A agent card",
      runName: "",
      a2a: { ...DEFAULT_A2A },
    };
  if (type === PROMPT_CONFIG_TYPE.MCP_TOOL)
    return {
      ...base,
      name: "MCP server tool",
      runName: "",
      mcp_tool: { ...DEFAULT_MCP_TOOL, server: { ...DEFAULT_MCP_TOOL.server } },
    };
  if (type === PROMPT_CONFIG_TYPE.CODE)
    return { ...base, name: "My code", runName: "", code: { ...DEFAULT_CODE } };
  if (type === PROMPT_CONFIG_TYPE.TRACES)
    return {
      ...base,
      name: "Traces I already have",
      runName: "",
      traces: { ...DEFAULT_TRACES, match: { ...DEFAULT_TRACES.match } },
    };
  if (type === PROMPT_CONFIG_TYPE.HARNESS)
    return {
      ...base,
      name: "Agent harness",
      runName: "",
      harness_config: {
        ...DEFAULT_HARNESS,
        limits: { ...DEFAULT_HARNESS.limits },
      },
    };
  return base;
}

/**
 * A run card from a Level 3 draft: the picker's draft rides on
 * `r4` (entry, mode, form, run config, reserved run id); `targetType` keeps
 * the R2 kind so the plan, letters and issues work unchanged.
 * @param {object} draft  SourceConfig's draft (+ `config`)
 */
export function newR4Card(draft) {
  const kind = draft?.kind ?? "code";
  const type =
    kind === "harness"
      ? PROMPT_CONFIG_TYPE.HARNESS
      : kind === "voice"
        ? PROMPT_CONFIG_TYPE.VOICE
        : PROMPT_CONFIG_TYPE.CODE;
  const { machine, agent, sideEffect, ...rest } = draft ?? {};
  return {
    _itemId: getRandomId(),
    experimentType: "llm",
    targetType: type,
    outputFormat: "string",
    variables: [],
    variableMapping: {},
    name: draft?.runName ?? "",
    runName: draft?.runName ?? "",
    r4: {
      ...rest,
      // Names for the discard confirm (runPlan.draftSummary).
      entryName:
        rest.config?.framework?.name ??
        rest.config?.harness?.name ??
        draft?.runName ??
        null,
      whereName:
        draft?.mode === "local"
          ? draft?.kind === "harness"
            ? "Docker"
            : "This machine"
          : draft?.mode === "sandbox"
            ? "the Future AGI sandbox"
            : draft?.mode === "ci"
              ? "CI"
              : draft?.mode === "remote"
                ? "a remote endpoint"
                : null,
      machine: machine
        ? { id: machine.id, name: machine.name, owner: machine.owner ?? null }
        : null,
      agent: agent
        ? {
            id: agent.id,
            name: agent.name,
            side_effect_tools: agent.side_effect_tools ?? [],
          }
        : null,
      sideEffect: sideEffect ?? null,
    },
    ...(type === PROMPT_CONFIG_TYPE.CODE
      ? {
          code: {
            ...DEFAULT_CODE,
            mode: draft?.form === "connect" ? "sdk_preview" : "upload",
          },
        }
      : {}),
    ...(type === PROMPT_CONFIG_TYPE.HARNESS
      ? {
          harness_config: {
            ...DEFAULT_HARNESS,
            name: draft?.config?.harness?.name ?? DEFAULT_HARNESS.name,
            // IT-R2 G1 P6: the harness's own model, so the estimate finds the
            // same setup's last run (Codex CLI · gpt-5-codex → #2E).
            model: draft?.config?.harness?.model ?? DEFAULT_HARNESS.model,
            limits: { ...DEFAULT_HARNESS.limits },
          },
        }
      : {}),
  };
}

const mainModel = (id) =>
  id ? [{ id, role: "main", calls: null, source: "declared" }] : [];

/**
 * options-truth L3 G8 P6: the R4 config of an R2-era run, so a copy keeps
 * what it ran. `#1C` (My code, `code.framework: "LangGraph 0.6"`, ran by
 * `alice@mbp-14`) gets its framework id, version, model and "this machine";
 * `#1D` (harness by name, `sandbox: "Docker · …"`) gets its harness id,
 * model, MCP servers and Docker (local) or the Future AGI sandbox. R4
 * configs (a framework id, a harness id, an execution) come back as-is.
 * @param {object} c      the run's config
 * @param {object} entry  its catalog entry (entryOfConfig)
 */
export function r4ConfigOfLegacy(c, entry) {
  if (!c || !entry?.id) return c;
  if (c.kind === "code" && !c.framework?.id) {
    const fw = String(c.code?.framework ?? "");
    const version = fw.slice(String(entry.name ?? "").length).trim() || null;
    return {
      ...c,
      framework: {
        id: entry.id,
        name: entry.name,
        version,
        language: entry.snippetKey ?? entry.languages?.[0] ?? null,
      },
      execution: c.execution ?? {
        mode: "local",
        label: c.code?.host ?? null,
      },
      models: c.models ?? mainModel(c.model?.id),
      tools: c.tools ?? [],
    };
  }
  if (c.kind === "harness" && !(c.harness?.id && c.execution)) {
    const sandbox = String(c.harness?.sandbox ?? "");
    const docker = /^docker\b/i.test(sandbox);
    return {
      ...c,
      harness: { ...(c.harness ?? {}), id: c.harness?.id ?? entry.id },
      execution: c.execution ?? {
        mode: docker ? "local" : "sandbox",
        label: docker ? "Docker" : "Future AGI sandbox",
      },
      models: c.models ?? mainModel(c.harness?.model ?? c.model?.id),
      mcp_servers:
        c.mcp_servers ??
        (c.harness?.mcp_servers ?? []).map((s) => ({
          name: s?.name ?? s,
          tools: s?.tools ?? null,
        })),
    };
  }
  return c;
}

/**
 * L2 G9: the R4 draft of an earlier run (Start from, Duplicate & edit), so
 * its card is that run's setup: framework / harness / voice entry, models,
 * tools, MCP servers, where it runs (machine, CI, sandbox), limits and the
 * harness recipe. Null for runs without an R4 config (prompt, agent, R2).
 * @param {{name?: string, config?: object}} run
 */
export function r4DraftOfRun(run) {
  const raw = run?.config ?? null;
  if (
    !raw ||
    !(
      raw.framework?.id ||
      raw.kind === "harness" ||
      raw.kind === "voice" ||
      raw.kind === "code"
    )
  )
    return null;
  const entry = entryOfConfig(raw);
  if (!entry?.id) return null;
  const c = r4ConfigOfLegacy(raw, entry);
  const kind =
    entry.kind === "harness"
      ? "harness"
      : entry.kind === "voice"
        ? "voice"
        : "code";
  const ex = c.execution ?? {};
  const machine =
    ex.mode === "local" && ex.machine_id
      ? { id: ex.machine_id, name: ex.label ?? null, owner: ex.owner ?? null }
      : null;
  const models = (c.models ?? [])
    .filter((m) => !m?.helper && m?.role !== "helper")
    .map((m) => ({ name: m?.id ?? m?.name ?? m }))
    .filter((m) => m.name);
  const tools = (c.tools ?? []).map((t) => t?.name ?? t).filter(Boolean);
  const limits = c.limits ?? {};
  return {
    kind,
    entryId: entry.id,
    mode: ex.mode ?? (kind === "harness" ? "sandbox" : "local"),
    form: machine || c.code?.mode === "connect_preview" ? "connect" : "upload",
    runName: run?.name ?? "",
    machine,
    config: c,
    uses: { models, tools: c.tools ?? [], mcp_servers: c.mcp_servers ?? [] },
    declared: {
      model:
        c.engine?.model ??
        c.params?.values?.model ??
        c.harness?.model ??
        models[0]?.name ??
        null,
      tools,
    },
    limits: {
      maxCostUsd: limits.max_cost_usd ?? c.harness?.max_cost_usd ?? null,
      timeoutS: limits.timeout_s ?? c.harness?.timeout_s ?? null,
      concurrency: ex.concurrency ?? limits.concurrency ?? null,
    },
    ...(kind === "harness"
      ? { slot: { harness: recipeFromConfig(c.harness ?? null, entry) } }
      : {}),
  };
}

/** The `r4` body block of an R4 card (null for other cards). */
export function r4Payload(item) {
  const r4 = item?.r4;
  if (!r4) return null;
  return {
    entry_id: r4.entryId,
    mode: r4.mode,
    form: r4.form ?? null,
    reserved_run_id: r4.reserved?.runId ?? null,
    config: r4.config ?? null,
    ...(seedFirstResultOf(r4)
      ? { seed_first_result: seedFirstResultOf(r4) }
      : {}),
  };
}

/**
 * IT-R5 G1 P1: the one row the wizard's Simulate showed (`1 of 1 passed`),
 * sent with the create so the run it becomes holds that same result.
 * @returns {{row_id: string, trace_id: string|null, status: string}|null}
 */
export function seedFirstResultOf(r4) {
  const a = r4?.arrival;
  if (!a || (!a.row_id && !a.trace_id)) return null;
  return {
    row_id: a.row_id ?? null,
    trace_id: a.trace_id ?? null,
    status: a.status ?? null,
  };
}

/**
 * The create / edit body entry of an R2 card (`code`, `traces`, `harness`):
 * its fields pass through as-is (R2 manifest row 42c). Null for other types.
 * @param {object} item  form card
 * @param {object} [runRef]  `{run_id}` in edit mode
 */
export function r2RunPayload(item, runRef = {}) {
  const type = item?.targetType;
  const name = String(item?.runName || item?.name || "").trim();
  const mapping = item?.variableMapping ?? {};
  const variableMapping = Object.keys(mapping).length
    ? { variable_mapping: mapping }
    : {};
  const r4 = r4Payload(item);
  const r4Block = r4 ? { r4 } : {};
  if (type === PROMPT_CONFIG_TYPE.CODE)
    return {
      target_type: "code",
      name,
      code: { ...DEFAULT_CODE, ...(item?.code ?? {}) },
      ...r4Block,
      ...runRef,
    };
  if (type === PROMPT_CONFIG_TYPE.VOICE)
    return {
      target_type: "voice",
      name,
      ...variableMapping,
      ...r4Block,
      ...runRef,
    };
  if (type === PROMPT_CONFIG_TYPE.TRACES)
    return {
      target_type: "traces",
      name,
      traces: { ...DEFAULT_TRACES, ...(item?.traces ?? {}) },
      ...runRef,
    };
  if (type === PROMPT_CONFIG_TYPE.HTTP) {
    const h = item?.http ?? {};
    return {
      target_type: "http",
      ...(item?.name && item.name !== "HTTP endpoint" ? { name } : {}),
      ...variableMapping,
      http: {
        url: String(h.url ?? "").trim(),
        method: h.method ?? "POST",
        headers: (h.headers ?? []).filter((x) => String(x?.name ?? "").trim()),
        body_template: h.bodyTemplate ?? "",
        output_path: String(h.outputPath ?? "").trim(),
        streaming: Boolean(h.streaming),
        audio: h.audio ?? "text",
      },
      ...runRef,
    };
  }
  if (type === PROMPT_CONFIG_TYPE.A2A) {
    // The card itself is sent so the run reads the skills it showed.
    const { card, ...rest } = { ...DEFAULT_A2A, ...(item?.a2a ?? {}) };
    return {
      target_type: "a2a",
      name,
      a2a: { ...rest, card },
      ...runRef,
    };
  }
  if (type === PROMPT_CONFIG_TYPE.MCP_TOOL) {
    const { tools: _tools, ...m } = {
      ...DEFAULT_MCP_TOOL,
      ...(item?.mcp_tool ?? {}),
    };
    return { target_type: "mcp_tool", name, mcp_tool: m, ...runRef };
  }
  if (type === PROMPT_CONFIG_TYPE.HARNESS)
    return {
      target_type: "harness",
      name,
      ...variableMapping,
      harness_config: { ...DEFAULT_HARNESS, ...(item?.harness_config ?? {}) },
      ...r4Block,
      ...runRef,
    };
  return null;
}

/**
 * R2 keys on the create body's prompt entries (R2-SPEC §5.5): each prompt
 * run gets its card's `tools[{name, schema, mode, endpoint}]`,
 * `max_tool_turns` and `on_unrecorded`. `entries` are the transformed
 * `prompt_config` (one per run, card order); `runs` = flattenRuns(cards).
 */
export function withPromptTools(entries, cards, runs) {
  return (entries ?? []).map((entry, i) => {
    const card = cards?.[runs?.[i]?.configIndex];
    // the interface contract: each card's extra keys (RunSettings/cardExtras.js slots).
    if (entry?.target_type !== "prompt") return withCardExtras(entry, card);
    return card
      ? withCardExtras({ ...entry, ...promptToolsPayload(card) }, card)
      : entry;
  });
}

/**
 * Top-level keys of a create that started from an earlier experiment
 * (`Start from`, R2-SPEC §5.3.1): `preset` and, when the carry-over toggle
 * is on and the dataset version moved, `carry_over_from_experiment_id`.
 * Form field `startFrom` = `{experimentId, preset, carryOver, versionMoved}`.
 */
export function startFromPayload(values) {
  const sf = values?.startFrom;
  if (!sf?.experimentId) return {};
  return {
    ...(sf.preset ? { preset: sf.preset } : {}),
    ...(sf.carryOver !== false && sf.versionMoved
      ? { carry_over_from_experiment_id: sf.experimentId }
      : {}),
  };
}

const providerFields = (params = {}) => ({
  providers: params.providers ?? "",
  logoUrl: params.logoUrl ?? "",
});

/**
 * `2 runs not copied: runs from your code, a harness, a voice agent, an
 * upload or linked traces are not started from Future AGI.` (pure; null
 * for none).
 */
export function notCopiedLine(n, labels = []) {
  if (!n) return null;
  const named = (labels ?? []).filter(Boolean);
  const which = !named.length
    ? ""
    : named.length === 1
      ? ` (${named[0]})`
      : named.length === n && runSpan(named)
        ? ` (${runSpan(named)})`
        : ` (${named.join(", ")})`;
  return `${n} ${n === 1 ? "run" : "runs"} not copied${which}: ${n === 1 ? "it" : "they"} ran outside Future AGI, so there is nothing here to start again.`;
}

// `#3A–#3F` when the labels are consecutive letters of one experiment.
function runSpan(labels) {
  const parts = labels.map((l) => /^#(\d+)([A-Z])$/.exec(String(l)));
  if (parts.some((p) => !p) || new Set(parts.map((p) => p[1])).size !== 1)
    return null;
  const codes = parts.map((p) => p[2].charCodeAt(0));
  const consecutive = codes.every((c, i) => i === 0 || c === codes[i - 1] + 1);
  return consecutive && labels.length > 2
    ? `${labels[0]}–${labels[labels.length - 1]}`
    : null;
}

/**
 * L7b G2 (legacy:r4-run-card-kept): the cards without the one at `index`
 * (found by its id when the list moved), exactly one card fewer. Pure.
 */
export function withoutCard(cards = [], index) {
  const list = cards ?? [];
  const id = list[index]?._itemId;
  const at =
    id && list[index]?._itemId === id
      ? index
      : list.findIndex((c) => id && c?._itemId === id);
  const drop = at >= 0 ? at : index;
  return list.filter((_, i) => i !== drop);
}

/** The tries a run played itself (null when it took the experiment's). */
const ownTriesOfRun = (run, trials) => {
  const t = Number(run?.config?.limits?.tries);
  return t > 0 && t !== (Number(trials) || 1) ? t : null;
};

/** An endpoint, A2A or MCP card's limits from its run's config. */
const limitsOfRun = (run, trials) => {
  const tries = ownTriesOfRun(run, trials);
  const cap = Number(run?.config?.limits?.max_cost_usd) || null;
  return tries || cap
    ? { ...(tries ? { tries } : {}), ...(cap ? { maxCostUsd: cap } : {}) }
    : null;
};

/** A run's variable mapping as its card holds it ({} for none). */
const mappingOfRun = (run) => {
  const m = run?.config?.variable_mapping;
  return m && typeof m === "object" ? { ...m } : {};
};

/** The HTTP card fields of an endpoint run (its stored endpoint). */
export function httpCardOfRun(run) {
  const c = run?.config ?? {};
  const t = run?.target ?? {};
  const e = c.endpoint ?? c.http ?? run?.http ?? {};
  const transform = e.transform ?? null;
  return {
    ...DEFAULT_HTTP,
    url: e.url ?? t.url ?? "",
    method: String(e.method ?? t.method ?? "POST").toUpperCase(),
    headers: Array.isArray(e.headers) ? e.headers.map((h) => ({ ...h })) : [],
    bodyTemplate:
      e.body_template ?? e.bodyTemplate ?? DEFAULT_HTTP.bodyTemplate,
    outputPath: e.output_path ?? e.outputPath ?? DEFAULT_HTTP.outputPath,
    ...(e.streaming ? { streaming: true } : {}),
    ...(e.audio ? { audio: e.audio } : {}),
    ...(transform ? { transform: { ...transform } } : {}),
    ...(e.preset ? { preset: e.preset } : {}),
  };
}

/** The A2A card fields of an A2A run (card, skill, parts, auth). */
export function a2aCardOfRun(run) {
  const a = run?.config?.a2a ?? {};
  const { agent: _agent, version: _version, ...rest } = a;
  return {
    ...DEFAULT_A2A,
    ...rest,
    card_url: a.card_url ?? a.fetched_from ?? a.card?.url ?? "",
    card: a.card ?? null,
    skill_id: a.skill_id ?? run?.target?.skill ?? null,
    data_columns: [...(a.data_columns ?? [])],
    auth: a.auth ? { ...a.auth } : { ...DEFAULT_A2A.auth },
  };
}

/** The MCP card fields of an MCP tool run (server incl. env, tool, args). */
export function mcpCardOfRun(run) {
  const m = run?.config?.mcp_tool ?? {};
  return {
    ...DEFAULT_MCP_TOOL,
    server: { ...DEFAULT_MCP_TOOL.server, ...(m.server ?? {}) },
    tools: Array.isArray(m.tools) ? m.tools : [],
    tool: m.tool ?? run?.target?.tool ?? null,
    arguments: { ...(m.arguments ?? {}) },
    side_effects:
      m.side_effects ??
      run?.config?.safety?.side_effects ??
      DEFAULT_MCP_TOOL.side_effects,
  };
}

/** The kind of an endpoint-shaped run: http, a2a or mcp_tool. */
const endpointKindOf = (run) => {
  const src = run?.target?.source ?? run?.config?.kind;
  return src === "a2a" || src === "mcp_tool" ? src : "http";
};

/**
 * Form values from GET experiments/v2/{id}/ (canonical `runs[]`).
 * mode "edit" keeps run ids so the PUT can tell kept, changed and new runs
 * apart; "duplicate" drops them and names the copy.
 * dataset integration FE-0 (owner Q12): a copy (Start from, Duplicate & edit) takes
 * only the runs whose source the picker offers (prompt, agent graph,
 * endpoint); `notCopied` counts the others. Edit keeps every run (they
 * exist; dropping one would delete it on save).
 */
export function valuesFromExperiment(detail, { mode = "edit" } = {}) {
  const keepIds = mode === "edit";
  const copyAll = keepIds;
  const trials = detail?.trials ?? 1;
  let notCopied = 0;
  const notCopiedLabels = [];
  const promptConfig = [];
  const promptCards = new Map();
  // L7b G2: a kept card is keyed by its run (stable across re-renders and
  // removals); each card remembers what its run ran with (`ranWith`, the
  // one run identity), so the confirm says exactly what an edit changes.
  const itemIdOf = (run) => (keepIds && run?.run_id) || getRandomId();
  const ranWithOf = (run) =>
    keepIds ? runIdentityOf(run, { trials }) ?? undefined : undefined;
  const skip = (run) => {
    notCopied += 1;
    if (run?.label) notCopiedLabels.push(run.label);
  };
  (detail?.runs ?? []).forEach((run) => {
    const t = run.target ?? {};
    const runId = keepIds ? run.run_id : undefined;
    if (run.target_type === "prompt") {
      const key = `${t.prompt_id}|${t.prompt_version_id}`;
      let card = promptCards.get(key);
      if (!card) {
        card = {
          _itemId: itemIdOf(run),
          experimentType: "llm",
          targetType: PROMPT_CONFIG_TYPE.PROMPT,
          promptId: t.prompt_id ?? null,
          promptVersion: t.prompt_version_id ?? null,
          versionLabel: t.prompt_version_name ?? "",
          name: t.prompt_name ?? run.name,
          model: [],
          modelParams: {},
          configuration: { toolChoice: "auto", tools: [] },
          outputFormat: "string",
          isDraft: false,
          variables: [],
          // L7b G2 (vars:kept-in-edit): the mapping the run ran with.
          variableMapping: mappingOfRun(run),
        };
        promptCards.set(key, card);
        promptConfig.push(card);
      }
      const params = (detail?.prompt_configs ?? []).find(
        (p) => p.model === t.model && p.prompt_version === t.prompt_version_id,
      )?.model_params;
      card.model.push({
        id: getRandomId(),
        value: t.model,
        ...providerFields(params),
        ...(runId && { runId, ranWith: ranWithOf(run) }),
      });
      // Each run keeps its own params (#2C·3 at temperature 0.7 copies 0.7).
      card.modelParams[escapeModelKey(t.model)] = { ...(t.model_params ?? {}) };
      return;
    }
    if (run.target_type === "agent" && !t.source) {
      promptConfig.push({
        _itemId: itemIdOf(run),
        experimentType: "llm",
        targetType: PROMPT_CONFIG_TYPE.AGENT,
        agentId: t.graph_id ?? null,
        agentVersion: t.version_id ?? null,
        versionLabel: t.version_name ?? "",
        name: t.graph_name ?? run.name,
        outputFormat: "string",
        isDraft: false,
        variables: [],
        variableMapping: mappingOfRun(run),
        ...(runId && { runId, ranWith: ranWithOf(run) }),
      });
      return;
    }
    // L2 G9: an R4 run comes back as its own R4 card (same setup).
    const r4 = r4DraftOfRun(run);
    if (r4) {
      if (!copyAll && !isOffered(r4.entryId)) {
        skip(run);
        return;
      }
      promptConfig.push({
        ...newR4Card(r4),
        _itemId: itemIdOf(run),
        ...(runId && { runId }),
      });
      return;
    }
    if (
      !copyAll &&
      ["code", "traces", "harness", "external", "voice", "agent"].includes(
        run.target_type,
      )
    ) {
      skip(run);
      return;
    }
    if (["code", "traces", "harness"].includes(run.target_type)) {
      const card = newRunCard(run.target_type);
      promptConfig.push({
        ...card,
        _itemId: itemIdOf(run),
        runName: run.name ?? "",
        ...(run.config?.code && { code: { ...card.code, ...run.config.code } }),
        ...(run.config?.traces && {
          traces: { ...card.traces, ...run.config.traces },
        }),
        ...(run.config?.harness && {
          harness_config: { ...card.harness_config, ...run.config.harness },
        }),
        ...(runId && { runId }),
      });
      return;
    }
    if (run.target_type === "external") {
      promptConfig.push({
        ...newRunCard(PROMPT_CONFIG_TYPE.EXTERNAL),
        _itemId: itemIdOf(run),
        runName: run.name ?? "",
        ...(runId && { runId }),
      });
      return;
    }
    if (run.target_type === "http") {
      // L7b G2 (edit:a2a-mcp-as-http): each endpoint-shaped run comes back
      // as its own card with what it ran: the stored endpoint (body,
      // output path, headers, transform), the A2A card and skill, or the
      // MCP server, tool and arguments; tries and cost cap; mapping.
      const kind = endpointKindOf(run);
      const limits = limitsOfRun(run, trials);
      const shared = {
        _itemId: itemIdOf(run),
        variableMapping: mappingOfRun(run),
        ...(limits ? { limits } : {}),
        ...(runId && { runId, ranWith: ranWithOf(run) }),
      };
      if (kind === "a2a") {
        promptConfig.push({
          ...newRunCard(PROMPT_CONFIG_TYPE.A2A),
          runName: run.name ?? "",
          a2a: a2aCardOfRun(run),
          ...shared,
        });
        return;
      }
      if (kind === "mcp_tool") {
        promptConfig.push({
          ...newRunCard(PROMPT_CONFIG_TYPE.MCP_TOOL),
          runName: run.name ?? "",
          mcp_tool: mcpCardOfRun(run),
          ...shared,
        });
        return;
      }
      promptConfig.push({
        ...newRunCard(PROMPT_CONFIG_TYPE.HTTP),
        http: httpCardOfRun(run),
        ...shared,
      });
      return;
    }
    // A run of a kind this form cannot rebuild stays a kept, read-only run
    // (Edit) and is named as not copied (Start from, Duplicate & edit).
    if (!copyAll) {
      skip(run);
      return;
    }
    promptConfig.push({
      ...newRunCard(PROMPT_CONFIG_TYPE.EXTERNAL),
      _itemId: itemIdOf(run),
      runName: run.name ?? "",
      readOnly: true,
      ...(runId && { runId }),
    });
  });
  const meta = Object.entries(detail?.metadata ?? {})
    .filter(([k]) => k !== "source")
    .map(([key, value]) => ({ key, value: String(value ?? "") }));
  const evals = (detail?.user_eval_metrics ?? []).map((item) => ({
    ...item,
    ...(keepIds && { actualEvalCreatedId: item.id }),
    evalId: item.template_id ?? item.id,
    templateId: item.template_id ?? item.template_details?.id,
    name: item.name,
    config: item.config ?? { mapping: item.mapping ?? {} },
    model: item.model ?? item.selected_model,
    // L4 (ver:pin-card): the version the eval runs when pinned (the detail
    // read's pinned_version_*), shown on the card as "V2 · pinned".
    ...(item.pinned_version_id && {
      pinnedVersionId: item.pinned_version_id,
      ...(item.pinned_version_number != null && {
        version: `V${item.pinned_version_number} · pinned`,
      }),
    }),
  }));
  return {
    ...emptyExperimentValues(),
    columnId: detail?.column_id ?? null,
    name:
      mode === "duplicate"
        ? `${detail?.name ?? ""} (copy)`
        : detail?.name ?? "",
    description: detail?.description ?? "",
    experimentType: detail?.experiment_type ?? "llm",
    promptConfig,
    userEvalMetrics: evals,
    datasetVersionId: detail?.dataset_version?.id ?? null,
    splitIds: (detail?.run_scope?.split_ids ?? detail?.splits ?? []).map((s) =>
      typeof s === "string" ? s : s?.id,
    ),
    trials: detail?.trials ?? 1,
    maxConcurrency: detail?.runs?.[0]?.concurrency ?? DEFAULT_CONCURRENCY,
    // v2.1 (knob:error-policy, knob:trial-reducer): Edit and Duplicate
    // show what the runs were scored with.
    ...storedScoringOf(detail?.runs),
    metadata: meta,
    notCopied,
    notCopiedLabels,
  };
}

/**
 * v2.1: the error policy and trial reducer an experiment's runs store
 * (`error_policy` zero | exclude, `trial_reducer` {kind, n?}), as form
 * values. No stored policy is null, the default: an errored try gets no
 * score. Mean is the default reducer (pure).
 * @param {object[]} runs  detail.runs (getRun shape)
 * @returns {{errorPolicy: "zero"|"exclude"|null, trialReducer: object|null}}
 */
export function storedScoringOf(runs = []) {
  const list = (runs ?? []).filter(Boolean);
  const reducer = list.map((r) => r.trial_reducer).find(Boolean) ?? null;
  const policies = list.map((r) => r.error_policy ?? r.config?.error_policy);
  return {
    errorPolicy: policies.includes("exclude")
      ? "exclude"
      : policies.includes("zero")
        ? "zero"
        : null,
    trialReducer: reducer ? parseReducer(reducer) : null,
  };
}

/**
 * options-truth L3 G8 P6: the dataset evals an earlier experiment was scored
 * with: its runs' metric keys, matched to R1 `metric_defs[].user_eval_id`.
 * Start from and Duplicate & edit copy these.
 * @param {{results?: object[], metric_defs?: object[]}} runConfigs  R1 result
 * @param {string} experimentId
 * @returns {string[]} dataset eval ids, in the leaderboard's order
 */
export function evalIdsOfExperiment(runConfigs, experimentId) {
  if (!experimentId) return [];
  const keys = new Set(
    (runConfigs?.results ?? [])
      .filter((r) => r?.experiment_id === experimentId)
      .flatMap((r) => r?.metric_keys ?? []),
  );
  return (runConfigs?.metric_defs ?? [])
    .filter((d) => d?.user_eval_id && keys.has(d.key))
    .map((d) => d.user_eval_id);
}

/** Metadata rows -> object (`source: ui` is added by the server). */
export function metadataObject(rows = []) {
  const out = {};
  rows.forEach(({ key, value }) => {
    const k = String(key ?? "").trim();
    if (k && k !== "source") out[k] = value ?? "";
  });
  return out;
}

/**
 * Keys the create body adds to the existing payload. Edit sends only the
 * editable ones (version, splits and trials are fixed after create).
 */
export function experimentExtras(values, { isEditing = false } = {}) {
  const extras = {
    name: String(values?.name ?? "").trim(),
    description: String(values?.description ?? "").trim(),
  };
  if (isEditing) return extras;
  return {
    ...extras,
    dataset_version_id: values?.datasetVersionId || undefined,
    split_ids: values?.splitIds ?? [],
    trials: Number(values?.trials ?? 1),
    max_concurrency: Number(values?.maxConcurrency ?? DEFAULT_CONCURRENCY),
    metadata: metadataObject(values?.metadata),
    // R4 (lane Ra, P5): the rows a door preselected (`These 6 rows`).
    ...(values?.rowIds?.length ? { row_ids: values.rowIds } : {}),
    // the interface contract: how errored tries score, sent only when the
    // person chose Count as 0 or Leave out (the default sends nothing: an
    // errored try gets no score and stays out of every average).
    ...(values?.errorPolicy === "zero" || values?.errorPolicy === "exclude"
      ? { error_policy: values.errorPolicy }
      : {}),
    // the interface contract (notify D40): a seeded random sample of the rows in scope (a
    // door's row selection wins).
    ...(sampleOfValues(values) ? { sample: sampleOfValues(values) } : {}),
    // the interface contract: how each row's tries become its value (Mean is the default).
    ...(trialReducerOfValues(values)
      ? { trial_reducer: trialReducerOfValues(values) }
      : {}),
    // RT-09 (W7): every run is recorded in this release; the body never
    // sends `record` (the server has no such field), whatever the form holds.
  };
}

/** The most tries any run of the form plays (its card's own, else Trials). */
export function maxTriesOfValues(values) {
  const trials = Math.max(1, Math.round(Number(values?.trials) || 1));
  return Math.max(
    trials,
    ...(values?.promptConfig ?? []).map((c) => runTriesOf(c, trials)),
  );
}

/**
 * the interface contract: the create body's `trial_reducer: {kind, n?}`, or null for Mean
 * (pure). Review S2: what is sent is what Review shows: nothing when no run
 * plays more than one try (the setting is hidden), and `At least n` never
 * above the tries a row has.
 */
export function trialReducerOfValues(values) {
  const r = values?.trialReducer;
  if (!r) return null;
  // The tries are known once the form carries Trials or a run card.
  const known =
    values?.trials != null || (values?.promptConfig ?? []).length > 0;
  const k = known ? maxTriesOfValues(values) : null;
  if (k != null && k <= 1) return null;
  const p = parseReducer(r);
  if (p.kind === "mean") return null;
  return p.kind === "at_least" && k != null ? { ...p, n: Math.min(k, p.n) } : p;
}

/** the interface contract: the create body's `sample: {n, seed}`, or null (pure). */
export function sampleOfValues(values) {
  const s = values?.sample;
  if (!s?.on || values?.rowIds?.length) return null;
  const n = Math.round(Number(s.n));
  if (!Number.isFinite(n) || n <= 0) return null;
  return { n, seed: Math.round(Number(s.seed)) || 0 };
}

/**
 * A dataset eval (get_evals_list item) as a preselected experiment eval.
 * The dataset's `output` input points at its own generated column; in an
 * experiment it reads each run's output.
 */
export function evalFromDatasetDefault(item, columns = []) {
  const outputIds = new Set(
    columns
      .filter(
        (c) =>
          ["run_prompt", "evaluation"].includes(
            String(c.origin_type).toLowerCase(),
          ) || c.role === "output",
      )
      .map((c) => c.id),
  );
  const mapping = Object.fromEntries(
    Object.entries(item?.mapping ?? item?.config?.mapping ?? {}).map(
      ([key, value]) => [
        key,
        key === "output" || outputIds.has(value) ? "output" : value,
      ],
    ),
  );
  return {
    evalId: item.template_id,
    templateId: item.template_id,
    datasetEvalId: item.id,
    name: item.name,
    evalTemplateName: item.template_name ?? item.name,
    mapping,
    model: item.model ?? item.run_config?.model,
    config: { ...(item.config ?? {}), mapping },
    outputType: item.output_type,
    higherIsBetter: item.higher_is_better ?? null,
    passThreshold: item.pass_threshold ?? null,
    version: item.version ?? null,
    isDefault: true,
  };
}

// Compare the same escaped model lookup and API parameter names the request
// builder sends, independent of editor aliases or object insertion order.
const paramsOfCard = (item, model) =>
  stable(requestModelParams(modelParamsFor(item?.modelParams, model)));

/**
 * L7b G2: the body entry one run of a card sends (pure; the same keys the
 * create / edit body carries), so the form reads its identity the way the
 * server does (runIdentity.runIdentityOf). Null for cards it does not model.
 */
export function entryOfCard(item, run = {}) {
  const type = item?.targetType;
  const mapping = item?.variableMapping ?? {};
  const variableMapping = Object.keys(mapping).length
    ? { variable_mapping: mapping }
    : {};
  if (type === PROMPT_CONFIG_TYPE.PROMPT) {
    const model = run?.model ?? item?.model?.[run?.modelIndex ?? 0]?.value;
    return {
      target_type: "prompt",
      prompt_id: item?.promptId ?? null,
      prompt_version: item?.promptVersion ?? null,
      model,
      model_params: paramsOfCard(item, model),
      ...promptToolsPayload(item),
      ...variableMapping,
    };
  }
  if (type === PROMPT_CONFIG_TYPE.AGENT)
    return {
      target_type: "agent",
      agent_id: item?.agentId ?? null,
      agent_version: item?.agentVersion ?? null,
      ...variableMapping,
    };
  if (
    ![
      PROMPT_CONFIG_TYPE.HTTP,
      PROMPT_CONFIG_TYPE.A2A,
      PROMPT_CONFIG_TYPE.MCP_TOOL,
    ].includes(type)
  )
    return null;
  const base = withCardExtras(r2RunPayload(item), item);
  const tries = Number(item?.limits?.tries) || null;
  const cap = Number(item?.limits?.maxCostUsd) || null;
  return {
    ...base,
    ...(type !== PROMPT_CONFIG_TYPE.HTTP ? variableMapping : {}),
    ...(tries || cap
      ? {
          limits: {
            ...(tries ? { tries } : {}),
            ...(cap ? { max_cost_usd: cap } : {}),
          },
        }
      : {}),
  };
}

/** What a kept card's run ran with (`ranWith`), per model on prompt cards. */
const ranWithOf = (run, item) =>
  run?.modelIndex != null
    ? item?.model?.[run.modelIndex]?.ranWith ?? null
    : item?.ranWith ?? null;

const targetKey = (run, item) => {
  const type = item?.targetType;
  if (type === PROMPT_CONFIG_TYPE.PROMPT)
    return JSON.stringify([
      item.promptVersion,
      run.model,
      paramsOfCard(item, run.model),
    ]);
  if (type === PROMPT_CONFIG_TYPE.AGENT)
    return JSON.stringify([item.agentVersion]);
  if (type === PROMPT_CONFIG_TYPE.HTTP)
    return JSON.stringify([item.http?.url, item.http?.method]);
  return JSON.stringify([item?.runName ?? ""]);
};

const runIdOf = (run, item) =>
  run.modelIndex != null
    ? item?.model?.[run.modelIndex]?.runId ?? null
    : item?.runId ?? null;

/**
 * A run's label in the edit confirm. A saved HTTP source read without the
 * run catalog has no label: name it by its saved name, never by its id.
 */
function runLabelOf(labelById, id, item) {
  if (labelById[id] != null) return labelById[id];
  const name =
    item?.targetType === PROMPT_CONFIG_TYPE.HTTP
      ? String(item?.name ?? "").trim()
      : "";
  return name || id;
}

const shown = (v) =>
  v == null || v === ""
    ? "none"
    : typeof v === "object"
      ? JSON.stringify(v)
      : String(v);

/** The model an R4 card runs (S5: harness model, params model, main model). */
const r4ModelOf = (item) => {
  const c = item?.r4?.config ?? {};
  return (
    c.harness?.model ??
    c.params?.values?.model ??
    c.engine?.model ??
    (c.models ?? []).find((m) => m?.role !== "helper")?.id ??
    item?.r4?.declared?.model ??
    null
  );
};

/**
 * options-truth L3 G8 P6: what changed on one kept run, in words
 * (`version v3 → v2`, `model gpt-4o → gpt-4.1`,
 * `params temperature 0.2 → 0.7`). Empty when nothing changed.
 * @param {{run: object, item: object}} a  before (flattened run + its card)
 * @param {{run: object, item: object}} b  after
 * @returns {string[]}
 */
export function runChangesOf(a, b) {
  const type = b.item?.targetType;
  const out = [];
  if (type === PROMPT_CONFIG_TYPE.PROMPT || type === PROMPT_CONFIG_TYPE.AGENT) {
    const prompt = type === PROMPT_CONFIG_TYPE.PROMPT;
    const vA = prompt ? a.item?.promptVersion : a.item?.agentVersion;
    const vB = prompt ? b.item?.promptVersion : b.item?.agentVersion;
    if ((vA ?? null) !== (vB ?? null))
      out.push(
        `version ${a.item?.versionLabel || shown(vA)} → ${b.item?.versionLabel || shown(vB)}`,
      );
  }
  if (type === PROMPT_CONFIG_TYPE.PROMPT) {
    const mA = a.run?.model ?? null;
    const mB = b.run?.model ?? null;
    if (mA !== mB) out.push(`model ${shown(mA)} → ${shown(mB)}`);
    const pA = paramsOfCard(a.item, mA);
    const pB = paramsOfCard(b.item, mB);
    const keys = [...new Set([...Object.keys(pA), ...Object.keys(pB)])].filter(
      (k) => JSON.stringify(pA[k] ?? null) !== JSON.stringify(pB[k] ?? null),
    );
    if (keys.length)
      out.push(
        `params ${keys.map((k) => `${k} ${shown(pA[k])} → ${shown(pB[k])}`).join(", ")}`,
      );
    return out;
  }
  if (type === PROMPT_CONFIG_TYPE.HTTP) {
    if ((a.item?.http?.url ?? "") !== (b.item?.http?.url ?? ""))
      out.push(`URL ${shown(a.item?.http?.url)} → ${shown(b.item?.http?.url)}`);
    if ((a.item?.http?.method ?? "") !== (b.item?.http?.method ?? ""))
      out.push(
        `method ${shown(a.item?.http?.method)} → ${shown(b.item?.http?.method)}`,
      );
    return out;
  }
  if (b.item?.r4 || a.item?.r4) {
    const eA = a.item?.r4?.entryName ?? a.item?.r4?.entryId ?? null;
    const eB = b.item?.r4?.entryName ?? b.item?.r4?.entryId ?? null;
    if (eA !== eB) out.push(`${shown(eA)} → ${shown(eB)}`);
    const mA = r4ModelOf(a.item);
    const mB = r4ModelOf(b.item);
    if (mA !== mB) out.push(`model ${shown(mA)} → ${shown(mB)}`);
    const wA = a.item?.r4?.whereName ?? a.item?.r4?.mode ?? null;
    const wB = b.item?.r4?.whereName ?? b.item?.r4?.mode ?? null;
    if (wA !== wB) out.push(`runs in ${shown(wA)} → ${shown(wB)}`);
    if (
      !out.length &&
      JSON.stringify(a.item?.r4?.config ?? null) !==
        JSON.stringify(b.item?.r4?.config ?? null)
    )
      out.push("setup changed");
  }
  const nA = a.item?.runName ?? "";
  const nB = b.item?.runName ?? "";
  if (nA !== nB) out.push(`name ${shown(nA)} → ${shown(nB)}`);
  return out;
}

/**
 * L7b G2 (edit:estimate): the cards an Edit save runs (re-run and new runs
 * only; kept runs cost nothing), for the one estimator. Prompt cards keep
 * only the models that run. Pure; `flatten` is runPlan.flattenRuns.
 * @returns {{promptConfig: object[], rerun: string[], added: string[], kept: string[]}}
 */
export function editRunScope(
  original,
  current,
  { runs = [], flatten, columns = [] },
) {
  const summary = editChangeSummary(original, current, {
    runs,
    flatten,
    columns,
  });
  const kept = new Set(summary.keptIds);
  const cards = current?.promptConfig ?? [];
  const runsOf = new Map();
  flatten(cards).forEach((r) => {
    const item = cards[r.configIndex];
    const id = runIdOf(r, item);
    if (id && kept.has(id)) return;
    const list = runsOf.get(r.configIndex) ?? [];
    list.push(r);
    runsOf.set(r.configIndex, list);
  });
  const promptConfig = cards
    .map((item, i) => {
      const list = runsOf.get(i);
      if (!list?.length) return null;
      if (!Array.isArray(item?.model) || list[0].modelIndex == null)
        return item;
      const keep = new Set(list.map((r) => r.modelIndex));
      return { ...item, model: item.model.filter((_, m) => keep.has(m)) };
    })
    .filter(Boolean);
  return {
    promptConfig,
    rerun: summary.rerun,
    added: summary.added,
    kept: summary.kept,
  };
}

/**
 * What one Edit save changes, for the confirm line
 * "Re-run: #4B (params changed) · New: #4D · Kept: #4A · Evaluations: + Tone".
 * `flatten` is runPlan.flattenRuns (passed in to keep this module pure of it).
 * `keptIds` are the kept runs' ids; `httpChanged` the changed HTTP sources.
 * @returns {{rerun, added, kept, keptIds, removed, httpChanged, evalsAdded,
 *   evalsRemoved, text}}
 */
export function editChangeSummary(
  original,
  current,
  { runs = [], flatten, columns = [] },
) {
  const labelById = Object.fromEntries(runs.map((r) => [r.run_id, r.label]));
  const trials = original?.trials ?? current?.trials ?? 1;
  const ordinal = runs[0]?.experiment_ordinal;
  const before = new Map();
  flatten(original?.promptConfig ?? []).forEach((r) => {
    const item = original.promptConfig[r.configIndex];
    const id = runIdOf(r, item);
    if (id) before.set(id, { key: targetKey(r, item), run: r, item });
  });
  const used = runs.map((r) => r.letter ?? "A").sort();
  let next = used.length ? used[used.length - 1].charCodeAt(0) + 1 : 65;
  const out = {
    rerun: [],
    added: [],
    kept: [],
    keptIds: [],
    removed: [],
    changes: {},
    // Changed saved HTTP sources (an HTTP Edit saves them; nothing runs).
    httpChanged: [],
  };
  const seen = new Set();
  flatten(current?.promptConfig ?? []).forEach((r) => {
    const item = current.promptConfig[r.configIndex];
    const id = runIdOf(r, item);
    if (id && before.has(id)) {
      seen.add(id);
      const was = before.get(id);
      const label = runLabelOf(labelById, id, was.item);
      const http = was.item?.targetType === PROMPT_CONFIG_TYPE.HTTP;
      const keep = () => {
        out.kept.push(label);
        out.keptIds.push(id);
      };
      const rerun = (words) => {
        out.rerun.push(label);
        out.changes[label] = words.length ? words : ["setup changed"];
        if (http) out.httpChanged.push(label);
      };
      // L7b G2: a card that knows what its run ran with compares the one
      // run identity (the server's); others compare their own targets.
      const ran = ranWithOf(was.run, was.item);
      const entry = ran ? entryOfCard(item, r) : null;
      if (ran && entry) {
        const [ranAs, after] = forCompare(
          ran,
          runIdentityOf(entry, { trials }),
          { columns },
        );
        const promptWords =
          item?.targetType === PROMPT_CONFIG_TYPE.PROMPT ||
          item?.targetType === PROMPT_CONFIG_TYPE.AGENT
            ? runChangesOf(was, { run: r, item })
            : [];
        const words = [
          ...promptWords,
          ...runIdentityChanges(ranAs, after, {
            skip: promptWords.length ? ["prompt", "agent"] : [],
          }).filter((w) => !promptWords.includes(w)),
        ];
        if (sameRunIdentity(ranAs, after) && !promptWords.length) keep();
        else rerun(words);
        return;
      }
      const what = runChangesOf(was, { run: r, item });
      if (was.key === targetKey(r, item) && !what.length) keep();
      else rerun(what);
      return;
    }
    out.added.push(`#${ordinal ?? ""}${String.fromCharCode(next)}`);
    next += 1;
  });
  [...before.keys()]
    .filter((id) => !seen.has(id))
    .forEach((id) =>
      out.removed.push(runLabelOf(labelById, id, before.get(id).item)),
    );
  const names = (list) => (list ?? []).map((e) => e?.name).filter(Boolean);
  const was = names(original?.userEvalMetrics);
  const now = names(current?.userEvalMetrics);
  out.evalsAdded = now.filter((n) => !was.includes(n));
  out.evalsRemoved = was.filter((n) => !now.includes(n));
  const parts = [];
  if (out.rerun.length)
    parts.push(
      `Re-run: ${out.rerun.map((l) => `${l} (${out.changes[l].join("; ")})`).join(", ")}`,
    );
  if (out.added.length) parts.push(`New: ${out.added.join(", ")}`);
  if (out.removed.length) parts.push(`Removed: ${out.removed.join(", ")}`);
  if (out.kept.length) parts.push(`Kept: ${out.kept.join(", ")}`);
  const evals = [
    ...out.evalsAdded.map((n) => `+ ${n}`),
    ...out.evalsRemoved.map((n) => `− ${n}`),
  ];
  if (evals.length) parts.push(`Evaluations: ${evals.join(", ")}`);
  out.text = parts.join(" · ");
  return out;
}
