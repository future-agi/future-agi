import { TracerTraceAgentGraphResponse } from "src/generated/api-contracts/api.zod";

/**
 * Validate the complete Agent Graph HTTP response at the network boundary.
 *
 * Presentation code consumes the canonical snake_case result returned by the
 * generated contract. ``path_edges`` is retained as an empty compatibility
 * field until telemetry records authoritative execution-path transitions.
 * Missing metrics and legacy aliases are contract failures; none are repaired
 * or defaulted in the browser.
 */
export const parseAgentGraphResponse = (payload) => {
  const response = TracerTraceAgentGraphResponse.parse(payload);
  if (response.status !== true) {
    throw new Error("Agent Graph response was not successful");
  }
  return response.result;
};
