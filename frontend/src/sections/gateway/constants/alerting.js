// The only metrics the gateway's alerting plugin records — see `collectMetrics`
// in agentcc-gateway/internal/plugins/alerting/alerting.go. A rule on any other
// metric is accepted and then never evaluated, so every surface that writes
// `alerting.rules` picks its options from here.
export const ALERT_METRIC_OPTIONS = [
  { value: "error_count", label: "Error Count" },
  { value: "request_count", label: "Request Count" },
  { value: "cost_total", label: "Total Cost ($)" },
  { value: "latency_avg", label: "Avg Latency (ms)" },
  { value: "tokens_total", label: "Total Tokens" },
];

export const DEFAULT_ALERT_METRIC = "error_count";

// The only channel types the gateway's alerting plugin can deliver to — see
// the channel switch in the same file. Any other type is accepted and then
// skipped with a log line.
export const ALERT_CHANNEL_TYPE_OPTIONS = [
  { value: "webhook", label: "Webhook" },
  { value: "slack", label: "Slack" },
  { value: "log", label: "Log Only" },
];
