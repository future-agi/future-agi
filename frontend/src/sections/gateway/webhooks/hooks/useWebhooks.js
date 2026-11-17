import { useRef } from "react";
import {
  useQuery,
  useMutation,
  useQueryClient,
  keepPreviousData,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

const WH_KEY = "agentcc-webhooks";
const WH_EVENT_KEY = "agentcc-webhook-events";

export function useWebhooks() {
  return useQuery({
    queryKey: [WH_KEY],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.gateway.webhooks.list);
      return data.result || [];
    },
    staleTime: 30000,
    placeholderData: keepPreviousData,
  });
}

export function useWebhookDetail(id) {
  return useQuery({
    queryKey: [WH_KEY, "detail", id],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.gateway.webhooks.detail(id));
      return data.result;
    },
    enabled: Boolean(id),
    staleTime: 15000,
  });
}

// A failed save reaches the app-wide error handler first (the MutationCache
// onError, src/utils/queryErrorHandler.js), which toasts it unless the save's
// meta.errorHandled is set when it runs. The page passes pageReports(err):
// true only when it will show that failure itself. It is asked about each
// failure before the failure goes on, so one the page cannot show (the person
// left it, or the handler has its own words for it) keeps the app-wide toast,
// and a failure is told once. TanStack Query passes this meta object on by
// reference, so each hook keeps one and sets it for each failure.
// The hook's own onError runs right after the handler and clears the mark
// again. The handler reads it once more, later, for a failure it left to a
// workspace recovery that then ends without a line of its own; by then the
// mark must not carry the answer to a later save on the same hook.
function useReportedSave(pageReports, request) {
  const meta = useRef({ errorHandled: false }).current;
  const mutationFn = async (variables) => {
    meta.errorHandled = false;
    try {
      return await request(variables);
    } catch (err) {
      meta.errorHandled = Boolean(pageReports?.(err));
      throw err;
    }
  };
  const onError = () => {
    meta.errorHandled = false;
  };
  return { meta, mutationFn, onError };
}

export function useCreateWebhook({ pageReports } = {}) {
  const qc = useQueryClient();
  const save = useReportedSave(pageReports, async (payload) => {
    const { data } = await axios.post(
      endpoints.gateway.webhooks.create,
      payload,
    );
    return data.result;
  });
  return useMutation({
    meta: save.meta,
    mutationFn: save.mutationFn,
    onError: save.onError,
    onSuccess: () => qc.invalidateQueries({ queryKey: [WH_KEY] }),
  });
}

export function useUpdateWebhook({ pageReports } = {}) {
  const qc = useQueryClient();
  const save = useReportedSave(pageReports, async ({ id, ...payload }) => {
    const { data } = await axios.patch(
      endpoints.gateway.webhooks.update(id),
      payload,
    );
    return data.result;
  });
  return useMutation({
    meta: save.meta,
    mutationFn: save.mutationFn,
    onError: save.onError,
    onSuccess: () => qc.invalidateQueries({ queryKey: [WH_KEY] }),
  });
}

export function useDeleteWebhook() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id) => {
      const { data } = await axios.delete(
        endpoints.gateway.webhooks.delete(id),
      );
      return data.result;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: [WH_KEY] }),
  });
}

export function useTestWebhook() {
  const qc = useQueryClient();
  return useMutation({
    // A test sends a real request. Retry only after another explicit click,
    // even when the surrounding client has a default mutation retry policy.
    retry: false,
    mutationFn: async (id) => {
      const { data } = await axios.post(endpoints.gateway.webhooks.test(id));
      return data.result;
    },
    onSuccess: (_result, id) => {
      // The API accepts both receiver success and rejection. Both may record
      // an attempt: refresh the overview and invalidate this endpoint's lists.
      // Do not delay the inline receiver result while those reads complete.
      void qc.invalidateQueries({
        queryKey: [WH_EVENT_KEY],
        predicate: ({ queryKey }) => {
          const webhookId = queryKey[1]?.webhook_id;
          return !webhookId || String(webhookId) === String(id);
        },
      });
    },
  });
}

export function useWebhookEvents(params = {}) {
  return useQuery({
    queryKey: [WH_EVENT_KEY, params],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.gateway.webhookEvents.list, {
        params,
      });
      return data.result || [];
    },
    staleTime: 15000,
    placeholderData: keepPreviousData,
  });
}

export function useRetryWebhookEvent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id) => {
      const { data } = await axios.post(
        endpoints.gateway.webhookEvents.retry(id),
      );
      return data.result;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: [WH_EVENT_KEY] }),
  });
}
