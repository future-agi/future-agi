import isEqual from "lodash/isEqual";
import isPlainObject from "lodash/isPlainObject";
import juryBindingPayload from "src/sections/evals/jury/juryBindingPayload";

// The raw binding wins over the list endpoint's lossy run_config view (which
// collapses custom summaries and supplies defaults for absent overrides).
export const bindingRunConfigOf = (data) =>
  data?.storedBinding?.config?.run_config ??
  data?.config?.run_config ??
  data?.run_config ??
  data?.runConfig ??
  data?.config?.runConfig ??
  {};

export const bindingContextOptions = (injection = {}) => {
  if (!injection || typeof injection !== "object") return ["variables_only"];
  const options = [];
  if (injection.full_row || injection.fullRow) options.push("dataset_row");
  for (const key of [
    "span_context",
    "trace_context",
    "session_context",
    "call_context",
  ]) {
    if (injection[key]) options.push(key);
  }
  return options.length ? options : ["variables_only"];
};

// One serializer for the Update comparison AND EvaluationDrawer's actual
// request. Transient run/experiment routing flags are added by the host.
export function buildBindingSavePayload(selection) {
  if (selection.bindingPayload) return selection.bindingPayload;
  const composite = selection.templateType === "composite";
  const runConfig = { ...selection.run_config };
  if (!composite) {
    for (const key of [
      "model",
      "agent_mode",
      "summary",
      "knowledge_base_id",
      "knowledge_bases",
      "tools",
    ]) {
      if (selection[key]) runConfig[key] = selection[key];
    }
    for (const key of ["check_internet", "multi_choice"]) {
      if (selection[key] !== undefined) runConfig[key] = !!selection[key];
    }
    if (selection.pass_threshold !== undefined)
      runConfig.pass_threshold = selection.pass_threshold;
    if (
      selection.choice_scores &&
      Object.keys(selection.choice_scores).length
    ) {
      runConfig.choice_scores = selection.choice_scores;
    }
  }
  if (selection.data_injection)
    runConfig.data_injection = selection.data_injection;
  if (selection.error_localizer_enabled !== undefined) {
    runConfig.error_localizer_enabled = !!selection.error_localizer_enabled;
  }
  const params =
    selection.params && typeof selection.params === "object"
      ? selection.params
      : {};
  const weights =
    selection.composite_weight_overrides ?? selection.compositeWeightOverrides;
  const payload = {
    name: selection.name,
    template_id: selection.templateId,
    model: composite ? undefined : selection.model,
    error_localizer: runConfig.error_localizer_enabled ?? false,
    config: {
      mapping: selection.mapping || {},
      config: composite ? {} : selection.config || {},
      ...(Object.keys(params).length ? { params } : {}),
      ...(Object.keys(runConfig).length ? { run_config: runConfig } : {}),
    },
    ...(composite && weights ? { composite_weight_overrides: weights } : {}),
    ...(selection.versionId !== undefined
      ? { pinned_version_id: selection.versionId }
      : {}),
  };
  juryBindingPayload(payload, selection);
  return payload;
}

export function storedBindingPayload(data, initial) {
  if (data.storedBinding) return data.storedBinding;
  const config =
    data.config?.config !== undefined
      ? data.config
      : {
          config: data.bindingConfig ?? data.binding_config ?? {},
          run_config: bindingRunConfigOf(data),
          ...(data.params ? { params: data.params } : {}),
        };
  return {
    template_id: initial.template_id,
    name: data.name ?? initial.name,
    model: data.model ?? data.selectedModel ?? initial.model,
    error_localizer:
      data.error_localizer ??
      bindingRunConfigOf(data).error_localizer_enabled ??
      false,
    pinned_version_id:
      data.pinned_version_id ??
      data.pinnedVersionId ??
      initial.pinned_version_id,
    config: { ...config, mapping: data.mapping ?? config.mapping ?? {} },
    ...(data.composite_weight_overrides !== undefined
      ? { composite_weight_overrides: data.composite_weight_overrides }
      : {}),
  };
}

// Apply only changes from the hydrated editor to the exact stored JSON. The
// editor cannot represent every server value (custom summary text, disabled
// connectors, absent defaults, opaque config keys). Re-serializing untouched
// controls must not silently rewrite those values. Arrays are atomic; maps
// retain unknown keys, but deletions of displayed keys are real edits.
function applyEditorChanges(stored, initial, current, depth = 0) {
  if (isEqual(initial, current)) return stored;
  if (!isPlainObject(initial) || !isPlainObject(current)) return current;
  // config and run_config are sparse containers, but an object-valued setting
  // inside them replaces the inherited object at runtime. Seed that setting
  // with all its effective keys, then retain opaque stored keys and apply edits.
  const next = {
    ...(depth >= 3 ? initial : {}),
    ...(isPlainObject(stored) ? stored : {}),
  };
  for (const key of new Set([
    ...Object.keys(initial),
    ...Object.keys(current),
  ])) {
    if (isEqual(initial[key], current[key])) continue;
    if (!Object.hasOwn(current, key)) delete next[key];
    else
      next[key] = applyEditorChanges(
        stored?.[key],
        initial[key],
        current[key],
        depth + 1,
      );
  }
  return next;
}

// Compare the effective settings, not which container happened to serialize
// them. Save version can move runtime values into the selected base snapshot.
const versionSettings = (payload, baseConfig) => {
  const config = { ...baseConfig, ...flatVersionConfig(payload) };
  delete config.version_pin;
  delete config.pass_threshold;
  return { config, params: payload.config?.params };
};

