const camelizeKey = (key) =>
  key.replace(/_([a-z])/g, (_, char) => char.toUpperCase());

const shallowCamelizeKeys = (value) => {
  if (value == null || typeof value !== "object" || Array.isArray(value)) {
    return value;
  }

  return Object.entries(value).reduce((acc, [rawKey, rawValue]) => {
    const key = camelizeKey(rawKey);
    const hasExistingKey = Object.prototype.hasOwnProperty.call(acc, key);
    const isSnakeCaseSource = rawKey.includes("_");

    if (!hasExistingKey || !isSnakeCaseSource) {
      acc[key] = rawValue;
    }

    return acc;
  }, {});
};

export const getEvalConfig = (evalData) => evalData?.config || {};

export const getEvalFunctionParamsSchema = (evalData) => {
  const config = getEvalConfig(evalData);
  return config?.functionParamsSchema ?? config?.function_params_schema ?? null;
};

export const getEvalConfigParamsDesc = (evalData) => {
  const config = getEvalConfig(evalData);
  return config?.configParamsDesc ?? config?.config_params_desc ?? null;
};

export const getEvalConfigParamsOption = (evalData) => {
  const config = getEvalConfig(evalData);
  return config?.configParamsOption ?? config?.config_params_option ?? null;
};

export const getEvalTemplateType = (evalData) =>
  evalData?.templateType ?? evalData?.template_type ?? "single";

export const getEvalType = (evalData) =>
  evalData?.evalType ?? evalData?.eval_type ?? "llm";

export const getEvalOutputType = (evalData) =>
  evalData?.outputType ?? evalData?.output_type ?? "";

export const getEvalCurrentVersion = (evalData) =>
  evalData?.currentVersion ?? evalData?.current_version ?? "V1";

export const getEvalRequiredKeys = (evalData) => {
  const config = getEvalConfig(evalData);
  return (
    evalData?.requiredKeys ??
    evalData?.required_keys ??
    config?.requiredKeys ??
    config?.required_keys ??
    []
  );
};

export const getEvalCode = (evalData) => {
  const config = getEvalConfig(evalData);
  return config?.code ?? evalData?.code ?? "";
};

export const getEvalCodeLanguage = (evalData) => {
  const config = getEvalConfig(evalData);
  return (
    config?.language ??
    config?.codeLanguage ??
    config?.code_language ??
    evalData?.codeLanguage ??
    evalData?.code_language ??
    "python"
  );
};

export const normalizeEvalPickerConfig = (config) => {
  if (!config) return {};

  const camelizedConfig = shallowCamelizeKeys(config);

  return {
    ...camelizedConfig,
    functionParamsSchema: camelizedConfig?.functionParamsSchema ?? null,
    configParamsDesc: camelizedConfig?.configParamsDesc ?? null,
    configParamsOption: camelizedConfig?.configParamsOption ?? null,
    requiredKeys: camelizedConfig?.requiredKeys ?? [],
    codeLanguage:
      camelizedConfig?.codeLanguage ?? camelizedConfig?.language ?? "python",
  };
};

export const normalizeEvalPickerEval = (evalData) => {
  if (!evalData) return evalData;

  const camelizedEval = shallowCamelizeKeys(evalData);

  return {
    ...camelizedEval,
    config: normalizeEvalPickerConfig(evalData.config),
    templateType: getEvalTemplateType(evalData),
    evalType: getEvalType(evalData),
    outputType: getEvalOutputType(evalData),
    currentVersion: getEvalCurrentVersion(evalData),
    requiredKeys: getEvalRequiredKeys(evalData),
    code: getEvalCode(evalData),
    codeLanguage: getEvalCodeLanguage(evalData),
  };
};
