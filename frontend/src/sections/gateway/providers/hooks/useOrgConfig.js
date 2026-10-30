import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";

// The version is stored even when the push to the gateway fails
// (gateway_synced: false), so warn rather than report it as live.
function notifyOrgConfigResult(result, successMessage) {
  if (result?.gateway_synced === false) {
    enqueueSnackbar(
      result.gateway_warning ||
        "Gateway sync failed. Changes will apply on next gateway restart.",
      { variant: "warning" },
    );
    return;
  }
  enqueueSnackbar(successMessage, { variant: "success" });
}

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
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["agentcc-org-config"] });
      queryClient.invalidateQueries({
        queryKey: ["agentcc-org-config-history"],
      });
      notifyOrgConfigResult(result, "Config saved and activated");
    },
    onError: (err) => {
      enqueueSnackbar(err?.message || "Failed to save config", {
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
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["agentcc-org-config"] });
      queryClient.invalidateQueries({
        queryKey: ["agentcc-org-config-history"],
      });
      notifyOrgConfigResult(result, "Rolled back to selected version");
    },
    onError: (err) => {
      enqueueSnackbar(err?.message || "Failed to activate config", {
        variant: "error",
      });
    },
  });
}
