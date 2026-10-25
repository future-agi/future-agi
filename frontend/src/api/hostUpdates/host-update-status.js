import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";

import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { OPENAPI_CONTRACT } from "src/api/contracts/openapi-contract.generated";
import { validateContractedResponse } from "src/api/contracts/openapi-contract";
import { useAuthContext } from "src/auth/hooks";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";

const STATUS_URL = apiPath("/api/host-update-status/");
export const STATUS_POLL_MS = 60_000;
export const STATUS_STALE_MS = 30_000;
const terminal = (data) =>
  ["forbidden", "unsupported"].includes(data?.availability);

// Display gate only. The server rechecks active human organization membership.
// Numeric organization level is authoritative; workspace roles never qualify.
export function canReadHostUpdates(
  { user, authenticated, loading },
  organization,
  deployment,
) {
  const level = user?.org_level;
  const admin =
    level == null
      ? ["Owner", "Admin"].includes(user?.organization_role)
      : [8, 15].includes(level);
  return Boolean(
    authenticated &&
      !loading &&
      user?.id &&
      admin &&
      organization.isReady &&
      organization.currentOrganizationId &&
      deployment.isSuccess &&
      deployment.mode === "oss",
  );
}

export function useHostUpdateAccess() {
  const auth = useAuthContext();
  const organization = useOrganization();
  const deployment = useDeploymentMode();
  let sessionEpoch = "current-session";
  try {
    sessionEpoch = localStorage.getItem("fai_auth_generation") || sessionEpoch;
  } catch {
    // Cache is memory-only; logout also clears the application's QueryClient.
  }
  return {
    allowed: canReadHostUpdates(auth, organization, deployment),
    pending:
      auth.loading ||
      (auth.authenticated && (!organization.isReady || deployment.isLoading)),
    queryKey: [
      "host-update-status",
      auth.user?.id ?? null,
      organization.currentOrganizationId,
      sessionEpoch,
      auth.user?.org_level ?? auth.user?.organization_role ?? null,
    ],
    organizationId: organization.currentOrganizationId,
  };
}

const unavailable = (
  reason = "request_failed",
  availability = "unavailable",
) => ({
  api_schema_version: 1,
  availability,
  reason_code: reason,
  snapshot: null,
  freshness: "unknown",
  discovery_freshness: "unknown",
  versions_freshness: "unknown",
  progress_freshness: "unknown",
  age_seconds: null,
});

const timezone = z
  .string()
  .max(64)
  .refine((value) => {
    try {
      new Intl.DateTimeFormat("en", { timeZone: value });
      return true;
    } catch {
      return false;
    }
  });
const clock = z.string().regex(/^([01][0-9]|2[0-3]):[0-5][0-9]$/);
const windowSchema = z
  .discriminatedUnion("kind", [
    z.object({ kind: z.literal("always"), timezone }).strict(),
    z
      .object({
        kind: z.literal("interval"),
        timezone,
        days_of_week: z.array(z.number().int().min(1).max(7)).min(1).max(7),
        start: clock,
        end: clock,
      })
      .strict(),
  ])
  .nullable();

// The shared OpenAPI validator handles closed objects/types/enums/bounds, but
// deliberately does not enforce string patterns, formats or oneOf. Enforce the
// display-sensitive additions here using the generated schema, not a copied API.
function safeScalars(value, schema) {
  if (value == null) return true;
  if (typeof value === "string") {
    if (schema.pattern && !new RegExp(schema.pattern).test(value)) return false;
    if (schema.format === "date-time") {
      const time = new Date(value);
      if (
        !Number.isFinite(time.getTime()) ||
        time.toISOString() !== value.replace("Z", ".000Z")
      )
        return false;
    }
  }
  if (Array.isArray(value)) {
    if (schema.uniqueItems && new Set(value).size !== value.length)
      return false;
    return value.every((item) => safeScalars(item, schema.items || {}));
  }
  if (typeof value === "object") {
    return Object.entries(value).every(([key, item]) =>
      safeScalars(item, schema.properties?.[key] || {}),
    );
  }
  return true;
}

