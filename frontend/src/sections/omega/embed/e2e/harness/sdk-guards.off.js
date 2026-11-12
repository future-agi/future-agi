// Negative control for the e2e sink test: the same exports as
// telemetry/sdk-guards.js, all pass-through. The control build swaps this in
// (and drops the index.html intake script) to prove the sink sees a leak.
export const POSTHOG_AUTOCAPTURED_EVENTS = [];
export const posthogBeforeSend = (event) => event;
export const posthogMaskNetworkRequest = (request) => request;
export const POSTHOG_AUTOCAPTURE_URL_IGNORELIST = [];
export const POSTHOG_REPLAY_URL_BLOCKLIST = [];
export const shouldDropMixpanelEvent = () => false;
export const normalizeMixpanelProperties = (properties) => properties;
export const scrubMixpanelProperties = (properties) => properties;
export const SENTRY_REPLAY_BLOCK_SELECTORS = [];
export const sentryShouldTraceRequest = () => true;
export const sentryBeforeBreadcrumb = (breadcrumb) => breadcrumb;
export const sentryScrubEvent = (event) => event;
export const sentryBeforeAddRecordingEvent = (event) => event;
export const sentryScrubRecordingEvent = (event) => event;
export const NEW_RELIC_OBFUSCATE_RULES = [];
export const NEW_RELIC_LOCAL_DENY_LIST = [];
