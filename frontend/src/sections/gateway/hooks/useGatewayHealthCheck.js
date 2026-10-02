import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";

export function useGatewayHealthCheck() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (id) => {
      const { data } = await axios.post(
        endpoints.gateway.healthCheck(id),
        {},
        { timeout: 30000 },
      );
      return data.result;
    },
    onSuccess: () => {
      enqueueSnackbar("Health check complete", { variant: "success" });
    },
    onError: (error) => {
      const timedOut =
        error?.transportCode === "ECONNABORTED" ||
        error?.code === "ECONNABORTED";
      if (timedOut) {
        enqueueSnackbar(
          "Health check timed out — the gateway did not respond. Check that it is reachable, then try again.",
          { variant: "error" },
        );
        return;
      }
      const detail = error?.detail || error?.message;
      enqueueSnackbar(
        typeof detail === "string" && detail
          ? `Health check failed: ${detail}`
          : "Health check failed",
        { variant: "error" },
      );
    },
    onSettled: () => {
      // An unreachable gateway is still a completed check. Refresh its status
      // and server timestamp on failure too, not just on success.
      queryClient.invalidateQueries({ queryKey: ["agentcc-gateways"] });
    },
  });
}
