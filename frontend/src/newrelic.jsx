// Populate using values from the New Relic JavaScript snippet.
// NewRelic browser license keys (NRJS-*) are safe to ship client-side — they
// are embedded in the browser agent and designed to be public. Still, they
// are sourced from env vars so OSS users can point at their own NR account.

const licenseKey = import.meta.env.VITE_NEWRELIC_LICENSE_KEY || "";
const accountID = import.meta.env.VITE_NEWRELIC_ACCOUNT_ID || "";
const trustKey = import.meta.env.VITE_NEWRELIC_TRUST_KEY || accountID;
const devAppID = import.meta.env.VITE_NEWRELIC_DEV_APP_ID || "";
const devAgentID = import.meta.env.VITE_NEWRELIC_DEV_AGENT_ID || devAppID;
const prodAppID = import.meta.env.VITE_NEWRELIC_PROD_APP_ID || "";
const prodAgentID = import.meta.env.VITE_NEWRELIC_PROD_AGENT_ID || prodAppID;

export const devTracing = {
  init: {
    distributed_tracing: { enabled: true },
    privacy: { cookies_enabled: true },
    ajax: { deny_list: ["bam.nr-data.net"] },
  },
  info: {
    beacon: "bam.nr-data.net",
    errorBeacon: "bam.nr-data.net",
    licenseKey,
    applicationID: devAppID,
    sa: 1,
  },
  loader_config: {
    accountID,
    trustKey,
    agentID: devAgentID,
    licenseKey,
    applicationID: devAppID,
  },
};

export const prodTracing = {
  init: {
    distributed_tracing: { enabled: true },
    privacy: { cookies_enabled: true },
    ajax: { deny_list: ["bam.nr-data.net"] },
  },
  info: {
    beacon: "bam.nr-data.net",
    errorBeacon: "bam.nr-data.net",
    licenseKey,
    applicationID: prodAppID,
    sa: 1,
  },
  loader_config: {
    accountID,
    trustKey,
    agentID: prodAgentID,
    licenseKey,
    applicationID: prodAppID,
  },
};

// The tracing config for this build's environment, or null without a license
// key and application ID. Without them the agent sends nothing, but it still
// wraps fetch, XHR and history on every page, so it is not started at all. The
// published images are built without them.
export const newRelicTracing = (environment) => {
  const tracing = { production: prodTracing, dev: devTracing }[environment];
  return tracing?.info.licenseKey && tracing.info.applicationID
    ? tracing
    : null;
};
