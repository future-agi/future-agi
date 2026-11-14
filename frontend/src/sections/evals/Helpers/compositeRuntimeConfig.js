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

const camelizeKey = (key) =>
  key.replace(/_([a-z])/g, (_, char) => char.toUpperCase());

const readRunConfigKey = (source, nested, key) => {
  const camelKey = camelizeKey(key);
  return (
    source[key] ?? source[camelKey] ?? nested[key] ?? nested[camelKey] ?? null
  );
};

const isEmptyCollection = (value) => {
  if (Array.isArray(value)) return value.length === 0;
  return typeof value === "object" && Object.keys(value).length === 0;
};

export function buildCompositeChildRunConfig(evalMeta) {
  const source = evalMeta || {};
  const rawNested = source.config?.run_config ?? source.config?.runConfig;
  const nested =
    rawNested && typeof rawNested === "object" && !Array.isArray(rawNested)
      ? rawNested
      : {};

  const runConfig = {};
  for (const key of CHILD_RUN_CONFIG_KEYS) {
    const value = readRunConfigKey(source, nested, key);
    if (value === undefined || value === null) continue;
    if (typeof value === "object" && isEmptyCollection(value)) continue;
    if (key === "error_localizer_enabled" && value !== true) continue;
    runConfig[key] = value;
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
