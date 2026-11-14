import { parseDotEnv } from "src/pages/dashboard/harness/dotenv";

/**
 * Hosted providers' credential fields, each mapped to the alias the
 * create/preflight contract requires for that connector. A provider absent
 * here (the chat platforms other than Retell) sends no value, so its key is
 * dropped by redaction as before.
 */
const PROVIDER_SECRET_FIELDS = {
  vapi: { apiKey: "VAPI_API_KEY" },
  retell: { apiKey: "RETELL_API_KEY" },
  retell_chat: { apiKey: "RETELL_API_KEY" },
  livekit: { apiKey: "LIVEKIT_API_KEY", apiSecret: "LIVEKIT_API_SECRET" },
};

/**
 * Strip raw secrets before anything is persisted. `apiKey`, `apiSecret` and
 * `envText` carry plaintext credentials; keeping them out avoids leaking into
 * the zustand store / devtools. `secretFiles` is already `{name,size,secret_ref}`
 * (no contents) and `secret_refs` (staged file refs) is opaque, so both
 * survive.
 */
export function redactSource(source) {
  const safe = { ...(source ?? {}) };
  delete safe.apiKey;
  delete safe.apiSecret;
  delete safe.envText;
  return safe;
}

/**
 * The raw `{ ALIAS: value }` credential map a source carries, BEFORE anything is
 * redacted:
 *  - a hosted provider's credential fields → their fixed aliases
 *    (VAPI_API_KEY / RETELL_API_KEY / LIVEKIT_API_KEY + LIVEKIT_API_SECRET)
 *  - pasted `.env` contents → one alias per assignment (parsed with parseDotEnv)
 *
 * Sent write-only as `credential_values` with preflight and create; never
 * stored here. A blank value (`KEY=`) is dropped, so both see the same set.
 */
function collectCredentialValues(source) {
  const values = {};
  const fields = PROVIDER_SECRET_FIELDS[source?.provider] || {};
  Object.entries(fields).forEach(([field, alias]) => {
    const value =
      typeof source?.[field] === "string" ? source[field].trim() : "";
    if (value) values[alias] = value;
  });
  if (source?.envText) Object.assign(values, parseDotEnv(source.envText));
  return Object.fromEntries(
    Object.entries(values).filter(([, value]) => value !== ""),
  );
}

/**
 * The `{ ALIAS: reference }` map for credential FILES already uploaded to the vault.
 * Preflight reads `agent.secret_refs` and nothing else, so a file left out here is
 * invisible to `credential_files` however many times it was uploaded.
 */
function fileSecretRefs(source) {
  const refs = {};
  for (const file of source?.secretFiles ?? []) {
    const alias = String(file?.environment_name || "").trim();
    if (alias && file?.secret_ref) refs[alias] = file.secret_ref;
  }
  return refs;
}

/**
 * Turn a raw panel source into what preflight/build need:
 *  - `draft`: the redacted source — safe to persist and to POST to
 *    preflight/create. Its `secret_refs` hold the staged credential files only.
 *  - `credentialValues`: the raw `{ ALIAS: value }` map for the write-only
 *    `credential_values` field of preflight and create. This is NEVER stored (it
 *    holds plaintext secrets); only `draft` is staged for build.
 *
 * Rejects if the `.env` parse fails — the caller surfaces it.
 */
export async function prepareSourceForBuild(source) {
  const credentialValues = collectCredentialValues(source);
  const fileRefs = fileSecretRefs(source);
  const withRefs = Object.keys(fileRefs).length
    ? { ...source, secret_refs: fileRefs }
    : source;
  return { draft: redactSource(withRefs), credentialValues };
}
