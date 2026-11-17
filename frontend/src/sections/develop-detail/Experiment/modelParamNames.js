// Shared by the initializer and transforms without a dependency on either.
// RHF otherwise interprets dots in model names as nested form paths.
export const escapeModelKey = (key) => key?.replace(/\./g, "__DOT__") ?? key;
export const unescapeModelKey = (key) => key?.replace(/__DOT__/g, ".") ?? key;

// Current RHF state uses escaped keys; older cards can still use raw names.
export const modelParamsFor = (params, model) =>
  params?.[escapeModelKey(model)] ?? params?.[model] ?? {};

// The parameter editor uses camelCase; persisted run/provider settings use
// snake_case. Convert only known top-level aliases (reasoning/schema objects
// are provider contracts, not arbitrary objects to recursively rename).
const aliases = {
  maxTokens: "max_tokens",
  topP: "top_p",
  presencePenalty: "presence_penalty",
  frequencyPenalty: "frequency_penalty",
  responseFormat: "response_format",
};

export function formModelParams(params) {
  const out = { ...(params ?? {}) };
  Object.entries(aliases).forEach(([form, api]) => {
    if (api in out) {
      if (out[form] === undefined) out[form] = out[api];
      delete out[api];
    }
  });
  return out;
}

export function requestModelParams(params) {
  return Object.fromEntries(
    Object.entries(formModelParams(params))
      .filter(([, value]) => value !== null && value !== undefined)
      .map(([key, value]) => [aliases[key] ?? key, value]),
  );
}
