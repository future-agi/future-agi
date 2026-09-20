const SECRET_NAME_MARKERS = [
  "TOKEN",
  "SECRET",
  "PASSWORD",
  "API_KEY",
  "PRIVATE_KEY",
];

export function isSecretCredentialName(name) {
  const normalized = String(name || "").toUpperCase();
  return SECRET_NAME_MARKERS.some((marker) => normalized.includes(marker));
}

export function partitionConfigurationValues(values) {
  const configurationValues = {};
  const environmentValues = {};
  for (const [name, value] of Object.entries(values || {})) {
    if (isSecretCredentialName(name)) environmentValues[name] = value;
    else configurationValues[name] = value;
  }
  return { configurationValues, environmentValues };
}

export function credentialValue(environmentValues, configurationValues, name) {
  if (Object.hasOwn(environmentValues, name)) return environmentValues[name];
  return configurationValues[name] || "";
}

export function mergePastedCredentials(
  environmentValues,
  configurationValues,
  pastedValues,
) {
  const nextConfiguration = { ...configurationValues };
  Object.keys(pastedValues).forEach((name) => delete nextConfiguration[name]);
  return {
    environmentValues: { ...environmentValues, ...pastedValues },
    configurationValues: nextConfiguration,
  };
}

export function updateCredential(
  environmentValues,
  configurationValues,
  { name, value, kind },
) {
  const nextEnvironment = { ...environmentValues };
  const nextConfiguration = { ...configurationValues };
  const belongsToEnvironment =
    kind === "secret" ||
    isSecretCredentialName(name) ||
    Object.hasOwn(environmentValues, name);

  if (belongsToEnvironment) {
    nextEnvironment[name] = value;
    delete nextConfiguration[name];
  } else {
    nextConfiguration[name] = value;
    delete nextEnvironment[name];
  }
  return {
    environmentValues: nextEnvironment,
    configurationValues: nextConfiguration,
  };
}

export function credentialCount(environmentValues, configurationValues) {
  return new Set([
    ...Object.keys(environmentValues),
    ...Object.keys(configurationValues),
  ]).size;
}
