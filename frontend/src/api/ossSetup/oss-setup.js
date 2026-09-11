import axios from "axios";
import { useQuery } from "@tanstack/react-query";
import { HOST_API } from "src/config-global";
import { endpoints } from "src/utils/axios";

// Not the shared instance: its 401 handler redirects to login, and this screen
// is pre-auth.
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
  const res = await bareClient.get(endpoints.ossSetup.setupChecks, {
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
    staleTime: 0,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
  });
}
