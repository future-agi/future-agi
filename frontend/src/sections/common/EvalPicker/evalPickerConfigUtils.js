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

  return nextConfig;
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
      aggregation_enabled:
        compositeDetail?.aggregation_enabled ?? fullEval?.aggregation_enabled ?? true,
      aggregation_function:
        compositeDetail?.aggregation_function
        || fullEval?.aggregation_function
        || "weighted_avg",
      composite_child_axis:
        compositeDetail?.composite_child_axis || fullEval?.composite_child_axis || "",
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
