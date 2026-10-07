import { CREDENTIAL_FILE_ALIAS } from "../../workspace/settings/configurationEdit";

export const CHOICE_LABEL = {
  google_model_auth: "Google model sign-in",
};

export const isFileAlias = (alias) => alias === CREDENTIAL_FILE_ALIAS;

export const optionLabel = (option) => option.join(" + ");

export const requiredAliases = (inputNeeded, picks = {}) => [
  ...(inputNeeded?.keys || []),
  ...(inputNeeded?.choices || []).flatMap(
    (choice) => choice.options[picks[choice.id] ?? 0] || [],
  ),
];

export function buildRebuildBody({ required = [], values = {}, files = {} }) {
  const environmentValues = {};
  const credentialFiles = {};
  const missing = [];
  required.forEach((alias) => {
    if (isFileAlias(alias)) {
      if (files[alias]) credentialFiles[alias] = files[alias];
      else missing.push(alias);
      return;
    }
    const value = (values[alias] || "").trim();
    if (value) environmentValues[alias] = value;
    else missing.push(alias);
  });
  Object.entries(values).forEach(([alias, raw]) => {
    const value = (raw || "").trim();
    if (!required.includes(alias) && value) environmentValues[alias] = value;
  });
  const body = {};
  if (Object.keys(environmentValues).length) {
    body.environment_values = environmentValues;
  }
  if (Object.keys(credentialFiles).length) {
    body.credential_files = credentialFiles;
  }
  return { body, missing };
}
