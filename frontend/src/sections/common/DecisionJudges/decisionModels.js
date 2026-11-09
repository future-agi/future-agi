import { formatExactMoney } from "src/sections/common/format/money";
import {
  PROVIDERS,
  PRICES,
  RESOLVED,
} from "src/test-fixtures/build-data/decisionPreset";

const PROTOTYPE_BUILD =
  import.meta.env.VITE_ENVIRONMENT === "local" &&
  import.meta.env.VITE_PROTOTYPE_MODE === "true";
const INCLUDE_PREVIEW = PROTOTYPE_BUILD || import.meta.env.MODE === "test";

// Decision-model judges (PR #3289 parity; parity plan the capability contract, the interface contract).
// One pure module the evaluator picker, Test on one row and the mock world's
// scorer share: the decision models a workspace can use, their prices, the
// two wire formats and the gateway's envelope and route checks.
//
// Sources (read-only, origin/feat/th-8183-jev-evaluator-models):
//   agentcc-gateway/internal/modeldb/decision_models.go   prices
//   agentcc-gateway/internal/server/handlers_decision.go  GET /v1/decisions/models
//   agentcc-gateway/internal/models/decision.go           envelope checks
//   api_contracts/gateway/decision-{providers,cases}.json presets, codes
//   futureagi/ee/jev/mapping.py, validation.py            question mapping
// Policy: third-party decision models run only on the customer's own key
// (BYOK). Future AGI holds no decision key and a decision call draws no
// Future AGI credits; the vendor bills the customer's key directly.

/** Price keys live under "decision:" (modeldb DecisionKeyPrefix). */
export const DECISION_PRICE_PREFIX = "decision:";

/** System One: questions is an object keyed by question id. */
export const FORMAT_SYSTEM_ONE = "systemone";
/** OpenAI Decisions: questions is an array. */
export const FORMAT_OPENAI = "openai";

export const FORMAT_LABEL = {
  [FORMAT_SYSTEM_ONE]: "System One",
  [FORMAT_OPENAI]: "OpenAI Decisions",
};

/**
 * The providers that serve decision models, keyed by the provider id the
 * workspace's key is saved under (Settings > AI Providers). `models` is the
 * preset's fixed list (decision-providers.json saves); OpenAI Decisions
 * use the workspace's existing OpenAI key.
 */
export const DECISION_PROVIDERS = {
  typesafe: {
    name: "TypeSafe",
    format: FORMAT_SYSTEM_ONE,
    models: ["jev-1.13.0", "jev-latest"],
  },
  perplexity: {
    name: "Perplexity Decider",
    keyName: "Perplexity",
    format: FORMAT_SYSTEM_ONE,
    models: ["pplx-decider-v1.1-27b", "pplx-decider-v1-27b"],
  },
  ...(INCLUDE_PREVIEW ? PROVIDERS : {}),
};

/**
 * decision_models.go: USD per million input tokens. Every decision model
 * bills nothing for output.
 */
export const DECISION_PRICES = {
  "jev-1.13.0": 0.042,
  "jev-1.14.0": 0.042,
  "jev-latest": 0.042,
  "pplx-decider-v1.1-27b": 0.02,
  "pplx-decider-v1-27b": 0.02,
  ...(INCLUDE_PREVIEW ? PRICES : {}),
};

/** The model a vendor reports having used (jev-latest tracks the release). */
export const RESOLVED_MODEL = {
  "jev-latest": "jev-1.14.0",
  ...(INCLUDE_PREVIEW ? RESOLVED : {}),
};

const bareOf = (id) => {
  const s = String(id ?? "");
  const i = s.lastIndexOf("/");
  return i >= 0 ? s.slice(i + 1) : s;
};

