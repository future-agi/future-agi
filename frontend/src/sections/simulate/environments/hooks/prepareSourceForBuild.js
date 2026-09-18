import { storeHarnessSecretValues } from "src/api/harness/harness";
import { parseDotEnv } from "src/pages/dashboard/harness/dotenv";

/**
 * Hosted providers whose single API-key field maps to the one credential alias
 * the create/preflight contract requires for that connector. A provider absent
 * here (LiveKit's three-part family, the chat platforms) has no single-key
 * exchange, so its key is dropped by redaction as before.
 */
const PROVIDER_SECRET_ALIAS = {
  vapi: "VAPI_API_KEY",
  retell: "RETELL_API_KEY",
};

/**
 * Strip raw secrets before anything is persisted. `apiKey` and `envText` carry
 * plaintext credentials; keeping them out avoids leaking into the zustand store
 * / devtools. `secretFiles` is already `{name,size,secret_ref}` (no contents)
 * and `secret_refs` (from the exchange below) is opaque, so both survive.
 */
export function redactSource(source) {
  const safe = { ...(source ?? {}) };
  delete safe.apiKey;
  delete safe.envText;
  return safe;
}

/**
 * The raw `{ ALIAS: value }` credential map a source carries, BEFORE anything is
 * redacted or exchanged:
 *  - a hosted provider's `apiKey` → its fixed alias (VAPI_API_KEY / RETELL_API_KEY)
 *  - pasted `.env` contents → one alias per assignment (parsed with parseDotEnv)
 *
 * These same values feed two independent preflight paths: exchanged into opaque
 * `secret_refs` (which the `credentials_present` check reads), and sent verbatim
 * as write-only `credential_values` (which the live `credentials_valid` /
 * `provider_target` probes read). Empty when there is nothing to send.
 */
function collectCredentialValues(source) {
  const values = {};
  const alias = PROVIDER_SECRET_ALIAS[source?.provider];
  const apiKey = typeof source?.apiKey === "string" ? source.apiKey.trim() : "";
  if (alias && apiKey) values[alias] = apiKey;
  if (source?.envText) Object.assign(values, parseDotEnv(source.envText));
  return values;
}

/**
 * Exchange a raw `{ ALIAS: value }` map for opaque secret references the backend
 * can resolve. Returns a `{ ALIAS: reference }` map (empty when there is nothing
 * to exchange).
 *
 * NOTE: uploaded credential FILES (`source.secretFiles`) are intentionally not
 * folded here — the environments panels still mint those refs from the mock
 * upload hook, so they are not real vault references yet. They ride the draft
 * untouched until real secret-file upload is wired.
 */
async function exchangeSecrets(values) {
  if (!Object.keys(values).length) return {};
  const { secret_refs } = await storeHarnessSecretValues(values);
  return secret_refs || {};
}

/**
 * Turn a raw panel source into what preflight/build need:
 *  - `draft`: the redacted, exchanged source — safe to persist and to POST to
 *    preflight/create. Its `secret_refs` satisfy `credentials_present`.
 *  - `credentialValues`: the raw `{ ALIAS: value }` map for the preflight-only,
 *    write-only `credential_values` field that drives the live credential probe.
 *    This is NEVER stored (it holds plaintext secrets) — the caller sends it on
 *    the preflight request and discards it; only `draft` is staged for build.
 *
 * Rejects if the secret exchange (or `.env` parse) fails — the caller surfaces it.
 */
export async function prepareSourceForBuild(source) {
  const credentialValues = collectCredentialValues(source);
  const secretRefs = await exchangeSecrets(credentialValues);
  const withRefs = Object.keys(secretRefs).length
    ? { ...source, secret_refs: secretRefs }
    : source;
  return { draft: redactSource(withRefs), credentialValues };
}
