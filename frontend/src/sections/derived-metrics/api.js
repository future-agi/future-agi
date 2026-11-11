import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints, readQuery } from "src/utils/axios";

import { useMetricsEnabled } from "./useMetricsEnabled";

const paths = endpoints.derivedMetrics;
const hasScope = (scope) =>
  Boolean(scope?.organizationId && scope?.workspaceId);

/** Scope is explicit, not read from mutable session storage after a request starts.
 * projectId may be absent for the workspace list/capabilities. Version is a cache
 * identity for active reads, NOT an undocumented query parameter.
 */
export const metricKeys = {
  workspace: (scope) => [
    "derived-metrics-v2",
    scope?.organizationId ?? null,
    scope?.workspaceId ?? null,
  ],
  read: (resource, { scope, metricId, version, params, data } = {}) => [
    ...metricKeys.workspace(scope),
    scope?.projectId ?? null,
    resource,
    metricId ?? null,
    version ?? null,
    params ?? {},
    data ?? null,
  ],
};

function requestConfig({ scope, signal }) {
  if (!hasScope(scope))
    throw new Error("A metric workspace scope is required.");
  signal?.throwIfAborted();
  return {
    signal,
    headers: {
      "X-Organization-Id": scope.organizationId,
      "X-Workspace-Id": scope.workspaceId,
    },
  };
}

function withProject(scope, data = {}) {
  if (!scope?.projectId) return data;
  if (data.project_id && data.project_id !== scope.projectId) {
    throw new Error("The metric project does not match the request scope.");
  }
  return { ...data, project_id: scope.projectId };
}

async function responseData(promise, { signal, version } = {}) {
  const response = await promise;
  // Some adapters can resolve after abort. Such a response must not become data.
  signal?.throwIfAborted();
  const data = response.data;
  if (
    version != null &&
    data?.provenance &&
    data.provenance.version !== version
  ) {
    throw Object.assign(
      new Error("The metric definition changed. Reload it to retry."),
      {
        code: "metric_version_changed",
        retryable: false,
      },
    );
  }
  return data;
}

const get = (url, args, params = args.params) =>
  responseData(axios.get(url, { ...requestConfig(args), params }), args);
const post = (url, args, data = args.data) =>
  responseData(axios.post(url, data, requestConfig(args)), args);

/** Real wire envelopes only: no result aliases, default zeros or legacy-run fallback.
 * Every operation takes {scope, metricId?, params?, data?, signal?, version?}.
 * Bodies/queries use the generated snake_case contracts unchanged.
 */
export const metricsApi = {
  list: (args) => get(paths.list, args, withProject(args.scope, args.params)),
  detail: (args) => get(paths.detail(args.metricId), args),
  // The same detail read under its own cache key. A metric waiting for its
  // first calculation polls this, so a poll never blanks the page's detail.
  calculationStatus: (args) => get(paths.detail(args.metricId), args),
  create: (args) => post(paths.list, args, withProject(args.scope, args.data)),
  update: (args) =>
    responseData(
      axios.put(
        paths.detail(args.metricId),
        withProject(args.scope, args.data),
        requestConfig(args),
      ),
      args,
    ),
  validate: (args) =>
    post(paths.validate, args, withProject(args.scope, args.data)),
  capabilities: (args) => get(paths.capabilities, args),
  evaluators: (args) =>
    get(paths.evaluators, args, withProject(args.scope, args.params)),
  // The catalog has {status,result:{metrics,...}}, unlike derived-metric pages.
  // Keep that envelope and use its maintained read-POST transport.
  catalog: (args) =>
    responseData(
      readQuery(endpoints.dashboard.metrics, {
        ...requestConfig(args),
        params: args.params,
      }),
      args,
    ),
  kpi: (args) => get(paths.kpi(args.metricId), args),
  trend: (args) => get(paths.trend(args.metricId), args),
  drivers: (args) => get(paths.drivers(args.metricId), args),
  history: (args) => get(paths.history(args.metricId), args),
  fields: (args) => get(paths.fields(args.metricId), args),
  unitValues: (args) =>
    responseData(
      readQuery(paths.unitValues(args.metricId), {
        ...requestConfig(args),
        params: args.params,
      }),
      args,
    ),
  refresh: (args) => post(paths.refresh(args.metricId), args),
  activate: (args) => post(paths.activate(args.metricId), args),
  snapshots: (args) => get(paths.snapshots(args.metricId), args),
  createSnapshot: (args) => post(paths.snapshots(args.metricId), args),
  deleteSnapshot: (args) =>
    responseData(
      axios.delete(
        paths.snapshot(args.metricId, args.snapshotId),
        requestConfig(args),
      ),
      args,
    ),
  cancelPreview: (args) =>
    args.metricId
      ? post(paths.cancelPreview(args.metricId), args)
      : post(
          paths.cancelDraftPreview,
          args,
          withProject(args.scope, args.data),
        ),
  preview: async (args) => {
    const { metricId, scope, signal } = args;
    requestConfig(args);
    const data = {
      ...args.data,
      // Each actual execution gets a new identity; idempotency_key is not one.
      request_id: args.data?.request_id ?? crypto.randomUUID(),
    };
    const body = metricId ? data : withProject(scope, data);
    const cancel = () => {
      // The cancel request must outlive the aborted preview, and must use the
      // ORIGINAL tenant headers even if the viewer has switched workspaces.
      void metricsApi
        .cancelPreview({
          scope,
          metricId,
          data: {
            request_id: data.request_id,
            ...(!metricId && { project_id: body.project_id }),
          },
        })
        .catch(() => {
          // Best effort server cleanup; client cancellation remains authoritative.
          // Do not replace the original request's error with a cleanup error.
        });
    };
    signal?.addEventListener("abort", cancel, { once: true });
    try {
      return await post(
        metricId ? paths.preview(metricId) : paths.draftPreview,
        args,
        body,
      );
    } finally {
      signal?.removeEventListener("abort", cancel);
    }
  },
};

