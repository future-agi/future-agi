// The SIEM export drawer's choices (agentcc/models/siem.py Kind and STREAMS).
export const SIEM_KINDS = [
  { value: "hec", label: "HTTP Event Collector (HEC)" },
  { value: "webhook", label: "Webhook (signed HTTPS)" },
  { value: "otlp_http", label: "OTLP/HTTP collector" },
];

export const SIEM_STREAMS = [
  { value: "audit_events", label: "Audit events" },
  { value: "spans", label: "Spans" },
];
