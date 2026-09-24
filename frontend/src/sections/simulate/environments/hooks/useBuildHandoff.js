import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";

import { paths } from "src/routes/paths";
import { storeHarnessSecretValues } from "src/api/harness/harness";
import { parseDotEnv } from "src/pages/dashboard/harness/dotenv";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { setPendingCredentialValues } from "src/api/simulate-environments/credentialValues";
import { useEnvironmentsStore } from "../store/useEnvironmentsStore";

/**
 * Hosted providers whose single API-key field maps to the one credential alias
 * the preflight contract expects for that connector. A provider absent here has
 * no single-key exchange, so its key is dropped by redaction as before.
 */
const PROVIDER_SECRET_ALIAS = {
  vapi: "VAPI_API_KEY",
  retell: "RETELL_API_KEY",
};

/**
 * Strip raw secrets before anything is persisted. `apiKey` and `envText` carry
 * plaintext credentials; the draft never needs them, and keeping them out avoids
 * leaking into the zustand store / devtools. `secretFiles` is already a list of
 * `{name,size,secret_ref}` (no contents) and `secret_refs` is opaque, so both
 * survive.
 */
export function redactSource(source) {
  const safe = { ...(source ?? {}) };
  delete safe.apiKey;
  delete safe.envText;
  return safe;
}

/**
 * The raw `{ ALIAS: value }` map a source carries BEFORE redaction:
 *  - a hosted provider's `apiKey` under its fixed alias,
 *  - pasted `.env` contents, one alias per assignment.
 *
 * It feeds two independent preflight paths: exchanged into opaque `secret_refs`
 * (which the `credentials_present` check reads) and sent verbatim as the
 * write-only `credential_values` (which the live `credentials_valid` /
 * `provider_target` probes read).
 */
export function collectCredentialValues(source) {
  const values = {};
  const alias = PROVIDER_SECRET_ALIAS[source?.provider];
  const apiKey = typeof source?.apiKey === "string" ? source.apiKey.trim() : "";
  if (alias && apiKey) values[alias] = apiKey;
  if (source?.envText) Object.assign(values, parseDotEnv(source.envText));
  return values;
}

/**
 * Every "Build environment" CTA funnels through here. It exchanges the source's
 * plaintext credentials for opaque references, stages the raw values for the one
 * preflight that follows, hands the redacted draft to the store and routes to
 * the build page.
 *
 * Without the exchange the preflight sent `secret_refs: {}` and no
 * `credential_values`, so the backend reported the user's own key as missing and
 * never probed the agent id.
 *
 * A failed exchange (or an unparseable `.env`) surfaces as a snackbar and stops
 * the handoff — preflighting a source whose credentials never landed would just
 * report them missing.
 */
export default function useBuildHandoff() {
  const navigate = useNavigate();
  const setDraft = useEnvironmentsStore((s) => s.setDraft);

  return async (source) => {
    let credentialValues;
    let secretRefs = {};
    try {
      credentialValues = collectCredentialValues(source);
      if (Object.keys(credentialValues).length) {
        const { secret_refs: refs } = await storeHarnessSecretValues(credentialValues);
        secretRefs = refs || {};
      }
    } catch (error) {
      enqueueSnackbar(errorMessage(error), { variant: "error" });
      return;
    }

    setPendingCredentialValues(credentialValues);
    const withRefs = Object.keys(secretRefs).length
      ? { ...source, secret_refs: secretRefs }
      : source;
    setDraft(redactSource(withRefs));
    navigate(paths.dashboard.simulate.environments.build);
  };
}