/** Axios keeps HTTP and semantic error codes separate. Do not turn busy,
 * superseded, disabled (404), or transport failures into empty results.
 */
export function metricErrorState(error) {
  if (!error) return null;
  if (
    error.transportCode === "ERR_CANCELED" ||
    error.code === "ERR_CANCELED" ||
    error.name === "AbortError"
  )
    return "cancelled";
  if (error.statusCode === 503) return "unavailable";
  if (error.statusCode === 429) return "busy";
  if (error.statusCode === 409 && error.code === "preview_superseded")
    return "superseded";
  if (error.statusCode === 409 || error.code === "metric_version_changed")
    return "conflict";
  return "error";
}

const windowReads = new Set(["kpi", "trend", "drivers", "unitValues"]);
const collectionReads = new Set([
  "list",
  "capabilities",
  "evaluators",
  "catalog",
]);
function readReady(resource, args) {
  if (!hasScope(args.scope)) return false;
  if (resource === "preview") {
    return Boolean(
      args.data?.window_start &&
        args.data?.window_end &&
        (args.metricId || (args.scope.projectId && args.data?.definition)),
    );
  }
  if (resource === "evaluators" && !args.scope.projectId) return false;
  if (!collectionReads.has(resource) && !args.metricId) return false;
  if (
    windowReads.has(resource) &&
    !(args.params?.window_start && args.params?.window_end)
  )
    return false;
  if (
    resource === "unitValues" &&
    !(args.params?.context && args.params?.unit_ids?.length)
  )
    return false;
  return true;
}

function useMetricRead(resource, args = {}) {
  const { enabled: rolloutEnabled } = useMetricsEnabled();
  const enabled =
    rolloutEnabled && args.enabled !== false && readReady(resource, args);
  const query = useQuery({
    queryKey: [...metricKeys.read(resource, args), enabled],
    enabled,
    queryFn: ({ signal }) => {
      // refetch() bypasses React Query's enabled option; guard it as well.
      if (!enabled) throw new Error("This metric read is not enabled.");
      return metricsApi[resource]({ ...args, signal });
    },
    retry: false,
    // Previews are explicit builder work, never a focus/reconnect side effect.
    refetchOnWindowFocus: resource !== "preview",
    refetchOnReconnect: resource !== "preview",
    // Opt-in polling (a number or a function of the query). It pauses while
    // the browser tab is hidden.
    refetchInterval: args.refetchInterval ?? false,
    // Never inherit a global previous-page placeholder across scope/windows.
    placeholderData: undefined,
    meta: { errorHandled: true },
  });
  return {
    ...query,
    // A failed refetch can retain query-cache data. It is not a current value.
    data:
      enabled && !query.isError && !query.isFetching ? query.data : undefined,
    readState: !enabled
      ? "idle"
      : query.isFetching
        ? "loading"
        : query.isError
          ? metricErrorState(query.error)
          : query.isPending
            ? "loading"
            : "ready",
  };
}

