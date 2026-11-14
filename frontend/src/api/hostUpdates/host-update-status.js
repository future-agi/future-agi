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
  const { discovery, download, versions, safety, last_run: run } = snapshot;
  // These are required objects in both legacy and restore-backed reports.
  if (safety == null || snapshot.operation == null)
    return unavailable("malformed");
  // Swagger 2 flattens the two exact legacy/restore object alternatives.
  // Restore the complete-group constraint here; enums/scalars remain generated.
  for (const [value, keys] of [
    [
      safety,
      [
        "fallback_strategy",
        "restore_state",
        "snapshot_at",
        "loss_window_start",
        "loss_window_end",
        "loss_window_qualification",
        "guidance_id",
        "estimates",
        "failed_target_version",
      ],
    ],
    [
      run,
      [
        "forward_result",
        "restore_result",
        "effective_version",
        "failed_target_version",
      ],
    ],
  ]) {
    if (
      value &&
      keys.some((key) => Object.hasOwn(value, key)) &&
      !keys.every((key) => Object.hasOwn(value, key))
    )
      return unavailable("malformed");
  }
  if (
    (safety.loss_window_end != null &&
      (safety.loss_window_start == null ||
        safety.loss_window_end < safety.loss_window_start)) ||
    (safety.loss_window_qualification === "bounded" &&
      (!safety.loss_window_start || !safety.loss_window_end))
  )
    return unavailable("malformed");
  if (
    (["running", "failed", "recovery_required"].includes(
      safety.restore_state,
    ) ||
      ["restoring", "recovery_required"].includes(snapshot.operation.state)) &&
    (versions.running_release !== null ||
      versions.runtime_state !== "unknown" ||
      run?.effective_version != null)
  )
    return unavailable("malformed");
  const components = versions.components;
  const active = components.filter(
    (item) => item.observation !== "not_deployed",
  );
  // Match the host reader: a release is consistent only when at least one active
  // component was actually observed running that release, including after restore.
  if (
    new Set(components.map((item) => item.component)).size !==
      components.length ||
    components.some((item) =>
      item.observation === "known"
        ? item.observed_versions.length === 0
        : item.observed_versions.length !== 0,
    ) ||
    (versions.runtime_state === "consistent" &&
      (!versions.running_release ||
        !versions.observed_at ||
        active.length === 0 ||
        active.some(
          (item) =>
            item.observation !== "known" ||
            item.observed_versions.length !== 1 ||
            item.observed_versions[0] !== versions.running_release,
        )))
  )
    return unavailable("malformed");
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

export function humanDuration(seconds) {
  if (!Number.isSafeInteger(seconds) || seconds < 0) return "unknown";
  if (seconds < 60) return `${seconds} s`;
  if (seconds < 3600) return `${Math.ceil(seconds / 60)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}

export function humanBytes(bytes) {
  if (!Number.isSafeInteger(bytes) || bytes < 0) return "unknown";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KiB", "MiB", "GiB", "TiB", "PiB"];
  let value = bytes / 1024;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(1)} ${units[index]}`;
}

const automaticReasons = {
  migration_exceeds_window:
    "The migration cannot fit the maintenance window or time budget.",
  insufficient_disk_for_migration:
    "There is not enough disk space for migration growth and the protected restore reserve.",
  backup_exceeds_window:
    "The backup and recovery cannot fit the maintenance window or time budget.",
  insufficient_disk_for_backup:
    "There is not enough disk space for the cold backup and protected restore reserve.",
  restore_capacity_unknown:
    "Backup or restore capacity could not be measured safely.",
};

const automaticActivity = {
  checking: "The host is checking releases.",
  downloading: "The host is downloading images for the current operation.",
  backing_up:
    "The host is backing up the stopped installation for the current update.",
  applying: "The host is applying the current update.",
  verifying:
    "The host is verifying readiness and stability of the current update.",
  rolling_back: "The host is restoring prior images for the current operation.",
};

export function automaticNoticeForStatus(data) {
  if (data?.availability !== "available" || !data.snapshot) return null;
  const { operation, safety, last_run: run, discovery } = data.snapshot;
  if (safety.fallback_strategy !== "restore_backup") return null;
  let text = null;
  let recoveryNotice = false;
  let admissionNotice = false;
  // last_run is history, not the current operation. Recovery comes first, then
  // current forward activity and admission refusals, then the matching restore.
  // Disabled scheduling never implies that an outstanding recovery has ended.
  if (
    operation.state === "recovery_required" ||
    ["failed", "recovery_required"].includes(safety.restore_state)
  ) {
    recoveryNotice = true;
    text = `Automatic recovery needs host operator attention. Installed version is unverified. Last phase: ${operation.phase || "unknown"}. Keep the backup and safety scheduler; inspect private host evidence instead of retrying blindly.`;
  } else if (
    operation.state === "restoring" ||
    safety.restore_state === "running"
  ) {
    recoveryNotice = true;
    text =
      "The update failed; restoring the cold backup and previous images. Future AGI is unavailable until recovery health checks pass.";
  } else if (automaticActivity[operation.state]) {
    text = automaticActivity[operation.state];
  } else {
    const reasons = [
      ...(discovery.reason_codes || []),
      ...(operation.reason_codes || []),
    ];
    const explanations = [
      ...new Set(reasons.filter((reason) => automaticReasons[reason])),
    ].map((reason) => automaticReasons[reason]);
    if (explanations.length) {
      admissionNotice = true;
      text = `Notify-only for this installation. ${explanations.join(" ")} Run a manual update at a time you choose; the updater will not run outside the window.`;
    } else if (discovery.state === "blocked") {
      admissionNotice = true;
      text =
        "Notify-only for this installation. The current release did not pass automatic-update admission; review the reported reasons before taking action on the host.";
    } else if (
      operation.state === "restored" &&
      run?.restore_result === "verified" &&
      run.forward_result !== "succeeded"
    ) {
      recoveryNotice = true;
      text = `Update to ${run.failed_target_version || run.target_version || "the new version"} failed; restored ${run.effective_version || "the previous version"}; ${run.failed_target_version || run.target_version || "the failed target"} will not be retried automatically.`;
    }
  }
  if (!text) return null;
  const activeOperation =
    automaticActivity[operation.state] || operation.state === "restoring";
  const oldProgress =
    data.progress_freshness === "stale" ||
    (activeOperation && data.progress_freshness !== "fresh");
  if (
    data.freshness !== "fresh" ||
    (admissionNotice ? data.discovery_freshness !== "fresh" : oldProgress)
  ) {
    text = `Last host report (stale; current state unknown): ${text}`;
  }
  const estimates = safety.estimates;
  if (estimates) {
    text += ` Estimates: backup ${humanDuration(estimates.backup_seconds)}, migration ${humanDuration(estimates.migration_seconds)}, restore ${humanDuration(estimates.restore_seconds)}, total ${humanDuration(estimates.total_seconds)}; backup free space ${humanBytes(estimates.backup_required_free_bytes)}, Docker free space ${humanBytes(estimates.docker_required_free_bytes)} (restore reserve ${humanBytes(estimates.protected_restore_reserve_bytes)}).`;
  }
  if (recoveryNotice && safety.loss_window_start) {
    text += ` Restore discards new-version writes from ${safety.loss_window_start} to ${safety.loss_window_end || "the failure stop"} (${safety.loss_window_qualification || "unknown"} window). External service calls cannot be undone.`;
  }
  return { text };
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
    automaticNotice: automaticNoticeForStatus(data),
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
