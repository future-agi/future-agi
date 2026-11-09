// Product-owned services are inventoried, NOT permitted by the strict gate.
// Their enablement is a product decision; the offline asset build never rewrites
// their code/config. Paths are relative to frontend and include source lines.
export const PRODUCT_NETWORK_ENABLEMENT = [
  {
    service: "PostHog",
    config: [
      "src/config-global.js:43",
      "src/config-global.js:44",
      "src/utils/PostHog/posthog.js:14",
    ],
    enabledBy:
      "VITE_POSTHOG_KEY; VITE_POSTHOG_HOST overrides the default API host",
  },
  {
    service: "Mixpanel",
    config: [
      "src/utils/Mixpanel/mixpanel.js:14",
      "src/utils/Mixpanel/mixpanel.js:19",
      "src/utils/Mixpanel/mixpanel.js:39",
      "src/config-global.js:42",
    ],
    enabledBy:
      "VITE_MIXPANEL_TOKEN; VITE_MIXPANEL_HOST override; recorder requires VITE_MIXPANEL_SESSION_REPLAY_PERCENT > 0 and an unblocked route",
  },
  {
    service: "Google Tag Manager",
    config: ["index.html:8", "index.html:9"],
    enabledBy: "VITE_GTM_ID",
  },
  {
    service: "reCAPTCHA",
    config: [
      "src/config-global.js:40",
      "src/main.jsx:271",
      "src/utils/recaptchaService.js:53",
    ],
    enabledBy: "VITE_GOOGLE_SITE_KEY passed to GoogleReCaptchaProvider",
  },
];

export function productNetworkContext(row) {
  // Only relabel already-failing occurrences. Documentation remains documentation;
  // this classification is not a hostname exemption and never clears fail=true.
  if (!row.fail) return row;
  let host;
  try {
    host = new URL(row.url.startsWith("//") ? `https:${row.url}` : row.url)
      .hostname;
  } catch {
    return row;
  }
  const service = /^(?:[a-z0-9-]+\.)*posthog\.com$/.test(host)
    ? "PostHog"
    : /^(?:[a-z0-9-]+\.)*(?:mixpanel\.com|mxpnl\.com)$/.test(host)
      ? "Mixpanel"
      : host === "www.googletagmanager.com"
        ? "Google Tag Manager"
        : ["www.google.com", "www.gstatic.com", "www.recaptcha.net"].includes(
              host,
            ) && /\/recaptcha\//.test(row.url)
          ? "reCAPTCHA"
          : null;
  if (!service) return row;
  return {
    ...row,
    category: "PRODUCT_TELEMETRY_AND_ANTIBOT",
    originalCategory: row.category,
    reason:
      "Product service URL; retained as a strict-inventory failure, not an asset exemption",
    service,
    enablement: PRODUCT_NETWORK_ENABLEMENT.find((v) => v.service === service),
  };
}