// The legacy BooleanField and the JSON flag independently enable localization
// on the server. Keep hydration separate from the exact JSON used for no-op
// comparison, including a persisted false overriding the template default.
export function bindingErrorLocalizerOf(data, fallback = false) {
  const flag = data?.storedBinding?.error_localizer ?? data?.error_localizer;
  const runFlag = bindingRunConfigOf(data).error_localizer_enabled;
  return flag !== undefined
    ? Boolean(flag || runFlag)
    : Boolean(runFlag ?? fallback);
}

const flatVersionConfig = (payload) => ({
  ...payload.config?.config,
  ...payload.config?.run_config,
  ...(payload.model !== undefined ? { model: payload.model } : {}),
});

// A version is a complete selected definition, unlike an ordinary binding
// update. Preserve opaque effective values (e.g. custom summaries) rather than
// replacing them with the editor's lossy representation of unchanged controls.
export function buildBindingVersionConfig(stored, initial, draft, baseConfig) {
  const initialConfig = flatVersionConfig(initial);
  const effective = {
    ...initialConfig,
    ...baseConfig,
    ...flatVersionConfig(stored),
    ...(stored.error_localizer !== undefined
      ? {
          error_localizer_enabled: Boolean(
            stored.error_localizer ||
              stored.config?.run_config?.error_localizer_enabled,
          ),
        }
      : {}),
  };
  const config = {
    ...applyEditorChanges(
      effective,
      initialConfig,
      flatVersionConfig(draft),
      2,
    ),
  };
  delete config.version_pin;
  return config;
}

// Project the serialized editor onto the stored wire payload. Opaque settings
// stay exact, but represented settings have ONE effective value regardless of
// whether an older caller stored them in config.config or config.run_config.
// The latter wins on the server, so editing a definition must update it too.
function prospectiveBindingPayload(stored, initial, draft) {
  const next = { ...stored, config: { ...stored.config } };
  for (const key of new Set([...Object.keys(initial), ...Object.keys(draft)])) {
    if (key === "config" || isEqual(initial[key], draft[key])) continue;
    if (Object.hasOwn(draft, key)) next[key] = draft[key];
    else delete next[key];
  }
  for (const key of new Set([
    ...Object.keys(initial.config || {}),
    ...Object.keys(draft.config || {}),
  ])) {
    if (["config", "run_config"].includes(key)) continue;
    if (isEqual(initial.config?.[key], draft.config?.[key])) continue;
    // Empty params/mapping are explicit replacements, not inheritance.
    next.config[key] = draft.config?.[key] ?? {};
  }
  const before = flatVersionConfig(initial);
  const after = flatVersionConfig(draft);
  const effectiveStored = flatVersionConfig(stored);
  for (const key of new Set([...Object.keys(before), ...Object.keys(after)])) {
    if (isEqual(before[key], after[key])) continue;
    // Clearing an inherited collection must survive the server's overlay.
    const value = Object.hasOwn(after, key)
      ? after[key]
      : Array.isArray(before[key])
        ? []
        : isPlainObject(before[key])
          ? {}
          : null;
    const effective = applyEditorChanges(
      effectiveStored[key],
      before[key],
      value,
      3,
    );
    for (const container of ["config", "run_config"]) {
      if (
        Object.hasOwn(draft.config?.[container] || {}, key) ||
        Object.hasOwn(initial.config?.[container] || {}, key) ||
        Object.hasOwn(stored.config?.[container] || {}, key)
      ) {
        next.config[container] = {
          ...next.config[container],
          [key]: effective,
        };
      }
    }
  }
  return next;
}

export function reconcileBindingSave(
  stored,
  initial,
  draft,
  isSystemEval,
  baseConfig,
  savedVersion,
  storedVersionConfig,
) {
  const baseline = JSON.parse(
    JSON.stringify(prospectiveBindingPayload(stored, initial, initial)),
  );
  let payload = prospectiveBindingPayload(stored, initial, draft);
  // Save version changes the selected definition, but not the persisted
  // binding. Keep those two baselines separate until Update succeeds.
  const definitionDraft = savedVersion?.draft ?? initial;
  const definitionConfig = savedVersion?.config ?? baseConfig;
  if (
    !isSystemEval &&
    draft.pinned_version_id === definitionDraft.pinned_version_id &&
    !isEqual(
      versionSettings(definitionDraft, definitionConfig),
      versionSettings(draft, definitionConfig),
    )
  ) {
    payload = {
      ...payload,
      config: {
        ...payload.config,
        config: buildBindingVersionConfig(
          savedVersion ? { config: { config: savedVersion.config } } : stored,
          definitionDraft,
          draft,
          definitionConfig,
        ),
      },
    };
    delete payload.pinned_version_id;
  }
  // Compare JSON values actually sent by handleAdd: Save version's local
  // snapshot may contain undefined fields that the wire request omits.
  payload = JSON.parse(JSON.stringify(payload));
  const retainedPinPayload = {
    ...payload,
    pinned_version_id: baseline.pinned_version_id,
  };
  if (
    baseline.pinned_version_id &&
    !Object.hasOwn(payload, "pinned_version_id") &&
    storedVersionConfig &&
    isEqual(baseline, retainedPinPayload)
  ) {
    const storedSnapshot = JSON.parse(JSON.stringify(storedVersionConfig));
    // Omission asks the server to deduplicate, not clear the pin. Only treat
    // it as retention when both the complete persisted row AND the immutable
    // pinned snapshot stay exact. A mismatched historical snapshot must still
    // take the implicit-version path, even when its binding JSON is unchanged.
    if (
      isEqual(
        { ...storedSnapshot, ...flatVersionConfig(payload) },
        storedSnapshot,
      )
    ) {
      payload = retainedPinPayload;
    }
  }
  return { payload, changed: !isEqual(baseline, payload) };
}
