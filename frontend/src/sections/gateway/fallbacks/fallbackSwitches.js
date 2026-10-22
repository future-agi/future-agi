// The reliability switches as the gateway runs them for an org's routing
// config (agentcc-gateway tenant.RoutingConfig and server/org_routing.go).
// The Fallbacks page and Org Config > Routing both read these, so the two
// pages always show the same value.

import { ROUTING_STRATEGY_OPTIONS } from "../constants/routing";

// routing.strategy unset: the org's API keys go to the first of its
// providers that serves the model, by provider ID (server/org_failover.go
// firstTarget, handlers.go orgRouteFor), not round robin. Shown as a choice
// of its own so both strategy controls state what runs; choosing it clears
// the stored strategy. Never written to the config.
export const NO_ROUTING_STRATEGY = "none";

export const ROUTING_STRATEGY_CHOICES = [
  { value: NO_ROUTING_STRATEGY, label: "None: first provider by ID" },
  ...ROUTING_STRATEGY_OPTIONS,
];

// The strategy choice a routing config runs: its strategy, else None.
export function routingStrategyChoice(routing) {
  return routing?.strategy || NO_ROUTING_STRATEGY;
}

const hasChains = (routing) =>
  Object.keys(routing?.model_fallbacks || routing?.modelFallbacks || {})
    .length > 0;

// Model fallback chains run when fallback_enabled is true, never when it is
// false, and, when it was never set, whenever the org has a chain.
export function fallbackChainsOn(routing) {
  const set = routing?.fallback_enabled ?? routing?.fallbackEnabled;
  if (set === true || set === false) return set;
  return hasChains(routing);
}

// Provider failover runs only when failover.enabled is true.
export function providerFailoverOn(routing) {
  return Boolean(routing?.failover?.enabled);
}

// Failover on timeout: a failover block without on_timeout runs with it off;
// with no failover block (failover turned on by the fallback switch) it is on.
export function failoverOnTimeout(routing) {
  const failover = routing?.failover;
  if (!failover) return true;
  return Boolean(failover.on_timeout ?? failover.onTimeout ?? false);
}

// Retry on timeout runs only when retry.on_timeout is true.
export function retryOnTimeout(routing) {
  const retry = routing?.retry;
  return Boolean(retry?.on_timeout ?? retry?.onTimeout ?? false);
}
