import { QueryClient } from "@tanstack/react-query";

import { CAPABILITIES_QUERY_KEY } from "src/hooks/useCapabilities";

import { EARLY_ACCESS_KEY } from "../useEarlyAccess";

import { capabilitiesAnswer, earlyAccessAnswer } from "./rollout-answer";

export { capabilitiesAnswer, earlyAccessAnswer };

/**
 * A QueryClient whose capabilities query and Early access listing already hold
 * the answers for `options` (see rollout-answer.js). Pass `earlyAccess: false`
 * to leave the listing unread, so a test can drive its request.
 */
export function rolloutQueryClient(options = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  client.setQueryData(CAPABILITIES_QUERY_KEY, {
    data: capabilitiesAnswer(options),
  });
  if (options.earlyAccess !== false) {
    client.setQueryData(EARLY_ACCESS_KEY, {
      data: earlyAccessAnswer(options),
    });
  }
  return client;
}
