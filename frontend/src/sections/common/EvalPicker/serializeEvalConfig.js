// Translate the camelCase output of EvalPickerConfigFull.handleAdd into the
// snake_case payload accepted by simulate/run-tests/eval-configs/update and
// the simulate add endpoint. RUN_CONFIG_KEYS mirrors the BE's
//
// Each runtime override is emitted in two places:
//   1. Inside `config.run_config.*` — canonical location consumed by the BE's
//      normalize_eval_runtime_config + the simulation runner (xl.py:741-786).
//   2. Mirrored at the payload root — picked up by EvalPickerConfigFull's
//      init-effect fallbacks (`evalData?.<key>`) on edit-reopen, so the
//      prefill renders the user's saved choice instead of the template
//      default whenever the BE round-trips fields at the top level.
// `error_localizer_enabled` rides in run_config too (alongside the top-level
// `error_localizer: bool`), so `config.error_localizer_enabled` resolves on
// edit-reopen without a per-drawer rehydration patch.
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

export function serializeEvalConfig(evalConfig) {

  const runConfig = {};
  for (const k of RUN_CONFIG_KEYS) {
    if (evalConfig[k] !== undefined) runConfig[k] = evalConfig[k];
  }
  if (evalConfig.error_localizer_enabled !== undefined) {
    runConfig.error_localizer_enabled = !!evalConfig.error_localizer_enabled;
  }
  return {
   ...runConfig,
    template_id: evalConfig.templateId,
    name: evalConfig.name,
    model: evalConfig.model,
    mapping: evalConfig.mapping || {},
    config: {
      ...(evalConfig.config || {}),
      run_config: {
        ...(evalConfig.config?.run_config || {}),
        ...runConfig,
      },
    },
    error_localizer: !!evalConfig.error_localizer_enabled,
    filters: evalConfig.filters || {},
  };
}
