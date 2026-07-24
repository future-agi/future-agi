import { buildCompositeChildConfigs } from "src/sections/evals/Helpers/compositeRuntimeConfig";

const OUTPUT_TYPE_CONFIG_MAP = {
  pass_fail: "Pass/Fail",
  percentage: "score",
  deterministic: "choices",
};

const ROW_TYPE_CONTEXT_OPTIONS = {
  spans: ["span_context"],
  traces: ["trace_context"],
  sessions: ["session_context"],
  voiceCalls: ["call_context"],
};

export const contextOptionsForRowType = (rowType) =>
  ROW_TYPE_CONTEXT_OPTIONS[rowType] || null;

// Maps the UI's contextOptions array to the BE's data_injection flag dict.
// Any non-default option must land as its own flag — collapsing every
// non-default selection to { full_row: true } silently drops span / trace /
// session / call context choices.
export const buildDataInjection = (contextOptions = []) => {
  if (
    contextOptions.length === 0 ||
    (contextOptions.length === 1 && contextOptions[0] === "variables_only")
  ) {
    return { variables_only: true };
  }
  const flags = {};
  if (contextOptions.includes("dataset_row")) flags.full_row = true;
  if (contextOptions.includes("full_row")) flags.full_row = true;
  if (contextOptions.includes("span_context")) flags.span_context = true;
  if (contextOptions.includes("trace_context")) flags.trace_context = true;
  if (contextOptions.includes("session_context")) flags.session_context = true;
  if (contextOptions.includes("call_context")) flags.call_context = true;
  return Object.keys(flags).length > 0 ? flags : { full_row: true };
};

export { extractCodeEvaluateParams } from "src/utils/codeEvalParams";

export const hasNonEmptyPromptMessage = (messages = []) =>
  messages.some((message) => {
    if (!["system", "user"].includes(message?.role)) return false;

    const normalizedContent = String(message?.content || "")
      .replace(/<[^>]*>/g, " ")
      .replace(/&nbsp;/gi, " ")
      .trim();

    return normalizedContent.length > 0;
  });

export const buildEvalTemplateConfig = ({
  baseConfig = {},
  evalType,
  instructions,
  code,
  codeLanguage,
  messages = [],
  fewShotExamples = [],
  outputType,
  passThreshold,
  choiceScores,
  multiChoice,
  templateFormat,
}) => {
  const nextConfig = {
    ...baseConfig,
    rule_prompt: evalType === "code" ? "" : instructions,
    output: OUTPUT_TYPE_CONFIG_MAP[outputType] || baseConfig?.output,
    pass_threshold: passThreshold,
    template_format: templateFormat,
  };

  if (evalType === "code") {
    nextConfig.code = code;
    nextConfig.language = codeLanguage;
  }

  if (evalType === "llm") {
    nextConfig.messages = messages;

    if (fewShotExamples.length > 0) {
      nextConfig.few_shot_examples = fewShotExamples;
    } else {
      delete nextConfig.few_shot_examples;
    }
  }

  if (choiceScores && Object.keys(choiceScores).length > 0) {
    nextConfig.choice_scores = choiceScores;
  } else {
    delete nextConfig.choice_scores;
  }

  // Persist multi_choice into the runtime config so the dataset eval snapshot
  // (UserEvalMetric.config) carries it — the feedback get_template endpoint
  // reads multi_choice from that snapshot, not the template.
  if (multiChoice !== undefined) {
    nextConfig.multi_choice = Boolean(multiChoice);
  }

  return nextConfig;
};

// Build the nested `{ mapping, config, run_config, params }` payload the
// experiment endpoints expect (matches the shape EvaluationDrawer sends to
// /edit_and_run_user_eval). Kept here so both the creation wizard and the
// Manage-Evaluations drawer emit an identical shape — otherwise runtime
// overrides picked in the drawer (agent_mode, tools, summary, …) never
// reach `UserEvalMetric.config.run_config` and the pinned version snapshot
// ends up equal to the template default.
export const buildExperimentEvalRuntimePayload = (evalConfig, mapping) => {
  const isComposite = evalConfig.templateType === "composite";
  const templateConfig =
    evalConfig.config || evalConfig.evalTemplate?.config || {};
  const runConfig = {};
  if (!isComposite) {
    if (evalConfig.model) runConfig.model = evalConfig.model;
    if (evalConfig.agent_mode) runConfig.agent_mode = evalConfig.agent_mode;
    if (evalConfig.check_internet !== undefined)
      runConfig.check_internet = !!evalConfig.check_internet;
    if (evalConfig.summary) runConfig.summary = evalConfig.summary;
    if (evalConfig.knowledge_base_id)
      runConfig.knowledge_base_id = evalConfig.knowledge_base_id;
    if (evalConfig.knowledge_bases)
      runConfig.knowledge_bases = evalConfig.knowledge_bases;
    if (evalConfig.tools) runConfig.tools = evalConfig.tools;
    if (evalConfig.pass_threshold !== undefined)
      runConfig.pass_threshold = evalConfig.pass_threshold;
    if (
      evalConfig.choice_scores &&
      Object.keys(evalConfig.choice_scores).length
    )
      runConfig.choice_scores = evalConfig.choice_scores;
    if (evalConfig.multi_choice !== undefined)
      runConfig.multi_choice = !!evalConfig.multi_choice;
  }
  if (evalConfig.data_injection)
    runConfig.data_injection = evalConfig.data_injection;
  if (evalConfig.error_localizer_enabled !== undefined)
    runConfig.error_localizer_enabled = !!evalConfig.error_localizer_enabled;
  const evalParams =
    evalConfig.params && typeof evalConfig.params === "object"
      ? evalConfig.params
      : {};
  return {
    mapping,
    config: isComposite ? {} : templateConfig,
    ...(Object.keys(evalParams).length ? { params: evalParams } : {}),
    ...(Object.keys(runConfig).length ? { run_config: runConfig } : {}),
  };
};

export const buildCompositeSourceModeProps = ({
  isComposite,
  fullEval,
  compositeDetail,
  compositeChildWeights = {},
}) => {
  if (!isComposite) {
    return { isComposite: false };
  }

  const children = Array.isArray(compositeDetail?.children)
    ? compositeDetail.children
    : [];

  if (children.length === 0) {
    return { isComposite: true };
  }

  const baseWeights = children.reduce((acc, child) => {
    if (child?.child_id) {
      acc[child.child_id] = child.weight ?? 1;
    }
    return acc;
  }, {});

  const mergedWeights = {
    ...baseWeights,
    ...(compositeChildWeights || {}),
  };

  return {
    isComposite: true,
    compositeAdhocConfig: {
      child_template_ids: children.map((child) => child.child_id),
      child_configs: buildCompositeChildConfigs(children),
      aggregation_enabled:
        compositeDetail?.aggregation_enabled ??
        fullEval?.aggregation_enabled ??
        true,
      aggregation_function:
        compositeDetail?.aggregation_function ||
        fullEval?.aggregation_function ||
        "weighted_avg",
      composite_child_axis:
        compositeDetail?.composite_child_axis ||
        fullEval?.composite_child_axis ||
        "",
      child_weights:
        Object.keys(mergedWeights).length > 0 ? mergedWeights : null,
      pass_threshold:
        compositeDetail?.pass_threshold ?? fullEval?.pass_threshold ?? 0.5,
    },
  };
};

export const getSourceModeVariables = ({
  isComposite,
  variables = [],
  compositeUnionKeys = [],
}) => (isComposite ? compositeUnionKeys : variables);
