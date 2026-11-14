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
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [WH_KEY] });
      qc.invalidateQueries({ queryKey: [WH_EVENT_KEY] });
    },
  });
}

export function useTestWebhook() {
  return useMutation({
    mutationFn: async (id) => {
      const { data } = await axios.post(endpoints.gateway.webhooks.test(id));
      return data.result;
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
