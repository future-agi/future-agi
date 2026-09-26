// The check list itself comes from GET /api/setup-checks/ — nothing about it
// lives here, so checks can change server-side without a frontend release.

// Mirrors the server's launch modes. Sent as the `mode` query param.
export const LAUNCH_MODE = {
  LIVE: "live",
  EXPERIMENT: "experiment",
};

export const LAUNCH_MODES = [
  {
    id: LAUNCH_MODE.LIVE,
    title: "Production",
    description:
      "You're going live for real missions. Every security and infrastructure system is checked before liftoff.",
    icon: "solar:rocket-2-bold",
  },
  {
    id: LAUNCH_MODE.EXPERIMENT,
    title: "Test flight",
    description:
      "You're taking it for a spin locally. A few non-critical systems are eased so you're off the ground in minutes.",
    icon: "solar:test-tube-bold",
  },
];

export const DEFAULT_LAUNCH_MODE = LAUNCH_MODE.LIVE;

export const MODE_NOTE = {
  [LAUNCH_MODE.LIVE]:
    "Every system this install runs has to pass pre-flight before you're cleared for launch.",
  [LAUNCH_MODE.EXPERIMENT]:
    "Cautions won't hold you on the ground during a test flight.",
};

// Mirrors the server enum. No status gates the flow.
export const CHECK_STATUS = {
  PENDING: "pending",
  PASSED: "passed",
  WARNING: "warning",
  FAILED: "failed",
  SKIPPED: "skipped",
};

// Derived from the transport, not from any check.
export const CONNECTION_STATE = {
  CONNECTING: "connecting",
  REACHABLE: "reachable",
  UNREACHABLE: "unreachable",
};

export const CHECK_REVEAL_STAGGER_MS = 350;

// Mirrors the server's `setup` field: which install answered.
export const SETUP = {
  STANDALONE: "standalone",
  DISTRIBUTED: "distributed",
  HELM: "helm",
};

export const SETUP_META = {
  [SETUP.STANDALONE]: {
    title: "Standalone setup",
    description:
      "Everything runs in one app container, next to Postgres and ClickHouse.",
    icon: "solar:server-square-linear",
  },
  [SETUP.DISTRIBUTED]: {
    title: "Distributed setup",
    description:
      "Every service runs in its own container, so each one scales on its own.",
    icon: "solar:widget-5-bold-duotone",
  },
  [SETUP.HELM]: {
    title: "Helm setup",
    description:
      "The Distributed setup on Kubernetes: every service runs in its own pods, so each one scales on its own.",
    icon: "solar:server-square-cloud-linear",
  },
};

// Null for a value this build does not know, so a newer server never breaks
// the screen.
export const getSetupMeta = (setup) =>
  Object.prototype.hasOwnProperty.call(SETUP_META, setup)
    ? SETUP_META[setup]
    : null;

// Where OTLP/HTTP lands when the server does not say (it predates
// `collector_http_url`): the default FI_COLLECTOR_OTLP_HTTP_PORT.
export const LOCAL_COLLECTOR_URL = "http://localhost:4318";

export const TRACING_DOCS_URL = "https://docs.futureagi.com/docs/observe";

// Shown once pre-flight clears. Backticks render as inline code.
export const nextSteps = ({ authenticated, collectorUrl }) => [
  {
    id: "account",
    icon: "solar:user-id-bold",
    text: authenticated
      ? "Continue to your workspace."
      : "Create your account on the next screen. You become the owner of a new workspace.",
  },
  {
    id: "keys",
    icon: "solar:key-bold",
    text: "Open Keys in the sidebar to copy your API key and secret key.",
  },
  {
    id: "trace",
    icon: "solar:code-square-linear",
    text: `Send your first trace from this machine: \`pip install fi-instrumentation-otel\`, set \`FI_API_KEY\`, \`FI_SECRET_KEY\` and \`FI_BASE_URL=${collectorUrl || LOCAL_COLLECTOR_URL}\`, then call \`register()\`.`,
    link: { href: TRACING_DOCS_URL, label: "Tracing guide" },
  },
];
