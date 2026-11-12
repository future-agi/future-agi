import axios, { endpoints } from "src/utils/axios";

// Initial status gates tab mounting. Bound the whole read, including adapters
// or interceptors that do not honor Axios's transport timeout, then release
// saved settings with an error (never infer MCP-off from a missed deadline).
// Allow Django's 10-second upstream timeout to report gateway_unreachable first.
const STATUS_DEADLINE_MS = 15000;
export async function readMCPStatus(gatewayId, signal) {
  const controller = new AbortController();
  let timer;
  let onAbort;
  const deadline = new Promise((_, reject) => {
    onAbort = () => {
      reject(new Error("MCP status request cancelled."));
      controller.abort();
    };
    signal?.addEventListener("abort", onAbort, { once: true });
    timer = setTimeout(() => {
      // NEW RL-1009 diagnostic; the existing status-error banner remains.
      reject(new Error("MCP status request timed out."));
      controller.abort();
    }, STATUS_DEADLINE_MS);
    if (signal?.aborted) onAbort();
  });
  try {
    const { data } = await Promise.race([
      axios.get(endpoints.gateway.mcpStatus(gatewayId), {
        signal: controller.signal,
        timeout: STATUS_DEADLINE_MS,
      }),
      deadline,
    ]);
    return data.result;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onAbort);
  }
}
