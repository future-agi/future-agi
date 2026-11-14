export const DEFAULT_API_PATH_PREFIX = "/v1";

// The decision-model wire format (TypeSafe, Perplexity Decider, a custom
// endpoint). The gateway also accepts "typesafe" for it.
export const SYSTEMONE_API_FORMAT = "systemone";

export function normalizeApiFormat(apiFormat) {
  return apiFormat === "typesafe" ? SYSTEMONE_API_FORMAT : apiFormat;
}

export function isSystemOneFormat(apiFormat) {
  return normalizeApiFormat(apiFormat) === SYSTEMONE_API_FORMAT;
}

// The chat formats read fine as their IDs; only System One needs saying what it
// is. Anything empty comes back as-is so the caller's placeholder still shows.
export function apiFormatLabel(apiFormat) {
  return isSystemOneFormat(apiFormat)
    ? "System One (decision models)"
    : apiFormat;
}

export function getApiPathPrefix(config) {
  return config?.api_path_prefix ?? DEFAULT_API_PATH_PREFIX;
}

export function withApiPathPrefix(config, apiFormat, apiPathPrefix) {
  return apiFormat === "openai"
    ? { ...config, api_path_prefix: apiPathPrefix }
    : config;
}

export function parseTimeoutSeconds(value) {
  const text = String(value ?? "")
    .trim()
    .toLowerCase();
  if (!text) return null;

  const match = text.match(/^(\d+)(?:\s*(ms|s|m))?$/);
  if (!match) return null;

  const amount = Number(match[1]);
  if (!Number.isSafeInteger(amount) || amount <= 0) return null;

  const unit = match[2] || "s";
  if (unit === "ms") return Math.max(1, Math.ceil(amount / 1000));
  if (unit === "m") return amount * 60;
  return amount;
}
