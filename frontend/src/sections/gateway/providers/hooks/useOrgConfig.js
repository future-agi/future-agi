import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";

export function useOrgConfig() {
  return useQuery({
    queryKey: ["agentcc-org-config"],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.gateway.orgConfig.active);
      return data.result;
    },
    staleTime: 60000,
    retry: false,
  });
}

export function useOrgConfigHistory() {
  return useQuery({
    queryKey: ["agentcc-org-config-history"],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.gateway.orgConfig.list);
      return data.result || data.results || [];
    },
    staleTime: 60000,
  });
}

export function useCreateOrgConfig() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (configData) => {
      const { data } = await axios.post(
        endpoints.gateway.orgConfig.create,
        configData,
      );
      return data.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agentcc-org-config"] });
      queryClient.invalidateQueries({
        queryKey: ["agentcc-org-config-history"],
      });
      enqueueSnackbar("Config saved and activated", { variant: "success" });
    },
    onError: (err) => {
      enqueueSnackbar(err?.response?.data?.message || "Failed to save config", {
        variant: "error",
      });
    },
  });
}

export function useActivateOrgConfig() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (cfgId) => {
      const { data } = await axios.post(
        endpoints.gateway.orgConfig.activate(cfgId),
      );
      return data.result;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agentcc-org-config"] });
      queryClient.invalidateQueries({
        queryKey: ["agentcc-org-config-history"],
      });
      enqueueSnackbar("Rolled back to selected version", {
        variant: "success",
      });
    },
    onError: (err) => {
      enqueueSnackbar(
        err?.response?.data?.message || "Failed to activate config",
        { variant: "error" },
      );
    },
  });
}
