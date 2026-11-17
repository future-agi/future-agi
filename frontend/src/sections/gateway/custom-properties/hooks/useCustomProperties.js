import {
  useQuery,
  useMutation,
  useQueryClient,
  keepPreviousData,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

const CP_KEY = "agentcc-custom-properties";

export function useCustomProperties(params = {}, options = {}) {
  return useQuery({
    queryKey: [CP_KEY, params],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.gateway.customProperties.list,
        { params },
      );
      return data.result || [];
    },
    staleTime: 30000,
    placeholderData: keepPreviousData,
    ...options,
  });
}

export function useCustomPropertyDetail(id) {
  return useQuery({
    queryKey: [CP_KEY, "detail", id],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.gateway.customProperties.detail(id),
      );
      return data.result;
    },
    enabled: Boolean(id),
    staleTime: 15000,
  });
}

export function useCreateCustomProperty() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload) => {
      const { data } = await axios.post(
        endpoints.gateway.customProperties.create,
        payload,
      );
      return data.result;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: [CP_KEY] }),
  });
}

export function useUpdateCustomProperty() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...payload }) => {
      const { data } = await axios.patch(
        endpoints.gateway.customProperties.update(id),
        payload,
      );
      return data.result;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: [CP_KEY] }),
  });
}

export function useDeleteCustomProperty() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id) => {
      const { data } = await axios.delete(
        endpoints.gateway.customProperties.delete(id),
      );
      return data.result;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: [CP_KEY] }),
  });
}

export function useValidateCustomProperties() {
  return useMutation({
    mutationFn: async (payload) => {
      const { data } = await axios.post(
        endpoints.gateway.customProperties.validate,
        payload,
      );
      return data.result;
    },
  });
}
