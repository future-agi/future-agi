// The webhooks API (FA `webhooks/`, DESIGN.md section 10) and the one destination
// registry (`/integrations/destinations/`, section 11.1), as React Query hooks.
// Every response is FA's envelope `{ status, result }`; the hooks select `result`.
import { createContext, useContext } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

const result = (res) => res?.data?.result;

// The section shows these failures inline, next to what failed; this keeps the
// app-wide toast (src/utils/queryErrorHandler.js) from repeating them on top,
// where it can also cover a dialog's buttons.
const INLINE_ERRORS = { meta: { errorHandled: true } };

// The workspace the Webhooks section works in. A workspace's settings page
// (/settings/workspace/<id>/integrations) may be open while the session's
// workspace is another one, so every webhooks request from such a page names
// the page's workspace in X-Workspace-Id: FA's authentication then checks the
// caller may open it and scopes the request to it, exactly as for the session's
// own workspace. Null: the session's workspace.
const WebhooksScopeContext = createContext(null);
export const WebhooksScope = WebhooksScopeContext.Provider;
export const useWebhooksScope = () => useContext(WebhooksScopeContext);

export const scopedConfig = (workspaceId, config = {}) =>
  workspaceId
    ? {
        ...config,
        headers: { ...(config.headers || {}), "X-Workspace-Id": workspaceId },
      }
    : config;

const scopeKey = (workspaceId) => workspaceId || "session";

export const webhookKeys = {
  all: ["webhooks"],
  destinationsAll: () => [...webhookKeys.all, "destinations"],
  destinations: (workspaceId) => [
    ...webhookKeys.destinationsAll(),
    scopeKey(workspaceId),
  ],
  endpoints: () => [...webhookKeys.all, "endpoints"],
  endpoint: (id) => [...webhookKeys.all, "endpoint", id],
  // `filters` holds what the person chose (a time window as `hours`), never a
  // timestamp computed while rendering: a key that changes on every render
  // refetches without end.
  deliveries: (id, filters) => [...webhookKeys.all, "deliveries", id, filters],
  bulkCount: (id, filters) => [
    ...webhookKeys.all,
    "deliveries",
    id,
    "bulk-count",
    filters,
  ],
  delivery: (id) => [...webhookKeys.all, "delivery", id],
  activity: (id) => [...webhookKeys.all, "activity", id],
  testOutcome: (eventId) => [...webhookKeys.all, "test-outcome", eventId],
  eventTypes: () => [...webhookKeys.all, "event-types"],
  policy: (workspaceId) => [...webhookKeys.all, "policy", workspaceId || "org"],
  egress: () => [...webhookKeys.all, "egress"],
};

// A time window chosen as "the last N hours" becomes a start time when the
// request is made, so the window is always measured from now.
export const sinceOf = (hours, now = Date.now()) =>
  hours ? new Date(now - Number(hours) * 3600_000).toISOString() : undefined;

export function deliveryParams(filters = {}, now = Date.now()) {
  const { hours, ...rest } = filters;
  const params = Object.fromEntries(
    Object.entries(rest).filter(([, v]) => v !== "" && v != null),
  );
  const since = sinceOf(hours, now);
  if (since) params.since = since;
  return params;
}

export const useDestinations = (options = {}) => {
  const ws = useWebhooksScope();
  return useQuery({
    queryKey: webhookKeys.destinations(ws),
    // A registry that cannot be read leaves the section absent, without a toast.
    ...INLINE_ERRORS,
    queryFn: () =>
      axios.get(endpoints.integrations.destinations, scopedConfig(ws)),
    select: result,
    staleTime: 15_000,
    ...options,
  });
};

export const useWebhookEndpoints = (params = {}, options = {}) => {
  const ws = useWebhooksScope();
  return useQuery({
    queryKey: [...webhookKeys.endpoints(), scopeKey(ws), params],
    ...INLINE_ERRORS,
    queryFn: () =>
      axios.get(endpoints.webhooks.endpoints, scopedConfig(ws, { params })),
    select: (res) => result(res)?.results || [],
    staleTime: 15_000,
    ...options,
  });
};

