import { CAPABILITY } from "src/hooks/useCapabilities";

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
  "cloud_storage",
  "message_queue",
  "linear",
];

// Platforms that export AI Gateway request logs (integrations/temporal/activities.py
// EXPORT_PLATFORMS). Their first sync exports the last 24 hours, or from the
// date chosen, and each sync after it the request logs that arrived since.
export const EXPORT_PLATFORMS = [
  "datadog",
  "posthog",
  "mixpanel",
  "cloud_storage",
  "message_queue",
];

// Platforms that skip sync settings (no polling / backfill — action-only integrations)
export const SKIP_SYNC_SETTINGS_PLATFORMS = ["linear", "slack"];

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
  },
  {
    id: "datadog",
    name: "Datadog",
    description: "AI Gateway request logs and metrics",
    wizardDescription: "Export AI Gateway request logs and metrics to Datadog",
    logo: "/assets/icons/integrations/datadog.svg",
  },
  {
    id: "posthog",
    name: "PostHog",
    description: "AI Gateway request logs as PostHog events",
    wizardDescription: "Export AI Gateway request logs to PostHog as events",
    logo: "/assets/icons/integrations/posthog.svg",
  },
  {
    id: "pagerduty",
    name: "PagerDuty",
    description: "Incident management and on-call alerting",
    wizardDescription: "Route AI Gateway alerts to PagerDuty incidents",
    logo: "/assets/icons/integrations/pagerduty.svg",
  },
  {
    id: "mixpanel",
    name: "Mixpanel",
    description: "AI Gateway request logs as Mixpanel events",
    wizardDescription: "Export AI Gateway request logs to Mixpanel as events",
    logo: "/assets/icons/integrations/mixpanel.svg",
  },
  {
    id: "cloud_storage",
    name: "Cloud Storage",
    description: "Request log archival in Amazon S3",
    wizardDescription: "Archive AI Gateway request logs in Amazon S3",
    logo: "/assets/icons/integrations/cloud-storage.svg",
  },
  {
    id: "message_queue",
    name: "Message Queue",
    description: "SQS or Pub/Sub real-time log streaming",
    wizardDescription: "Publish request logs to a message queue",
    logo: "/assets/icons/integrations/message-queue.svg",
  },
  {
    id: "slack",
    name: "Slack",
    description: "Send Error Feed alerts to a Slack channel",
    wizardDescription: "Connect a Slack workspace for Error Feed alerts",
    logo: null,
    icon: "simple-icons:slack",
    // eslint-disable-next-line no-restricted-syntax -- ds: brand (Slack)
    iconColor: "#E01E5A",
  },
  {
    id: "linear",
    name: "Linear",
    description: "Issue tracking for Error Feed clusters",
    wizardDescription: "Create Linear issues from Error Feed clusters",
    logo: null,
    icon: "simple-icons:linear",
    // eslint-disable-next-line no-restricted-syntax -- ds: brand (Linear)
    iconColor: "#5E6AD2",
  },
];

// Destinations the Add Integration flow does not offer. A destination is
// offered once an end-to-end flow (e2e/flows/integrations/) shows the
// product delivering to it; it comes back by deleting its entry here.
// "<platform>" leaves a platform out of the page grid and the wizard;
// "<platform>/<sub_platform>" leaves one choice out of its credentials step.
// A connection that already exists for one of them stays listed and
// manageable on the Integrations page.
export const UNLISTED_DESTINATIONS = [
  "langfuse",
  "pagerduty",
  "message_queue",
  "cloud_storage/azure_blob",
  "cloud_storage/gcs",
];

export const isDestinationListed = (platform, subPlatform) =>
  !UNLISTED_DESTINATIONS.includes(platform) &&
  !(
    subPlatform && UNLISTED_DESTINATIONS.includes(`${platform}/${subPlatform}`)
  );

// The platforms the page grid and the wizard can offer.
export const LISTED_PLATFORMS = PLATFORMS.filter((platform) =>
  isDestinationListed(platform.id),
);

// A platform whose one use is a feature the deployment may not allow is
// offered only where the deployment's capabilities allow that feature:
// Linear issues are created from Error Feed clusters.
export const PLATFORM_CAPABILITY = { linear: CAPABILITY.ERROR_FEED };

// The listed platforms a deployment offers, given which capabilities it allows.
export const offeredPlatforms = (isAllowed) =>
  LISTED_PLATFORMS.filter((platform) => {
    const capability = PLATFORM_CAPABILITY[platform.id];
    return !capability || isAllowed(capability);
  });

// The name a new connection gets: its storage or queue choice, or its platform.
const SUB_PLATFORM_NAMES = {
  s3: "Amazon S3",
  azure_blob: "Azure Blob",
  gcs: "Google Cloud Storage",
  sqs: "Amazon SQS",
  pubsub: "Google Pub/Sub",
};
export const connectionName = (platform, credentials) =>
  SUB_PLATFORM_NAMES[credentials?.sub_platform] ||
  PLATFORMS.find((p) => p.id === platform)?.name ||
  platform;

// The credentials an export's Edit dialog replaces, by the key its
// credentials step stores them under (the backend's EDITABLE_CREDENTIALS in
// futureagi/integrations/views/integration_connection.py). They are checked
// with the platform before they are saved.
export const EDITABLE_CREDENTIALS = {
  datadog: [{ key: "api_key", label: "API Key", secret: true }],
  posthog: [{ key: "api_key", label: "Project API Key", secret: true }],
  mixpanel: [
    { key: "project_token", label: "Project Token", secret: true },
    { key: "api_secret", label: "API Secret", secret: true },
  ],
  cloud_storage: [
    { key: "access_key_id", label: "Access Key ID", secret: false },
    { key: "secret_access_key", label: "Secret Access Key", secret: true },
  ],
};

export const platformName = (platform) =>
  PLATFORMS.find((p) => p.id === platform)?.name || platform;
