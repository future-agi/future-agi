// Translate the camelCase output of EvalPickerConfigFull.handleAdd into the
// snake_case payload accepted by simulate/run-tests/eval-configs/update and
// the simulate add endpoint. RUN_CONFIG_KEYS mirrors the BE's
//
// Runtime overrides are emitted only in `config.run_config.*`, which is the
// backend contract consumed by normalize_eval_runtime_config and the simulation
// runner. Edit-reopen flows should read from that canonical location instead
// of depending on duplicate top-level payload keys.
import { isClearedMappingValue } from "src/sections/evals/utils/evalMappingPath";
import { isDecisionModel } from "src/sections/evals/utils/decisionModels";

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
  "jev_mapping",
];

const DECISION_RUN_CONFIG_KEYS = [
  ...RUN_CONFIG_KEYS,
  "output_type",
  "choices",
  "choice_scores",
  "multi_choice",
  "reverse_output",
  "knowledge_base_id",
  "messages",
  "few_shot_examples",
  "ground_truth_few_shot",
  "input_data_types",
];

// Hosts with their own payload envelopes must preserve typed overrides without
// forwarding arbitrary template config (prompts, auth, or evaluator internals).
export function getDecisionRunConfigOverrides(evalConfig) {
  if (!isDecisionModel(evalConfig.model)) return {};
  const runConfig = {};
  for (const source of [
    evalConfig.config,
    evalConfig.config?.run_config,
    evalConfig,
  ]) {
    for (const key of DECISION_RUN_CONFIG_KEYS) {
      if (source?.[key] !== undefined) runConfig[key] = source[key];
    }
  }
  return runConfig;
}

// A cleared auto-mapped field reaches us as "" — the eval runner treats a
// present-but-empty path as a required attribute it can never resolve and
// fails every row, while an absent key falls through to context injection.
// `isClearedMappingValue` is imported rather than restated so this stays one
// definition of what a mapping value may be.
export function sanitizeEvalMapping(mapping) {
  return Object.fromEntries(
    Object.entries(mapping || {}).filter(
      ([, path]) => !isClearedMappingValue(path),
    ),
  );
}

export function serializeEvalConfig(evalConfig) {
  const runConfig = getDecisionRunConfigOverrides(evalConfig);
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
