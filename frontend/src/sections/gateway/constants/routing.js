// Per-org routing strategies, named the way the gateway's org config names
// them (`tenant.RoutingConfig.Strategy`). Every screen that writes
// `routing.strategy` picks its options from here.
export const ROUTING_STRATEGY_OPTIONS = [
  { value: "round_robin", label: "Round Robin" },
  { value: "weighted", label: "Weighted" },
  { value: "least_latency", label: "Least Latency" },
  { value: "cost_optimized", label: "Cost Optimized" },
];

export const DEFAULT_ROUTING_STRATEGY = "round_robin";
