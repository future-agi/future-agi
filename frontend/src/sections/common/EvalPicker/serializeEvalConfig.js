// Translate the camelCase output of EvalPickerConfigFull.handleAdd into the
// snake_case payload accepted by simulate/run-tests/eval-configs/update and
// the simulate add endpoint. RUN_CONFIG_KEYS mirrors the BE's
//
// Runtime overrides are emitted only in `config.run_config.*`, which is the
// backend contract consumed by normalize_eval_runtime_config and the simulation
// runner. Edit-reopen flows should read from that canonical location instead
// of depending on duplicate top-level payload keys.
const RUN_CONFIG_KEYS = [
  "model",
  "agent_mode",
  "check_internet",
  "summary",
  "tools",
  "knowledge_bases",
  "mcp_connectors",
  "data_injection",
  "pass_threshold",
  "params",
];

// A cleared auto-mapped field reaches us as "" — the eval runner treats a
// present-but-empty path as a required attribute it can never resolve and
// fails every row, while an absent key falls through to context injection.
export function sanitizeEvalMapping(mapping) {
  return Object.fromEntries(
    Object.entries(mapping || {}).filter(
      ([, path]) =>
        path != null && !(typeof path === "string" && path.trim() === ""),
    ),
  );
}

export const isCompositeEval = (evalConfig) =>
  evalConfig.templateType === "composite" ||
  evalConfig.evalTemplate?.template_type === "composite" ||
  evalConfig.evalTemplate?.templateType === "composite";

export const buildRunConfig = (evalConfig) => {
  const runConfig = {};
  if (!isCompositeEval(evalConfig)) {
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
  return runConfig;
};

export function serializeEvalConfig(evalConfig) {
  const runConfig = {};
  for (const k of RUN_CONFIG_KEYS) {
    if (evalConfig[k] !== undefined) runConfig[k] = evalConfig[k];
  }
  if (evalConfig.error_localizer_enabled !== undefined) {
    runConfig.error_localizer_enabled = !!evalConfig.error_localizer_enabled;
  }
  return {
    template_id: evalConfig.templateId,
    name: evalConfig.name,
    model: evalConfig.model,
    mapping: sanitizeEvalMapping(evalConfig.mapping),
    config: {
      ...(evalConfig.config || {}),
      // BE looks up function-param values at `config.params` (normalize_eval_runtime_config).
      ...(evalConfig.params !== undefined && { params: evalConfig.params }),
      run_config: {
        ...(evalConfig.config?.run_config || {}),
        ...runConfig,
      },
    },
    error_localizer: !!evalConfig.error_localizer_enabled,
    filters: evalConfig.filters || [],
  };
}