export const useWebhookEndpoint = (id, options = {}) => {
  const ws = useWebhooksScope();
  return useQuery({
    queryKey: webhookKeys.endpoint(id),
    ...INLINE_ERRORS,
    queryFn: () => axios.get(endpoints.webhooks.endpoint(id), scopedConfig(ws)),
    select: result,
    enabled: !!id,
    ...options,
  });
};

export const useEventTypes = (options = {}) =>
  useQuery({
    queryKey: webhookKeys.eventTypes(),
    queryFn: () => axios.get(endpoints.webhooks.eventTypes),
    select: result,
    staleTime: Infinity,
    ...options,
  });

export const useWebhookDeliveries = (
  endpointId,
  filters = {},
  options = {},
) => {
  const ws = useWebhooksScope();
  return useInfiniteQuery({
    queryKey: webhookKeys.deliveries(endpointId, filters),
    ...INLINE_ERRORS,
    queryFn: ({ pageParam }) =>
      axios.get(
        endpoints.webhooks.endpointDeliveries(endpointId),
        scopedConfig(ws, {
          params: {
            ...deliveryParams(filters),
            ...(pageParam ? { cursor: pageParam } : {}),
            limit: 50,
          },
        }),
      ),
    getNextPageParam: (last) => result(last)?.next_cursor || undefined,
    initialPageParam: undefined,
    select: (data) => ({
      ...data,
      rows: data.pages.flatMap((p) => result(p)?.results || []),
    }),
    enabled: !!endpointId,
    // A log: switching back to a filter shows what is true now, not what was
    // true when that filter was last open.
    staleTime: 0,
    ...options,
  });
};

// "Redeliver failed (N)": N is the bulk endpoint's own dry-run count for the
// filters on screen (event type, prompt or deployment, time window and #), so
// the number on the button is the number the confirm dialog and the real run
// act on: the bulk API takes the same filters as the log.
export const bulkBody = (endpointId, filters = {}, now = Date.now()) => {
  const body = { endpoint: endpointId, status: "failed" };
  if (filters.event_type) body.event_type = filters.event_type;
  const subject = (filters.subject || "").trim();
  if (subject) body.subject = subject;
  const since = sinceOf(filters.hours, now);
  if (since) body.since = since;
  for (const key of ["seq_from", "seq_to"]) {
    const value = String(filters[key] ?? "").trim();
    if (value) body[key] = Number(value);
  }
  return body;
};

// The log's and the bulk's subject filter: prompt:<id> or deployment:<id>.
export const SUBJECT_SHAPE = /^(prompt|deployment):\S+$/;
const SEQ_SHAPE = /^[1-9][0-9]*$/;

// Why the bulk cannot run on these filters yet, or null: a prompt or deployment
// still being typed, or a # that is not a whole number, would count (and
// redeliver) something other than the view.
export const bulkFiltersProblem = (filters = {}) => {
  const subject = (filters.subject || "").trim();
  if (subject && !SUBJECT_SHAPE.test(subject))
    return "Type the prompt or deployment as prompt:<id> or deployment:<id> to redeliver in bulk.";
  for (const key of ["seq_from", "seq_to"]) {
    const value = String(filters[key] ?? "").trim();
    if (value && !SEQ_SHAPE.test(value))
      return "The # filter takes whole numbers from 1.";
  }
  return null;
};

export const useBulkRedeliverCount = (endpointId, filters, options = {}) => {
  const ws = useWebhooksScope();
  return useQuery({
    queryKey: webhookKeys.bulkCount(endpointId, filters),
    ...INLINE_ERRORS,
    queryFn: () =>
      axios.post(
        endpoints.webhooks.redeliverBulk,
        { ...bulkBody(endpointId, filters), dry_run: true },
        scopedConfig(ws),
      ),
    select: (res) => result(res)?.count ?? 0,
    enabled: !!endpointId,
    staleTime: 0,
    // The last count stays on the button while a new filter is counted.
    placeholderData: (previous) => previous,
    ...options,
  });
};

