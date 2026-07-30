/**
 * Build the `run_config` block persisted on UserEvalMetric.config from the
 * EvalPicker's save payload. Extracted from EvaluationDrawer's handleAdd so
 * the key whitelist is unit-testable — keys not forwarded here are silently
 * dropped before the API call, which is exactly the class of bug this
 * whitelist has a habit of producing.
 *
 * Composite bindings keep only the keys that apply at the binding level
 * (children each carry their own model/mode/tools).
 */
export function buildRunConfig(evalConfig, { isComposite }) {
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
    if (evalConfig.choice_scores && Object.keys(evalConfig.choice_scores).length)
      runConfig.choice_scores = evalConfig.choice_scores;
    if (evalConfig.multi_choice !== undefined)
      runConfig.multi_choice = !!evalConfig.multi_choice;
    // Judge model generation params (temperature / max_tokens / …). Only
    // forwarded when non-empty so an untouched picker never writes partial
    // values into run_config.
    if (evalConfig.model_params && Object.keys(evalConfig.model_params).length)
      runConfig.model_params = evalConfig.model_params;
  }
  // Data injection applies to both single and composite — the backend
  // resolves it at row-evaluation time.
  if (evalConfig.data_injection)
    runConfig.data_injection = evalConfig.data_injection;
  // Error localizer toggle was previously dropped between
  // EvalPickerConfigFull and the backend. It now flows through for both
  // single and composite bindings.
  if (evalConfig.error_localizer_enabled !== undefined)
    runConfig.error_localizer_enabled = !!evalConfig.error_localizer_enabled;
  return runConfig;
}
