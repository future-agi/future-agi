// L7b G8 (`Open in Prompts with this row ↗`): the router state a run door
// hands the workbench, and how the workbench applies it. Pure.
//   state = {prefillVariables: {name: value}, prefillModel: "gpt-4o-mini",
//            prefillParams: {temperature: 0.3, max_tokens: 400}}
// The workbench reads it once at mount (a later navigate that writes the
// selected versions into the URL drops location.state) and applies it after
// the ?version= restore, so the row's values, the run's model and its
// params win over the draft's saved ones for what the state names.
import { normalizeConfigurationForLoad } from "./common";

const isObject = (v) => v != null && typeof v === "object" && !Array.isArray(v);

/** The prefill a router state carries, or null when it carries none. */
export function prefillOf(state) {
  const variables = isObject(state?.prefillVariables)
    ? state.prefillVariables
    : null;
  const model =
    typeof state?.prefillModel === "string" && state.prefillModel.trim()
      ? state.prefillModel
      : null;
  const params = isObject(state?.prefillParams) ? state.prefillParams : null;
  if (!variables && !model && !params) return null;
  return { variables, model, params };
}

/**
 * The variable values with the prefill applied: each variable the prompt
 * has (a key of `current`) and the prefill names takes the prefill's value
 * (the workbench keeps one value per variable as `[value]`). Variables the
 * prompt does not have are ignored.
 */
export function withPrefilledVariables(current, variables) {
  const base = isObject(current) ? current : {};
  if (!isObject(variables)) return base;
  const next = { ...base };
  Object.entries(variables).forEach(([name, value]) => {
    if (!(name in base) || value == null) return;
    next[name] = [typeof value === "string" ? value : String(value)];
  });
  return next;
}

/**
 * One model config with the prefill applied: the run's model (its name in
 * `model` and `model_detail.model_name`; a different model drops the old
 * model's logo and provider) and its params (snake_case keys normalised the
 * way a saved configuration loads).
 */
export function withPrefilledModel(config, { model, params } = {}) {
  let next = { ...(config ?? {}) };
  if (model && model !== next.model) {
    const { modelName: _old, ...detail } = next.model_detail ?? {};
    next = {
      ...next,
      model,
      model_detail: {
        ...detail,
        model_name: model,
        logo_url: "",
        providers: "",
        is_available: true,
      },
    };
  }
  if (isObject(params)) {
    const loaded = normalizeConfigurationForLoad({ ...params }) ?? {};
    next = { ...next, ...loaded };
  }
  return next;
}

/**
 * The state a prompt run's door hands the workbench (with the one prompt
 * link, RunSettings/targetLinks.promptLinkOf): the model and params the run
 * ran (its R1 config, else its target) and, from a row door, that row's
 * variables. Run details and the leaderboard ⋯ pass no variables.
 */
export function promptDoorState(run, config, variables) {
  const cfg = config ?? run?.config ?? {};
  const target = run?.target ?? {};
  const model = cfg.model?.id ?? target.model ?? null;
  const params = cfg.model?.params ?? target.model_params ?? null;
  return {
    ...(isObject(variables) ? { prefillVariables: variables } : {}),
    ...(model ? { prefillModel: model } : {}),
    ...(isObject(params) ? { prefillParams: { ...params } } : {}),
  };
}