/** "typesafe/jev-1.13.0" -> {provider, model, format, price} or null. */
export function decisionModelOf(id) {
  if (typeof id !== "string" || !id) return null;
  const raw = id.startsWith(DECISION_PRICE_PREFIX)
    ? id.slice(DECISION_PRICE_PREFIX.length)
    : id;
  const slash = raw.indexOf("/");
  const prefix = slash > 0 ? raw.slice(0, slash) : null;
  const model = bareOf(raw);
  const provider =
    prefix && DECISION_PROVIDERS[prefix]
      ? prefix
      : Object.keys(DECISION_PROVIDERS).find((p) =>
          DECISION_PROVIDERS[p].models.includes(model),
        );
  // the interface contract (should-fix 7): a model the listing returns beyond the preset
  // (jev-1.14.0) is a decision model too when its provider is one Future
  // AGI routes and decision_models.go prices it.
  if (
    !provider ||
    !(
      DECISION_PROVIDERS[provider].models.includes(model) ||
      (prefix === provider && DECISION_PRICES[model] != null)
    )
  )
    return null;
  return {
    id: `${provider}/${model}`,
    provider,
    providerName: DECISION_PROVIDERS[provider].name,
    keyName:
      DECISION_PROVIDERS[provider].keyName ?? DECISION_PROVIDERS[provider].name,
    model,
    format: DECISION_PROVIDERS[provider].format,
    inputPerMTok: DECISION_PRICES[model] ?? null,
    outputPerMTok: 0,
    priceKey: `${DECISION_PRICE_PREFIX}${model}`,
  };
}

export const isDecisionModel = (id) => decisionModelOf(id) != null;

/**
 * GET /v1/decisions/models for a workspace: only the models of providers it
 * holds a key for (handlers_decision.go orgDecisionModels), sorted by id.
 * @param {Iterable<string>} keyedProviders provider ids with a saved key
 */
export function listDecisionModels(keyedProviders = []) {
  const keyed = new Set(keyedProviders);
  const data = [];
  Object.entries(DECISION_PROVIDERS).forEach(([provider, p]) => {
    if (!keyed.has(provider)) return;
    p.models.forEach((model) => {
      const price = DECISION_PRICES[model];
      data.push({
        id: `${provider}/${model}`,
        object: "model",
        provider,
        format: p.format,
        ...(price != null
          ? { pricing: { input_per_mtok: price, output_per_mtok: 0 } }
          : {}),
      });
    });
  });
  data.sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  return { object: "list", data };
}

/** "$0.042 / 1M input tokens · output free" */
export function priceLabel(pricing) {
  if (!pricing || pricing.input_per_mtok == null) return "No listed price";
  const out = Number(pricing.output_per_mtok ?? 0);
  return `$${pricing.input_per_mtok} / 1M input tokens · ${
    out ? `$${out} / 1M output` : "output free"
  }`;
}

/**
 * The picker's rows: every preset decision model. A model the listing
 * returned is ready; one whose provider has no key in the workspace is
 * still shown (never hidden, never faked) with the way to add the key.
 * @param {{data?: object[]}} listing GET /v1/decisions/models body
 */
export function pickerRowsOf(listing) {
  const listed = new Map((listing?.data ?? []).map((m) => [m.id, m]));
  const rows = [];
  const presetIds = new Set(
    Object.entries(DECISION_PROVIDERS).flatMap(([provider, p]) =>
      p.models.map((model) => `${provider}/${model}`),
    ),
  );
  Object.entries(DECISION_PROVIDERS).forEach(([provider, p]) => {
    p.models.forEach((model) => {
      const id = `${provider}/${model}`;
      const hit = listed.get(id);
      const price = DECISION_PRICES[model];
      const pricing =
        hit?.pricing ??
        (price != null ? { input_per_mtok: price, output_per_mtok: 0 } : null);
      rows.push({
        id,
        provider,
        providerName: p.name,
        model,
        format: hit?.format ?? p.format,
        formatLabel: FORMAT_LABEL[hit?.format ?? p.format],
        pricing,
        priceText: priceLabel(pricing),
        ready: Boolean(hit),
        keyHint: hit
          ? `Runs on your ${p.keyName ?? p.name} key`
          : `Add your ${p.keyName ?? p.name} key in Settings > AI Providers`,
      });
    });
  });
  // the interface contract (should-fix 7): the union with the listing: a listed model the
  // preset lacks shows too, when Future AGI can route it (decisionModelOf).
  (listing?.data ?? []).forEach((m) => {
    if (presetIds.has(m.id)) return;
    const dm = decisionModelOf(m.id);
    if (!dm) return;
    const p = DECISION_PROVIDERS[dm.provider];
    const pricing =
      m.pricing ??
      (dm.inputPerMTok != null
        ? { input_per_mtok: dm.inputPerMTok, output_per_mtok: 0 }
        : null);
    rows.push({
      id: dm.id,
      provider: dm.provider,
      providerName: p.name,
      model: dm.model,
      format: m.format ?? dm.format,
      formatLabel: FORMAT_LABEL[m.format ?? dm.format],
      pricing,
      priceText: priceLabel(pricing),
      ready: true,
      keyHint: `Runs on your ${p.keyName ?? p.name} key`,
    });
  });
  return rows;
}