// The endpoint page's Activity card: this endpoint's audit entries, newest
// first, a page of 20 at a time (GET /webhooks/endpoints/{id}/activity/).
export const useWebhookActivity = (endpointId, options = {}) => {
  const ws = useWebhooksScope();
  return useInfiniteQuery({
    queryKey: webhookKeys.activity(endpointId),
    ...INLINE_ERRORS,
    queryFn: ({ pageParam }) =>
      axios.get(
        endpoints.webhooks.activity(endpointId),
        scopedConfig(ws, {
          params: { limit: 20, ...(pageParam ? { cursor: pageParam } : {}) },
        }),
      ),
    getNextPageParam: (last) => result(last)?.next_cursor || undefined,
    initialPageParam: undefined,
    select: (data) => ({
      ...data,
      rows: data.pages.flatMap((p) => result(p)?.results || []),
    }),
    enabled: !!endpointId,
    ...options,
  });
};

export const useWebhookDelivery = (id, options = {}) => {
  const ws = useWebhooksScope();
  return useQuery({
    queryKey: webhookKeys.delivery(id),
    ...INLINE_ERRORS,
    queryFn: () => axios.get(endpoints.webhooks.delivery(id), scopedConfig(ws)),
    select: result,
    enabled: !!id,
    ...options,
  });
};

const SETTLED = ["succeeded", "failed", "skipped", "cancelled"];

// A test the API answered with 202 (DESIGN.md section 8.2: "the UI keeps
// polling"): the delivery is read until it settles, then reported like a 200.
// The 202 names the delivery when it already exists, and always the event.
export function testOutcomeOf(delivery) {
  if (!delivery || !SETTLED.includes(delivery.status)) return null;
  const attempts = delivery.attempts || [];
  const last = attempts[attempts.length - 1];
  if (!last) return null;
  return {
    status_code: last.response_status,
    outcome: last.outcome,
    latency_ms: last.duration_ms,
    response_excerpt: last.response_body_excerpt,
  };
}

export const useTestOutcome = (queued, options = {}) => {
  const ws = useWebhooksScope();
  const eventId = queued?.event_id;
  return useQuery({
    queryKey: webhookKeys.testOutcome(eventId),
    ...INLINE_ERRORS,
    queryFn: async () => {
      let deliveryId = queued.delivery_id;
      if (!deliveryId) {
        const list = await axios.get(
          endpoints.webhooks.deliveries,
          scopedConfig(ws, { params: { event: eventId, limit: 1 } }),
        );
        deliveryId = result(list)?.results?.[0]?.id;
        if (!deliveryId) return null;
      }
      const res = await axios.get(
        endpoints.webhooks.delivery(deliveryId),
        scopedConfig(ws),
      );
      return testOutcomeOf(result(res));
    },
    enabled: !!eventId,
    // Every 2 s until the delivery settles; a test is one attempt, so a few
    // minutes is far beyond the longest one (the timeout is at most 30 s).
    refetchInterval: (q) =>
      q.state.data || q.state.dataUpdateCount > 150 ? false : 2000,
    ...options,
  });
};

export const useWebhookPolicy = (workspaceId, options = {}) =>
  useQuery({
    queryKey: webhookKeys.policy(workspaceId),
    queryFn: () =>
      axios.get(
        workspaceId
          ? endpoints.webhooks.workspacePolicy(workspaceId)
          : endpoints.webhooks.policy,
      ),
    select: result,
    ...options,
  });

export const useEgressIps = (options = {}) =>
  useQuery({
    queryKey: webhookKeys.egress(),
    queryFn: () => axios.get(endpoints.webhooks.egressIps),
    select: result,
    staleTime: Infinity,
    ...options,
  });

