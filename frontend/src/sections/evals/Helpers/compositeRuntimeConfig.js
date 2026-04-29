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