// ---------------------------------------------------------------- mapping

/** The eval output type in one spelling: pass_fail | choices | score. */
export function outputKindOf(outputType) {
  const t = String(outputType ?? "").toLowerCase();
  if (/pass|fail/.test(t)) return "pass_fail";
  if (/choice|determin/.test(t)) return "choices";
  return "score";
}

/**
 * The question type an output type asks in a format, or {error}.
 * System One: noul (yes/no probability), choice, score.
 * OpenAI Decisions (the gateway contract's cases): predicate, choice.
 */
export function questionTypeOf(outputType, format) {
  const kind = outputKindOf(outputType);
  if (format === FORMAT_OPENAI) {
    if (kind === "pass_fail") return { type: "predicate" };
    if (kind === "choices") return { type: "choice" };
    return {
      error:
        "OpenAI Decisions asks predicate and choice questions; a score needs a System One model (TypeSafe or Perplexity Decider).",
    };
  }
  return {
    type: { pass_fail: "noul", choices: "choice", score: "score" }[kind],
  };
}

/**
 * The decision mapping an eval starts with: from its output type and (for
 * choices) its choice scores. The editor changes it. The question's
 * instructions are not part of it: they are the eval's own prompt, rendered
 * per row (mapping.py build_question), so the mapping carries none. Score
 * levels start empty: the author writes them (JEV_SCORE_LEVELS_REQUIRED).
 */
export function defaultDecisionConfig({
  model,
  outputType,
  choiceScores = null,
} = {}) {
  const dm = decisionModelOf(model);
  const format = dm?.format ?? FORMAT_SYSTEM_ONE;
  const kind = outputKindOf(outputType);
  const labels = Object.keys(choiceScores ?? {});
  return {
    revision: "jev-map-v1",
    model: dm?.id ?? model ?? null,
    format,
    route: "/v1/decisions",
    question_type: questionTypeOf(outputType, format).type ?? null,
    pass: { criteria_true: "", criteria_false: "" },
    choice: {
      labels,
      descriptions: Object.fromEntries(labels.map((l) => [l, ""])),
      scores: Object.fromEntries(
        labels.map((l, i) => [
          l,
          Number.isFinite(Number(choiceScores?.[l]))
            ? Number(choiceScores[l])
            : labels.length > 1
              ? 1 - i / (labels.length - 1)
              : 1,
        ]),
      ),
    },
    score: { levels: [] },
    output_kind: kind,
  };
}

/** A saved mapping without the instructions an earlier draft carried. */
export function withoutInstructions(config) {
  if (!config || typeof config !== "object" || !("instructions" in config))
    return config;
  const { instructions: _drop, ...rest } = config;
  return rest;
}

