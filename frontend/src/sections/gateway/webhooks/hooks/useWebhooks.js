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

export function useCreateWebhook() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload) => {
      const { data } = await axios.post(
        endpoints.gateway.webhooks.create,
        payload,
      );
      return data.result;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: [WH_KEY] }),
  });
}

export function useUpdateWebhook() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...payload }) => {
      const { data } = await axios.patch(
        endpoints.gateway.webhooks.update(id),
        payload,
      );
      return data.result;
    },
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