export function normalizeHostUpdateStatus(data) {
  if (
    (Number.isInteger(data?.result?.api_schema_version) &&
      data.result.api_schema_version !== 1) ||
    (Number.isInteger(data?.result?.snapshot?.schema_version) &&
      data.result.snapshot.schema_version !== 1)
  ) {
    return unavailable("unsupported_schema", "unsupported");
  }
  const valid = validateContractedResponse({
    data,
    status: 200,
    config: { url: STATUS_URL, method: "get" },
  });
  if (
    !valid.ok ||
    !safeScalars(data, OPENAPI_CONTRACT.definitions.HostUpdateStatusResponse)
  ) {
    return unavailable("malformed");
  }
  const result = data.result;
  const snapshot = result.snapshot;
  if (result.availability !== "available") {
    return unavailable(result.reason_code || "malformed", result.availability);
  }
  if (
    !snapshot ||
    result.reason_code !== null ||
    result.age_seconds === null ||
    !windowSchema.safeParse(snapshot.automation.maintenance_window).success
  ) {
    return unavailable("malformed");
  }
  const { discovery, download, versions } = snapshot;
  if (
    ((discovery.candidate_version ||
      ["current", "available"].includes(discovery.state)) &&
      (!discovery.candidate_version ||
        discovery.verification !== "verified" ||
        !discovery.checked_at)) ||
    (download.state === "downloaded" &&
      (!download.completed_at ||
        !download.verified_components.length ||
        !download.target_version ||
        download.target_version !== discovery.candidate_version)) ||
    (versions.runtime_state !== "consistent" &&
      versions.running_release !== null)
  ) {
    return unavailable("malformed");
  }
  return result;
}

export async function fetchHostUpdateStatus({ signal, organizationId }) {
  try {
    const response = await axios.get(STATUS_URL, {
      signal,
      timeout: 5000,
      headers: { "X-Organization-Id": organizationId },
    });
    return normalizeHostUpdateStatus(response.data);
  } catch (error) {
    const status = error?.statusCode ?? error?.response?.status;
    // Resolving a closed denial replaces any previously cached snapshot. Never
    // keep an old authorized report alongside a failed authorization response.
    if (status === 401 || status === 403)
      return unavailable("permission_denied", "forbidden");
    if (status === 404)
      return unavailable("unsupported_installation", "unsupported");
    if (status >= 400 && status < 500) return unavailable("request_rejected");
    // No raw response/error survives in query state, the page or global toasts.
    throw new Error("Host update reporting is unavailable.");
  }
}

export function useHostUpdateStatus() {
  const access = useHostUpdateAccess();
  const client = useQueryClient();
  const query = useQuery({
    queryKey: access.queryKey,
    queryFn: ({ signal }) =>
      fetchHostUpdateStatus({ signal, organizationId: access.organizationId }),
    enabled: (entry) => access.allowed && !terminal(entry.state.data),
    staleTime: STATUS_STALE_MS,
    gcTime: 60_000,
    refetchInterval: (entry) =>
      terminal(entry.state.data) ? false : STATUS_POLL_MS,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
    retry: 1,
    throwOnError: false,
    meta: { errorHandled: true },
  });
  // Keys prevent flashes; remove former scopes too, including a role downgrade.
  const scope = JSON.stringify(access.queryKey);
  useEffect(() => {
    client.removeQueries({
      queryKey: ["host-update-status"],
      predicate: (entry) =>
        !access.allowed || JSON.stringify(entry.queryKey) !== scope,
    });
  }, [access.allowed, scope, client]);

  const data = access.allowed
    ? query.isError
      ? unavailable()
      : query.data
    : undefined;
  return {
    ...access,
    data,
    isLoading: access.allowed && query.isPending,
    isFetching: access.allowed && query.isFetching,
    canRefresh: access.allowed && !terminal(data),
    refresh: () => {
      if (access.allowed && !terminal(data) && !query.isFetching)
        return query.refetch();
      return undefined;
    },
  };
}
