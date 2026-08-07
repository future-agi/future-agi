/**
 * Build the `run_config` object hosts nest under `config.run_config`.
 *
 * EvalPickerConfigFull emits runtime toggles as top-level siblings on the
 * save payload; the host (EvaluationDrawer, experiment wizard, etc.) is
 * responsible for nesting them. Keep this in one place so those surfaces
 * cannot drift.
 */
export const buildEvalRunConfig = (evalConfig, { isComposite } = {}) => {
  const composite =
    isComposite ?? evalConfig?.templateType === "composite";
  const runConfig = {};

  if (!composite) {
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

/** Picker emits snake; some hosts historically wrote camel — accept both. */
export const resolveCompositeWeightOverrides = (evalConfig) =>
  evalConfig?.compositeWeightOverrides ??
  evalConfig?.composite_weight_overrides ??
  null;
