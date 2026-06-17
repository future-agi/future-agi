export const DEFAULT_LANGFUSE_HOST = "https://cloud.langfuse.com";

export const SYNC_INTERVALS = [
  { value: 60, label: "Every minute" },
  { value: 120, label: "Every 2 minutes" },
  { value: 300, label: "Every 5 minutes" },
  { value: 600, label: "Every 10 minutes" },
  { value: 900, label: "Every 15 minutes" },
  { value: 1800, label: "Every 30 minutes" },
];

// Platforms that use custom credential forms (not public_key/secret_key)
export const CUSTOM_CREDENTIAL_PLATFORMS = [
  "datadog",
  "posthog",
  "pagerduty",
  "mixpanel",
  "cloud_storage",
  "message_queue",
  "linear",
];

// Platforms that skip the project mapping step
export const SKIP_PROJECT_MAPPING_PLATFORMS = [
  "datadog",
  "posthog",
  "mixpanel",
  "message_queue",
  "linear",
];

// Platforms that skip sync settings (no polling / backfill — action-only integrations)
export const SKIP_SYNC_SETTINGS_PLATFORMS = ["linear"];

export const DATADOG_SITES = [
  { value: "us1", label: "US1 (datadoghq.com)", domain: "datadoghq.com" },
  {
    value: "us3",
    label: "US3 (us3.datadoghq.com)",
    domain: "us3.datadoghq.com",
  },
  {
    value: "us5",
    label: "US5 (us5.datadoghq.com)",
    domain: "us5.datadoghq.com",
  },
  { value: "eu1", label: "EU1 (datadoghq.eu)", domain: "datadoghq.eu" },
  {
    value: "ap1",
    label: "AP1 (ap1.datadoghq.com)",
    domain: "ap1.datadoghq.com",
  },
  {
    value: "us1-fed",
    label: "US1-FED (ddog-gov.com)",
    domain: "ddog-gov.com",
  },
];

export const PLATFORMS = [
  {
    id: "langfuse",
    name: "Langfuse",
    description: "Open-source LLM observability platform",
    wizardDescription: "Import traces, spans, and scores from Langfuse",
    logo: "/assets/icons/integrations/langfuse.svg",
    available: true,
  },
  {
    id: "datadog",
    name: "Datadog",
    description: "APM, metrics, logs, and distributed traces",
    wizardDescription:
      "Export Agent Command Center metrics, logs, and traces to Datadog",
    logo: "/assets/icons/integrations/datadog.svg",
    available: true,
  },
  {
    id: "posthog",
    name: "PostHog",
    description: "Open-source product analytics",
    wizardDescription: "Export LLM usage events to PostHog",
    logo: "/assets/icons/integrations/posthog.svg",
    available: true,
  },
  {
    id: "pagerduty",
    name: "PagerDuty",
    description: "Incident management and on-call alerting",
    wizardDescription:
      "Route Agent Command Center alerts to PagerDuty incidents",
    logo: "/assets/icons/integrations/pagerduty.svg",
    available: true,
  },
  {
    id: "mixpanel",
    name: "Mixpanel",
    description: "Product analytics and user tracking",
    wizardDescription: "Export LLM usage events to Mixpanel",
    logo: "/assets/icons/integrations/mixpanel.svg",
    available: true,
  },
  {
    id: "cloud_storage",
    name: "Cloud Storage",
    description: "S3, Azure Blob, or GCS log archival",
    wizardDescription: "Ship request logs to cloud object storage",
    logo: "/assets/icons/integrations/cloud-storage.svg",
    available: true,
  },
  {
    id: "message_queue",
    name: "Message Queue",
    description: "SQS or Pub/Sub real-time log streaming",
    wizardDescription: "Publish request logs to a message queue",
    logo: "/assets/icons/integrations/message-queue.svg",
    available: true,
  },
  {
    id: "linear",
    name: "Linear",
    description: "Issue tracking for Error Feed clusters",
    wizardDescription: "Create Linear issues from Error Feed clusters",
    logo: null,
    icon: "simple-icons:linear",
    iconColor: "#5E6AD2",
    available: true,
  },
  {
    id: "langsmith",
    name: "LangSmith",
    description: "LangChain's tracing and evaluation platform",
    wizardDescription: "Import traces from LangSmith",
    logo: "/assets/icons/integrations/langsmith.svg",
    available: false,
  },
  {
    id: "arize",
    name: "Arize",
    description: "ML observability and monitoring",
    wizardDescription: "Import traces from Arize Phoenix",
    logo: "/assets/icons/integrations/arize.svg",
    available: false,
  },
];