/** A read another part of the page already settled, from the cache, with
 * when it was read. Null while it loads, refetches or has failed: a cached
 * value is never shown as current without its read time. */
export function settledMetricRead(client, resource, args) {
  const state = client.getQueryState([
    ...metricKeys.read(resource, args),
    true,
  ]);
  return state?.status === "success" && state.fetchStatus === "idle"
    ? { data: state.data, readAt: state.dataUpdatedAt }
    : null;
}

export const useMetricsList = (args) => useMetricRead("list", args);
export const useMetricDetail = (args) => useMetricRead("detail", args);
export const useMetricCalculationStatus = (args) =>
  useMetricRead("calculationStatus", args);
export const useMetricCapabilities = (args) =>
  useMetricRead("capabilities", args);
export const useMetricEvaluators = (args) => useMetricRead("evaluators", args);
export const useMetricCatalog = (args) => useMetricRead("catalog", args);
export const useMetricKpi = (args) => useMetricRead("kpi", args);
export const useMetricTrend = (args) => useMetricRead("trend", args);
export const useMetricDrivers = (args) => useMetricRead("drivers", args);
export const useMetricHistory = (args) => useMetricRead("history", args);
export const useMetricFields = (args) => useMetricRead("fields", args);
export const useMetricUnitValues = (args) => useMetricRead("unitValues", args);
export const useMetricSnapshots = (args) => useMetricRead("snapshots", args);
// F05 owns the 600ms debounce. Pass its settled body here; replacing that body,
// changing scope, or unmounting aborts and cancels the previous server request.
export const useMetricPreview = (args) => useMetricRead("preview", args);

const savedMetricReads = new Set([
  "detail",
  "calculationStatus",
  "kpi",
  "trend",
  "drivers",
  "history",
  "fields",
  "unitValues",
  "snapshots",
]);

function useMetricMutation(operation, args = {}) {
  const client = useQueryClient();
  const { enabled } = useMetricsEnabled();
  return useMutation({
    mutationKey: [
      ...metricKeys.workspace(args.scope),
      args.scope?.projectId ?? null,
      operation,
      args.metricId ?? null,
      args.version ?? null,
      args.snapshotId ?? null,
    ],
    retry: false,
    meta: { errorHandled: true },
    mutationFn: (data) => {
      if (!enabled) throw new Error("Metrics are not enabled.");
      return metricsApi[operation]({ ...args, data });
    },
    onSuccess: (data, _variables, context) => {
      // React Query may update callbacks while a mutation is in flight. Use the
      // captured workspace/metric, or the returned definition ID on creation.
      // Queued refresh is not a completed KPI; refetch the affected saved reads.
      // Previews belong to the explicit builder lifecycle, never invalidation.
      if (operation !== "validate") {
        const affectedMetricId = context.metricId ?? data?.id;
        return client.invalidateQueries({
          queryKey: context.workspaceKey,
          predicate: ({ queryKey }) => {
            const [, , , , resource, metricId] = queryKey;
            return (
              resource === "list" ||
              resource === "catalog" ||
              (affectedMetricId != null &&
                metricId === affectedMetricId &&
                savedMetricReads.has(resource))
            );
          },
        });
      }
    },
    onMutate: () => ({
      workspaceKey: metricKeys.workspace(args.scope),
      metricId: args.metricId,
    }),
  });
}

export const useCreateMetric = (args) => useMetricMutation("create", args);
export const useUpdateMetric = (args) => useMetricMutation("update", args);
export const useValidateMetric = (args) => useMetricMutation("validate", args);
export const useRefreshMetric = (args) => useMetricMutation("refresh", args);
export const useActivateMetric = (args) => useMetricMutation("activate", args);
export const useCreateMetricSnapshot = (args) =>
  useMetricMutation("createSnapshot", args);
export const useDeleteMetricSnapshot = (args) =>
  useMetricMutation("deleteSnapshot", args);
