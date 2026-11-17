import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useWorkspace } from "src/contexts/WorkspaceContext";
import { OMEGA_STATE, OmegaError } from "src/sections/omega/shared";

import { keepAccessToken } from "../lib/accessTokens";
import { isBusy } from "../lib/format";
import { isRateLimited, rateLimitDelay } from "../lib/rateLimit";
import { useDeploymentsApi } from "./DeploymentsApiContext";

export { isRateLimited, rateLimitDelay };

/**
 * React Query bindings for the Deployments tab. Every key starts with the
 * selected workspace, so switching workspace never shows another tenant's
 * cached rows. Detail replies pass through keepAccessToken, so a token never
 * sits in the query cache.
 *
 * After a 429 each surface keeps polling, never sooner than its normal
 * interval or the server's Retry-After (lib/rateLimit.js), so it comes back by
 * itself once the limit clears. Reads are not retried on a 429, so a poll
 * fails once and React Query's fetchFailureCount stays at 1.
 */
const LIST_POLL_MS = 15_000;
const DETAIL_POLL_MS = 10_000;
const BUSY_POLL_MS = 1_500;
export const DEPLOYMENT_ID = /^dep-[0-9a-f]{16}$/;

export function listPollInterval(query) {
  const normalInterval = (query?.state?.data || []).some((d) =>
    isBusy(d.status),
  )
    ? BUSY_POLL_MS * 2
    : LIST_POLL_MS;
  if (isRateLimited(query?.state?.error)) {
    return rateLimitDelay(
      query.state.error,
      query.state.fetchFailureCount || 1,
      normalInterval,
    );
  }
  return normalInterval;
}

export function detailPollInterval(query) {
  const normalInterval = isBusy(query?.state?.data?.status)
    ? BUSY_POLL_MS
    : DETAIL_POLL_MS;
  if (isRateLimited(query?.state?.error)) {
    return rateLimitDelay(
      query.state.error,
      query.state.fetchFailureCount || 1,
      normalInterval,
    );
  }
  return normalInterval;
}

export function healthPollInterval(query) {
  const normalInterval = DETAIL_POLL_MS * 3;
  if (isRateLimited(query?.state?.error)) {
    return rateLimitDelay(
      query.state.error,
      query.state.fetchFailureCount || 1,
      normalInterval,
    );
  }
  return normalInterval;
}

function useScope() {
  const { currentWorkspaceId } = useWorkspace();
  return currentWorkspaceId ?? "none";
}

export const deploymentKeys = {
  all: (scope) => ["omega-deployments", scope],
  list: (scope) => ["omega-deployments", scope, "list"],
  one: (scope, id) => ["omega-deployments", scope, "deployment", id],
  health: (scope, id) => [
    "omega-deployments",
    scope,
    "deployment",
    id,
    "health",
  ],
  credentials: (scope) => ["omega-deployments", scope, "credentials"],
};

/**
 * A 401 is a terminal browser-session state for a query. The shared client
 * already refreshes stale one-use assertions before returning a failure; once
 * a request still answers 401, the page must show the sign-in action instead
 * of keeping its detail skeleton while React Query retries it.
 */
export function isTransient(error) {
  if (error?.state === OMEGA_STATE.REFUSED) return error.status === 401;
  return (
    error?.state === OMEGA_STATE.UNAVAILABLE &&
    error.code !== "deployments_unavailable"
  );
}

/**
 * Whether a read is asked again at once. Not on a 401: the page offers sign-in.
 * Not on a 429: the page says how long to wait straight away, and the poll
 * interval is the backoff (a throttled session exchange arrives as
 * UNAVAILABLE, so without this it sat behind two retries and skeletons for
 * about 15 s). Stream and idempotent mutation callers still use isTransient.
 */
export const retryRead = (count, error) =>
  isTransient(error) &&
  error.status !== 401 &&
  !isRateLimited(error) &&
  count < 2;

/** Mutations that carry an Idempotency-Key may be re-sent once, with the same key. */
export const retryIdempotent = (count, error) =>
  count < 1 && isTransient(error);

/** A fresh Idempotency-Key for one user intent (reused if that intent is retried). */
export function newIdempotencyKey(kind = "deploy") {
  const random =
    globalThis.crypto?.randomUUID?.() ??
    `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `fa-dep-${kind}-${random}`;
}

export function useDeploymentList(options = {}) {
  const api = useDeploymentsApi();
  const scope = useScope();
  return useQuery({
    queryKey: deploymentKeys.list(scope),
    queryFn: () => api.list(),
    refetchInterval: listPollInterval,
    retry: retryRead,
    ...options,
  });
}

export function useDeployment(id) {
  const api = useDeploymentsApi();
  const scope = useScope();
  const valid = DEPLOYMENT_ID.test(id || "");
  return useQuery({
    queryKey: deploymentKeys.one(scope, id),
    queryFn: () => {
      // A malformed id is simply not a deployment of this workspace.
      if (!valid)
        throw new OmegaError({ state: OMEGA_STATE.NOT_FOUND, status: 404 });
      return api.get(id).then(keepAccessToken);
    },
    enabled: Boolean(id),
    refetchInterval: detailPollInterval,
    retry: retryRead,
  });
}

export function useDeploymentHealth(id, enabled = true) {
  const api = useDeploymentsApi();
  const scope = useScope();
  return useQuery({
    queryKey: deploymentKeys.health(scope, id),
    queryFn: () => api.health(id),
    enabled: Boolean(id) && enabled,
    refetchInterval: healthPollInterval,
    retry: retryRead,
  });
}

export function useCredentials(enabled = true) {
  const api = useDeploymentsApi();
  const scope = useScope();
  return useQuery({
    queryKey: deploymentKeys.credentials(scope),
    queryFn: () => api.credentials(),
    enabled,
    retry: retryRead,
  });
}

/**
 * One mutation per deployment verb. Detail replies drop their access token
 * into the in-memory holder; each success refreshes the workspace's keys.
 * Nothing is re-sent unless the caller passes `retry: retryIdempotent`, which
 * only keyed calls do (a re-send with the same key never deploys twice).
 * `removes: id` drops that deployment's cached queries instead of re-reading.
 */
export function useDeploymentMutation(run, options = {}) {
  const api = useDeploymentsApi();
  const scope = useScope();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (variables) => {
      const result = await run(api, variables);
      return result && typeof result === "object" && "schemaVersion" in result
        ? keepAccessToken(result)
        : result;
    },
    retry: false,
    retryDelay: 400,
    ...options,
    onSuccess: async (data, variables, context) => {
      if (options.removes) {
        // A deleted deployment is forgotten, not re-read (that would 404).
        queryClient.removeQueries({
          queryKey: deploymentKeys.one(scope, options.removes),
        });
      } else if (data?.id && data.schemaVersion)
        queryClient.setQueryData(deploymentKeys.one(scope, data.id), data);
      await queryClient.invalidateQueries({
        queryKey: deploymentKeys.all(scope),
      });
      await options.onSuccess?.(data, variables, context);
    },
  });
}
