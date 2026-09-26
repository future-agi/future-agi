import { useMutation } from "@tanstack/react-query";
import { preflightHarnessJob } from "src/api/harness/harness";
import { draftToPreflightPayload } from "src/api/simulate-environments/preflightPayload";

/**
 * Submit the source and write-only credential values for hosted preflight.
 * The response carries readiness and a credential report (requirements,
 * alternatives, and live probes), not a checks/state array.
 *
 * `credentialValues` is the raw `{ ALIAS: value }` map (from prepareSourceForBuild)
 * for the write-only `credential_values` preflight field: it drives the live
 * `credentials_valid` / `provider_target` probes and is stripped by the backend
 * from any echoed/created payload. It is sent only here and never persisted.
 *
 * A draft that cannot be preflighted (unparseable repo, missing archive, …) maps
 * to `{ skipped }` — surfaced here as a rejected mutation so the panel renders an
 * error rather than silently doing nothing. `meta.errorHandled` suppresses the
 * global mutation-error toast since the panel shows the failure inline.
 */
export function useRuntimePreflight() {
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: async ({ draft, credentialValues } = {}) => {
      const { payload, skipped } = draftToPreflightPayload(draft);
      if (skipped) throw new Error(skipped);
      if (credentialValues && Object.keys(credentialValues).length) {
        payload.credential_values = credentialValues;
      }
      return preflightHarnessJob(payload);
    },
  });
}
