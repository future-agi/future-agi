import axios from "axios";
import { useQuery } from "@tanstack/react-query";
import { HOST_API } from "src/config-global";
import { apiPath, isContractedApiPath } from "src/api/contracts/api-surface";

// GET /api/setup-checks/?mode=live|experiment
//
// Unauthenticated, OSS-only, stateless: every request re-probes and returns a
// full snapshot. See the API contract appendix in the OSS setup working doc.
const SETUP_CHECKS_PATH = "/api/setup-checks/";

// The endpoint ships with the backend work (B3). `apiPath` throws for paths
// absent from the generated contract, and `endpoints` is evaluated at module
// load — so registering it there today would crash the app on import. Resolving
// inside the query instead keeps the contract check (no raw literal ever reaches
// axios) and lets the screen render its designed "unreachable" state until B3
// lands and `yarn contracts:generate` picks the path up.
export const isSetupChecksAvailable = () =>
  isContractedApiPath(SETUP_CHECKS_PATH);

// TH-7217 CLEANUP: delete this fallback and register the path via `apiPath`
// once the endpoint exists.
//
// Until then the path is absent from the generated surface, so `apiPath`
// throws before a request is ever made — which would also stop MSW from seeing
// it. In dev we fall through to the literal path so the mock handler can serve
// the screen; production stays strict and surfaces the unreachable state.
// Once B3 ships and `yarn contracts:generate` runs, both branches converge and
// this fallback can be deleted along with the mock handler.
const resolveSetupChecksPath = () => {
  if (isContractedApiPath(SETUP_CHECKS_PATH)) {
    return apiPath(SETUP_CHECKS_PATH);
  }
  if (import.meta.env.DEV) {
    return SETUP_CHECKS_PATH;
  }
  throw new Error(
    `API path is not in generated contract: ${SETUP_CHECKS_PATH}`,
  );
};

// Deliberately NOT the shared axios instance. That one answers any 401 by
// attempting a token refresh and hard-redirecting to /auth/jwt/login
// (src/utils/axios.js). This screen runs pre-auth and polls, so the shared
// instance would bounce it off itself. Do not "consolidate" this back.
const bareClient = axios.create({ baseURL: HOST_API, timeout: 10000 });

export const OSS_SETUP_KEYS = {
  checks: (mode) => ["ossSetup", "checks", mode],
};

const normalizeCheck = (check) => ({
  id: check.id,
  label: check.label,
  status: check.status,
  required: Boolean(check.required),
  detail: check.detail || "",
});

export async function fetchSetupChecks(mode, { signal } = {}) {
  const res = await bareClient.get(resolveSetupChecksPath(), {
    params: { mode },
    signal,
  });
  const result = res?.data?.result ?? {};
  return {
    status: result.status ?? "issues",
    mode: result.mode ?? mode,
    checks: Array.isArray(result.checks)
      ? result.checks.map(normalizeCheck)
      : [],
  };
}

export function useSetupChecks(mode, options = {}) {
  return useQuery({
    ...options,
    queryKey: OSS_SETUP_KEYS.checks(mode),
    queryFn: ({ signal }) => fetchSetupChecks(mode, { signal }),
    // Every run is user- or poll-initiated; never serve a stale snapshot.
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });
}
