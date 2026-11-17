import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import { getSafeActionErrorMessage } from "src/utils/errorUtils";

export const HEALTH_CHECK_TIMEOUT_MS = 30000;

// No response within the client timeout is not proof that the gateway is down:
// the server may still be working. Say what we know and nothing more.
export const HEALTH_CHECK_TIMEOUT_MESSAGE =
  "Health check timed out after 30 seconds without a response, so the gateway status was not confirmed. Try again in a moment.";

// Why a health check failed, from src/utils/axios's flat rejection, or ""
// when the server's text is not safe to show.
export function healthCheckFailureReason(error) {
  // Failed probes return a typed result whose `error` is written for users.
  const typedError =
    typeof error?.result?.error === "string" ? error.result.error : undefined;
  return getSafeActionErrorMessage(
    {
      statusCode: error?.statusCode,
      code: error?.code,
      result: typedError || error?.detail || error?.message,
    },
    "",
  );
}

export function healthCheckFailureMessage(error) {
  const reason = healthCheckFailureReason(error);
  return reason ? `Health check failed: ${reason}` : "Health check failed";
}

export function useGatewayHealthCheck() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (id) => {
      const { data } = await axios.post(
        endpoints.gateway.healthCheck(id),
        {},
        { timeout: HEALTH_CHECK_TIMEOUT_MS },
      );
      return data.result;
    },
    // This hook reports its own failures; without this the app-wide
    // MutationCache handler adds a second, raw toast for the same error.
    meta: { errorHandled: true },
    onSuccess: () => {
      enqueueSnackbar("Health check complete", { variant: "success" });
    },
    onError: (error) => {
      const timedOut =
        error?.transportCode === "ECONNABORTED" ||
        error?.code === "ECONNABORTED";
      if (timedOut) {
        enqueueSnackbar(HEALTH_CHECK_TIMEOUT_MESSAGE, { variant: "warning" });
        return;
      }
      enqueueSnackbar(healthCheckFailureMessage(error), { variant: "error" });
    },
    onSettled: () => {
      // An unreachable gateway is still a completed check. Refresh its status
      // and server timestamp on failure too, not just on success.
      queryClient.invalidateQueries({ queryKey: ["agentcc-gateways"] });
    },
  });
}