/** Instructions without HTML tags and with spaces collapsed. */
export function stripTemplate(text) {
  return String(text ?? "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/[ \t]+/g, " ")
    .trim();
}

/**
 * The question's instructions with the row's values filled in (mapping.py
 * build_question takes the rendered prompt); a variable the row does not
 * carry stays as written.
 */
export function renderInstructions(text, vars = {}) {
  return stripTemplate(text).replace(/{{\s*([\w.-]+)\s*}}/g, (all, name) => {
    const v = vars[name];
    if (v == null) return all;
    return typeof v === "string" ? v : JSON.stringify(v);
  });
}

/** The config re-pointed at another model (format and question follow). */
export function withModel(config, model) {
  const dm = decisionModelOf(model);
  if (!dm) return config;
  const format = dm.format;
  return {
    ...config,
    model: dm.id,
    format,
    question_type: questionTypeOf(config.output_kind, format).type ?? null,
  };
}

const MEDIA = /^(image|images|audio|video|pdf|file|binary)$/i;
const hasEntries = (v) =>
  Array.isArray(v)
    ? v.length > 0
    : v && typeof v === "object"
      ? Object.values(v).some(Boolean)
      : Boolean(v);

/** "Jev" for TypeSafe's models, else the provider's name. */
const familyOf = (dm) =>
  dm?.provider === "typesafe" ? "Jev" : dm?.providerName ?? "Decision";

/**
 * The options a decision call cannot use, as mapping.py refuses them
 * (JEV_*_UNSUPPORTED) in the PR's words (jevMapping.js
 * getJevValidationErrors): the eval keeps them, Save and Test are blocked
 * until they are off. `ctx` is the editor's state.
 */
export function unsupportedOptionsOf(config, ctx = {}) {
  const dm = decisionModelOf(config?.model);
  const family = `${familyOf(dm)} models`;
  const out = [];
  const add = (feature, code) =>
    out.push({
      code,
      message: `${feature} is not supported with ${family} (${code}). Nothing was changed or removed from your template.`,
    });
  if (ctx.multiChoice || config?.multi_choice)
    add("Multi-choice", "JEV_MULTI_CHOICE_UNSUPPORTED");
  if (hasEntries(ctx.tools)) add("Tools", "JEV_TOOLS_UNSUPPORTED");
  if (hasEntries(ctx.knowledgeBases))
    add("Knowledge bases", "JEV_KNOWLEDGE_BASE_UNSUPPORTED");
  if (ctx.internet) add("Internet check", "JEV_INTERNET_UNSUPPORTED");
  if (
    Object.values(ctx.inputDataTypes ?? {}).some((t) => MEDIA.test(String(t)))
  )
    add("Media inputs", "JEV_INPUT_UNSUPPORTED");
  // agent_mode drives only an agent eval (mapping.py); quick mode is one call.
  if (ctx.evalType === "agent" && ctx.agentMode && ctx.agentMode !== "quick")
    add(`Agent mode (${ctx.agentMode})`, "JEV_AGENT_MODE_UNSUPPORTED");
  if (hasEntries(ctx.fewShotExamples))
    add("Few-shot examples", "JEV_FEW_SHOT_UNSUPPORTED");
  if ((ctx.contextOptions ?? []).some((o) => o && o !== "variables_only"))
    add("Automatic context", "JEV_DATA_INJECTION_UNSUPPORTED");
  return out;
}

/**
 * Why the question itself cannot be asked (mapping.py validate_jev_mapping
 * and build_question; the gateway's route rule for the format), as
 * [{code, message}]. `ctx.instructions`, when given, is the eval's prompt:
 * the question's instructions are that prompt, so it must hold text.
 */
export function questionIssuesOf(config, ctx = {}) {
  const issues = [];
  const add = (code, message) => issues.push({ code, message });
  if (!config)
    return [{ code: "JEV_MAPPING_INVALID", message: "Pick a decision model." }];
  const dm = decisionModelOf(config.model);
  if (!dm) add("model_not_found", "Pick a decision model.");
  const family = familyOf(dm);
  const format = dm?.format ?? config.format;
  const q = questionTypeOf(config.output_kind, format);
  if (q.error) add("JEV_MAPPING_INVALID", q.error);
  if ("instructions" in ctx && !stripTemplate(ctx.instructions))
    add(
      "JEV_MAPPING_INVALID",
      `${family} instructions must contain text: write the eval's prompt (it is the question's instructions).`,
    );
  if (config.route === "/v1/systemone" && format === FORMAT_OPENAI)
    add(
      "format_mismatch",
      "422 format_mismatch: /v1/systemone takes System One bodies only; send OpenAI Decisions to /v1/decisions.",
    );
  if (q.type === "choice") {
    const labels = config.choice?.labels ?? [];
    if (
      !labels.length ||
      labels.length > 255 ||
      labels.some((l) => !String(l).trim() || String(l).length > 200) ||
      new Set(labels.map((l) => String(l).trim().toLowerCase())).size !==
        labels.length
    )
      add(
        "JEV_CHOICE_LABELS_INVALID",
        `${family} single choice requires 1 to 255 unique, non-empty labels.`,
      );
    if (
      Object.values(config.choice?.descriptions ?? {}).some(
        (d) => d != null && String(d).length > 2000,
      )
    )
      add(
        "JEV_MAPPING_INVALID",
        `${family} descriptions must be at most 2000 characters.`,
      );
  }
  // the interface contract (should-fix 11, PR jevMapping rules).
  if (q.type === "score" && Object.keys(config.choice?.scores ?? {}).length)
    add(
      "JEV_MAPPING_INVALID",
      "Jev numeric rubrics cannot use choice scores (JEV_MAPPING_INVALID).",
    );
  if (
    (ctx.messages ?? []).some((m) => m && m.role && m.role !== "system") &&
    !config.include_messages
  )
    add(
      "JEV_MESSAGES_REQUIRE_CONFIRMATION",
      "The eval's messages other than the system prompt go to the decision model only when you confirm: tick Include messages (JEV_MESSAGES_REQUIRE_CONFIRMATION).",
    );
  if (q.type === "score") {
    const levels = config.score?.levels ?? [];
    if (!levels.length)
      add(
        "JEV_SCORE_LEVELS_REQUIRED",
        `${family} score requires explicit ordered rubric levels: write 2 to 10, lowest first (JEV_SCORE_LEVELS_REQUIRED).`,
      );
    else if (
      levels.length < 2 ||
      levels.length > 10 ||
      levels.some((l) => !String(l).trim() || String(l).length > 2000) ||
      new Set(levels.map((l) => String(l).trim())).size !== levels.length
    )
      add(
        "JEV_SCORE_LEVELS_INVALID",
        `${family} rubric score requires 2 to 10 ordered levels, each written and none repeated (JEV_SCORE_LEVELS_INVALID).`,
      );
  }
  if (
    q.type === "noul" || q.type === "predicate"
      ? ["criteria_true", "criteria_false"].some(
          (k) => String(config.pass?.[k] ?? "").length > 2000,
        )
      : false
  )
    add(
      "JEV_MAPPING_INVALID",
      `${family} descriptions must be at most 2000 characters.`,
    );
  return issues;
}

/**
 * Why a decision judge cannot be saved or tested, or []: the options the
 * call cannot use, then the question's own problems.
 */
export function decisionConfigErrors(config, ctx = {}) {
  if (!config) return ["Pick a decision model."];
  return [
    ...new Set(
      [
        ...unsupportedOptionsOf(config, ctx),
        ...questionIssuesOf(config, ctx),
      ].map((i) => i.message),
    ),
  ];
}

/**
 * The backend's 400 for a mapping it cannot ask (before any gateway call),
 * or null: what a run or a Test answers for a judge saved before a rule.
 */
export function decisionMappingError(config, ctx = {}) {
  const issue = questionIssuesOf(config, ctx).find(
    (i) => i.code !== "format_mismatch",
  );
  return issue
    ? { status: 400, code: issue.code, message: issue.message }
    : null;
}

// ---------------------------------------------------------------- wire

/** mapping.py build_state: one variable is the state itself, else an object. */
export function buildState(vars = {}) {
  const parse = (v) => {
    if (typeof v !== "string") return v;
    try {
      const p = JSON.parse(v);
      if (p && typeof p === "object") return p;
    } catch {
      /* plain text */
    }
    return v;
  };
  const entries = Object.entries(vars).filter(([, v]) => v != null);
  if (entries.length === 1) return parse(entries[0][1]);
  return Object.fromEntries(entries.map(([k, v]) => [k, parse(v)]));
}

/**
 * The question's instructions for one row (evaluator.py, mapping.py
 * build_question): the eval's own prompt rendered with the row's values,
 * then a line naming the state's variables. One part is a string, more
 * are a list (System One); OpenAI Decisions takes one string.
 */
export function questionInstructionsOf(prompt, vars = {}, format) {
  const parts = [renderInstructions(prompt, vars)];
  const names = Object.keys(vars).filter((k) => vars[k] != null);
  if (names.length)
    parts.push(`The state contains the input variables: ${names.join(", ")}.`);
  if (format === FORMAT_OPENAI) return parts.join("\n\n");
  return parts.length === 1 ? parts[0] : parts;
}

/** A question's instructions as one text (a list joined). */
export const instructionsText = (instructions) =>
  Array.isArray(instructions)
    ? instructions.map((p) => String(p ?? "")).join("\n")
    : String(instructions ?? "");

/**
 * The decision body for one row in the model's own format. `instructions`
 * is the eval's prompt (the mapping carries none of its own).
 * System One: {model, state, questions: {q1: {type, instructions, criteria}}}
 * OpenAI Decisions: {model, input, questions: [{type, name, instructions, choices}]}
 */
export function buildDecisionBody(config, vars = {}, { instructions } = {}) {
  const dm = decisionModelOf(config.model);
  const format = dm?.format ?? config.format;
  const type =
    questionTypeOf(config.output_kind, format).type ?? config.question_type;
  const state = buildState(vars);
  const instructionsOut = questionInstructionsOf(
    instructions ?? "",
    vars,
    format,
  );
  const labels = config.choice?.labels ?? [];
  if (format === FORMAT_OPENAI) {
    const q = { type, name: "q1", instructions: instructionsOut };
    if (type === "choice")
      q.choices = labels.map((l) => {
        const d = config.choice?.descriptions?.[l];
        return d ? { value: l, description: d } : { value: l };
      });
    // the interface contract (should-fix 10): the OpenAI Decisions question is type, name,
    // instructions and choices; a predicate carries no criteria.
    return { model: config.model, input: state, questions: [q] };
  }
  const q = { type, instructions: instructionsOut };
  if (type === "noul") {
    const ct = config.pass?.criteria_true;
    const cf = config.pass?.criteria_false;
    if (ct || cf)
      q.criteria = {
        ...(ct ? { true: ct } : {}),
        ...(cf ? { false: cf } : {}),
      };
  } else if (type === "choice") {
    q.criteria = Object.fromEntries(
      labels.map((l) => [l, config.choice?.descriptions?.[l] || null]),
    );
  } else if (type === "score") {
    q.criteria = [...(config.score?.levels ?? [])];
  }
  return { model: config.model, state, questions: { q1: q } };
}

const apiError = (status, code, message) => ({ status, code, message });

/**
 * decision.go ParseDecisionRequest: the envelope only. Returns
 * {format, model, question_ids, question_types} or {error}.
 */
export function parseDecisionRequest(body) {
  let obj = body;
  if (typeof body === "string") {
    try {
      obj = JSON.parse(body);
    } catch {
      return {
        error: apiError(
          400,
          "invalid_json",
          "request body must be a single JSON object",
        ),
      };
    }
  }
  if (!obj || typeof obj !== "object" || Array.isArray(obj))
    return {
      error: apiError(
        400,
        "invalid_json",
        "request body must be a single JSON object",
      ),
    };
  if (typeof obj.model !== "string" || !obj.model.trim())
    return {
      error: apiError(
        400,
        "missing_model",
        "model is required and must be a non-empty string",
      ),
    };
  const qs = obj.questions;
  const quote = (id) => JSON.stringify(String(id).slice(0, 64));
  if (qs && typeof qs === "object" && !Array.isArray(qs)) {
    const ids = Object.keys(qs);
    if (!ids.length)
      return {
        error: apiError(
          400,
          "missing_questions",
          "questions is required: an object keyed by question id (System One) or an array (OpenAI Decisions)",
        ),
      };
    if (obj.state == null)
      return {
        error: apiError(
          400,
          "missing_state",
          "state is required for a System One request",
        ),
      };
    const types = [];
    for (const id of ids) {
      const q = qs[id];
      if (!q || typeof q !== "object" || Array.isArray(q))
        return {
          error: apiError(
            400,
            "invalid_question",
            `question ${quote(id)}: must be an object`,
          ),
        };
      if (typeof q.type !== "string")
        return {
          error: apiError(
            400,
            "invalid_question",
            `question ${quote(id)}: type is required and must be a string`,
          ),
        };
      types.push(q.type);
    }
    return {
      format: FORMAT_SYSTEM_ONE,
      model: obj.model,
      question_ids: ids,
      question_types: types,
    };
  }
  if (Array.isArray(qs)) {
    if (!qs.length)
      return {
        error: apiError(
          400,
          "missing_questions",
          "questions is required: an object keyed by question id (System One) or an array (OpenAI Decisions)",
        ),
      };
    const ids = [];
    const types = [];
    for (let i = 0; i < qs.length; i += 1) {
      const q = qs[i];
      const id = q && typeof q.name === "string" && q.name ? q.name : `#${i}`;
      if (!q || typeof q !== "object" || typeof q.type !== "string")
        return {
          error: apiError(
            400,
            "invalid_question",
            `question ${quote(id)}: type is required and must be a string`,
          ),
        };
      ids.push(id);
      types.push(q.type);
    }
    return {
      format: FORMAT_OPENAI,
      model: obj.model,
      question_ids: ids,
      question_types: types,
    };
  }
  return {
    error: apiError(
      400,
      "missing_questions",
      "questions is required: an object keyed by question id (System One) or an array (OpenAI Decisions)",
    ),
  };
}

/**
 * The gateway's refusals before any vendor is called (handlers_decision.go,
 * decision-cases.json gateway_error_cases): the route's format, the
 * workspace's key, the provider's format. Returns {error} or
 * {format, provider, upstream_model, question_ids, question_types}.
 * @param {{route: string, body: object|string, keyedProviders: Iterable<string>}} a
 */
export function routeDecision({
  route = "/v1/decisions",
  body,
  keyedProviders,
}) {
  const parsed = parseDecisionRequest(body);
  if (parsed.error) return parsed;
  if (route === "/v1/systemone" && parsed.format !== FORMAT_SYSTEM_ONE)
    return {
      error: apiError(
        422,
        "format_mismatch",
        "questions is an array, which is the OpenAI Decisions format: send it to /v1/decisions. /v1/systemone takes questions as an object keyed by question id.",
      ),
    };
  const dm = decisionModelOf(parsed.model);
  if (!dm)
    return {
      error: apiError(
        404,
        "model_not_found",
        `No decision provider in this workspace lists ${parsed.model}.`,
      ),
    };
  const keyed = new Set(keyedProviders ?? []);
  if (!keyed.has(dm.provider))
    return {
      error: apiError(
        403,
        "decision_provider_not_configured",
        `No ${dm.keyName} key in this workspace. Decision models run only on your own key: add your ${dm.keyName} key in Settings > AI Providers.`,
      ),
    };
  if (parsed.format !== dm.format)
    return {
      error: apiError(
        422,
        "format_mismatch",
        `${dm.providerName} speaks ${FORMAT_LABEL[dm.format]}, and this body is ${FORMAT_LABEL[parsed.format]} (questions as ${
          parsed.format === FORMAT_OPENAI ? "an array" : "an object"
        }). Nothing is translated: send the provider's own format.`,
      ),
    };
  return {
    format: parsed.format,
    provider: dm.provider,
    upstream_model: dm.model,
    question_ids: parsed.question_ids,
    question_types: parsed.question_types,
  };
}

/**
 * the interface contract (should-fix 5): the code PR #3289's eval path raises for a key
 * refusal: JEV_KEY_MISSING before any gateway call (no key saved),
 * JEV_KEY_INVALID when the vendor rejects the saved key (401/403 from the
 * vendor). Gateway routes keep their own codes; null for anything else.
 */
export function evalPathCodeOf(error) {
  if (!error) return null;
  if (error.code === "decision_provider_not_configured")
    return "JEV_KEY_MISSING";
  if (error.code === "JEV_KEY_INVALID") return "JEV_KEY_INVALID";
  return null;
}

/** The PR's info line on a Jev (System One) decision judge. */
export const JEV_INFO_LINE =
  "Jev returns typed decisions with probabilities; no written reasoning.";

/**
 * The short reason a refused decision cell shows ("Skipped · no TypeSafe
 * key"): the gateway's refusal in a few words.
 */
export function refusalLabelOf(error, model) {
  if (!error) return "not scored";
  const dm = decisionModelOf(model);
  if (error.code === "decision_provider_not_configured")
    return `no ${dm?.keyName ?? "provider"} key (${error.status})`;
  if (error.code === "format_mismatch")
    return `format mismatch (${error.status})`;
  return `${error.code} (${error.status})`;
}

/** USD of one call at decision:<model> per-token pricing. */
export function decisionCostUsd(model, usage = {}) {
  const dm = decisionModelOf(model);
  if (!dm || dm.inputPerMTok == null) return null;
  const usd =
    (Number(usage.input_tokens ?? 0) * dm.inputPerMTok +
      Number(usage.output_tokens ?? 0) * dm.outputPerMTok) /
    1e6;
  return Math.round(usd * 1e10) / 1e10;
}

/** "$0.0000214" style: decision calls cost fractions of a cent. */
export function formatDecisionUsd(usd) {
  return formatExactMoney(usd, {
    minimumFractionDigits:
      Number(usd) === 0 ? 0 : Math.abs(Number(usd)) >= 0.01 ? 4 : 0,
  });
}
