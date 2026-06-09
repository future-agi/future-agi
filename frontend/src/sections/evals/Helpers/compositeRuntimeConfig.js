export function buildCompositeRuntimeConfig({ config = {}, codeParams = {} } = {}) {
  const runtimeConfig = config && typeof config === "object" ? { ...config } : {};
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

    if (params && typeof params === "object" && Object.keys(params).length > 0) {
      nextConfig.params = params;
    }

    if (Object.keys(nextConfig).length > 0) {
      acc[childId] = nextConfig;
    }

    return acc;
  }, {});
}