// Every write refreshes the registry, the list and the endpoint it touched;
// `keysOf(variables)` names any other query the write changes.
const useWebhookMutation = (mutationFn, { endpointId, keysOf } = {}) => {
  const queryClient = useQueryClient();
  const ws = useWebhooksScope();
  return useMutation({
    mutationFn: (variables) => mutationFn(variables, scopedConfig(ws)),
    ...INLINE_ERRORS,
    onSettled: (_data, _error, variables) => {
      queryClient.invalidateQueries({
        queryKey: webhookKeys.destinationsAll(),
      });
      queryClient.invalidateQueries({ queryKey: webhookKeys.endpoints() });
      const id = endpointId || variables?.id;
      if (id) {
        queryClient.invalidateQueries({ queryKey: webhookKeys.endpoint(id) });
        queryClient.invalidateQueries({
          queryKey: [...webhookKeys.all, "deliveries", id],
        });
        // Every write is an audit entry on the endpoint's Activity card.
        queryClient.invalidateQueries({ queryKey: webhookKeys.activity(id) });
      }
      for (const queryKey of keysOf ? keysOf(variables) : []) {
        queryClient.invalidateQueries({ queryKey });
      }
    },
  });
};

export const useCreateWebhook = () =>
  useWebhookMutation((body, config) =>
    axios.post(endpoints.webhooks.endpoints, body, config),
  );

export const useUpdateWebhook = (id) =>
  useWebhookMutation(
    (body, config) =>
      axios.patch(endpoints.webhooks.endpoint(id), body, config),
    { endpointId: id },
  );

export const useDeleteWebhook = (id) =>
  useWebhookMutation(
    (_v, config) => axios.delete(endpoints.webhooks.endpoint(id), config),
    { endpointId: id },
  );

export const useSetWebhookEnabled = (id) =>
  useWebhookMutation(
    ({ enabled }, config) =>
      axios.post(
        enabled
          ? endpoints.webhooks.enable(id)
          : endpoints.webhooks.disable(id),
        {},
        config,
      ),
    { endpointId: id },
  );

export const useResumeWebhook = (id) =>
  useWebhookMutation(
    ({ mode }, config) =>
      axios.post(endpoints.webhooks.resume(id), { mode }, config),
    { endpointId: id },
  );

export const useRotateSecret = (id) =>
  useWebhookMutation(
    ({ overlapSeconds }, config) =>
      axios.post(
        endpoints.webhooks.rotateSecret(id),
        { overlap_seconds: overlapSeconds },
        config,
      ),
    { endpointId: id },
  );

export const useRevokePreviousSecret = (id) =>
  useWebhookMutation(
    (_v, config) =>
      axios.post(endpoints.webhooks.revokePreviousSecret(id), {}, config),
    { endpointId: id },
  );

// A test is sent by the worker; the API waits up to 20 s for the answer (200)
// and otherwise answers 202 with the ids, which useTestOutcome follows.
export const useSendTest = () =>
  useWebhookMutation(({ id, eventType }, config) =>
    axios.post(
      endpoints.webhooks.test(id),
      { event_type: eventType },
      { ...config, timeout: 40_000 },
    ),
  );

// The drawer shows the delivery it redelivered: its own query is refreshed too,
// so attempt 2 appears there without reopening it.
export const useRedeliver = (endpointId) =>
  useWebhookMutation(
    (deliveryId, config) =>
      axios.post(endpoints.webhooks.redeliver(deliveryId), {}, config),
    {
      endpointId,
      keysOf: (deliveryId) => [webhookKeys.delivery(deliveryId)],
    },
  );

export const useBulkRedeliver = (endpointId) =>
  useWebhookMutation(
    (body, config) =>
      axios.post(endpoints.webhooks.redeliverBulk, body, config),
    { endpointId },
  );

export const usePreview = () => {
  const ws = useWebhooksScope();
  return useMutation({
    mutationFn: (body) =>
      axios.post(endpoints.webhooks.preview, body, scopedConfig(ws)),
    ...INLINE_ERRORS,
  });
};

export const useUpdatePolicy = (workspaceId) => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body) =>
      axios.put(
        workspaceId
          ? endpoints.webhooks.workspacePolicy(workspaceId)
          : endpoints.webhooks.policy,
        body,
      ),
    ...INLINE_ERRORS,
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: webhookKeys.all });
    },
  });
};
