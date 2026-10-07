export const CONNECTION_FIELDS = ["phone_number", "livekit_url", "LIVEKIT_URL"];
export const VARIABLES_FIELD = "dynamic_variables";
export const SPEAKS_FIRST_FIELD = "target_speaks_first";
export const CREDENTIAL_FILE_ENV = "GOOGLE_APPLICATION_CREDENTIALS";
export const CREDENTIAL_FILE_ALIAS = "GOOGLE_APPLICATION_CREDENTIALS_JSON";

const VOICE_CONNECTORS = ["livekit", "vapi", "retell", "phone", "auto"];
const KEY_NAME = /^[A-Z_][A-Z0-9_]*$/;

export function isEditableConfigKey(key) {
  return (
    CONNECTION_FIELDS.includes(key) ||
    key === VARIABLES_FIELD ||
    key === SPEAKS_FIRST_FIELD
  );
}

export function needsRebuild(key) {
  return !isEditableConfigKey(key);
}

export function ownConfigKeys(config, connector) {
  const keys = Object.keys(config || {}).filter(
    (key) => key !== VARIABLES_FIELD,
  );
  if (showsSpeaksFirst(connector) && !keys.includes(SPEAKS_FIRST_FIELD)) {
    keys.push(SPEAKS_FIRST_FIELD);
  }
  return keys;
}

export function showsSpeaksFirst(connector) {
  return VOICE_CONNECTORS.includes(String(connector || "").toLowerCase());
}

export function displayConfigValue(value) {
  if (value && typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function variablesToText(variables) {
  if (!variables || typeof variables !== "object") return "";
  return Object.entries(variables)
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
}

export function textToVariables(text, original = {}) {
  const variables = {};
  const lines = text.split("\n");
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index].trim();
    if (!line) continue;
    const split = line.indexOf("=");
    const key = split > 0 ? line.slice(0, split).trim() : "";
    if (!key) {
      return { error: `Line ${index + 1} needs the form KEY=value.` };
    }
    if (key in variables) {
      return { error: `${key} appears more than once.` };
    }
    const value = line.slice(split + 1).trim();
    const before = original?.[key];
    variables[key] =
      before !== undefined && String(before) === value ? before : value;
  }
  return { variables };
}

export function validateKeyName(name, existing = []) {
  const value = name.trim().toUpperCase();
  if (!value) return "Enter a key name.";
  if (!KEY_NAME.test(value)) {
    return "Use letters, digits and underscores, not starting with a digit.";
  }
  if (value.startsWith("SIMULATOR_")) return "SIMULATOR_ names are reserved.";
  if (existing.includes(value)) {
    return `${value} is already set. Use the pencil to replace it.`;
  }
  return null;
}

function sameVariables(a, b) {
  const left = a || {};
  const right = b || {};
  const keys = Object.keys(left);
  if (keys.length !== Object.keys(right).length) return false;
  return keys.every((key) => key in right && left[key] === right[key]);
}

export function buildConfigurationBody({
  config,
  secretDrafts,
  addedSecrets,
  connectionDrafts,
  speaksFirst,
  variables,
}) {
  const environmentValues = {};
  Object.entries({ ...secretDrafts, ...addedSecrets }).forEach(
    ([alias, value]) => {
      if (value.trim()) environmentValues[alias] = value;
    },
  );

  const configUpdate = {};
  Object.entries(connectionDrafts).forEach(([key, value]) => {
    if (value.trim() && value.trim() !== String(config?.[key] ?? "")) {
      configUpdate[key === "LIVEKIT_URL" ? "livekit_url" : key] = value.trim();
    }
  });
  if (
    speaksFirst !== undefined &&
    speaksFirst !== Boolean(config?.[SPEAKS_FIRST_FIELD])
  ) {
    configUpdate[SPEAKS_FIRST_FIELD] = speaksFirst;
  }
  if (variables && !sameVariables(variables, config?.[VARIABLES_FIELD])) {
    configUpdate[VARIABLES_FIELD] = variables;
  }

  const body = {};
  if (Object.keys(environmentValues).length) {
    body.environment_values = environmentValues;
  }
  if (Object.keys(configUpdate).length) body.config = configUpdate;
  return Object.keys(body).length ? body : null;
}
