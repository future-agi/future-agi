export function buildCompositeRuntimeConfig({
  config = {},
  codeParams = {},
} = {}) {
  const runtimeConfig =
    config && typeof config === "object" ? { ...config } : {};
  const existingParams =
    runtimeConfig.params && typeof runtimeConfig.params === "object"
      ? runtimeConfig.params
      : {};
  const explicitParams =
    codeParams && typeof codeParams === "object" ? codeParams : {};

  const mergedParams = {
    ...existingParams,
    ...explicitParams,
  };

  if (Object.keys(mergedParams).length > 0) {
    runtimeConfig.params = mergedParams;
  } else {
    delete runtimeConfig.params;
  }

  return runtimeConfig;
}

const CHILD_RUN_CONFIG_KEYS = [
  "model",
  "pass_threshold",
  "error_localizer_enabled",
  "check_internet",
  "agent_mode",
  "summary",
  "tools",
  "knowledge_bases",
  "data_injection",
];

export function buildCompositeChildRunConfig(evalMeta) {
  const source = evalMeta || {};
  const nested =
    source.config?.run_config && typeof source.config.run_config === "object"
      ? source.config.run_config
      : {};

  const runConfig = {};
  for (const key of CHILD_RUN_CONFIG_KEYS) {
    const value = source[key] ?? nested[key];
    if (value === undefined || value === null) continue;
    if (Array.isArray(value) && value.length === 0) continue;
    if (
      !Array.isArray(value) &&
      typeof value === "object" &&
      Object.keys(value).length === 0
    ) {
      continue;
    }
    runConfig[key] = value;
  }

  if (!runConfig.model) {
    const fallbackModel = source.config?.model || source.evalTemplate?.model;
    if (fallbackModel) runConfig.model = fallbackModel;
  }

  return runConfig;
}

export function buildCompositeChildConfigs(children = []) {
  return (children || []).reduce((acc, child) => {
    const childId = child?.child_id || child?.id;
    if (!childId) return acc;

    const existingConfig =
      child?.config && typeof child.config === "object" ? child.config : {};
    const params =
      child?.params && typeof child.params === "object"
        ? child.params
        : existingConfig?.params;
    const nextConfig = { ...existingConfig };

    if (
      params &&
      typeof params === "object" &&
      Object.keys(params).length > 0
    ) {
      nextConfig.params = params;
    }

    if (Object.keys(nextConfig).length > 0) {
      acc[childId] = nextConfig;
    }

    return acc;
  }, {});
}
