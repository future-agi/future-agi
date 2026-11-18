import { GA_ID } from "src/config-global";

// Google Analytics 4, loaded directly with gtag.js. A no-op unless the build
// sets VITE_GA_ID, so self-hosted builds send nothing.
//
// index.html decides whether the page load may run analytics at all (no
// one-time links or sign-in parameters in the address) and exposes that check
// as window.__analyticsUrlAllowed. If that check is missing, nothing loads.
//
// What GA4 receives:
// - page_location: origin + path with ID-like segments replaced by ":id",
//   keeping only campaign query parameters (utm_*, gclid, gbraid, wbraid). No
//   fragment, other query parameters, emails or record IDs.
// - page_title: always "Future AGI" (page titles can hold user content such as
//   chat, agent or dataset names).
// Both are set on the tag configuration and refreshed after every in-app
// navigation, so the page views GA4 records on history changes use them too.
// In-app navigation to a blocked address also sets Google's documented
// opt-out flag (window["ga-disable-<id>"]), so nothing is sent while there.

const MEASUREMENT_ID = /^G-[A-Z0-9]+$/;
const PAGE_TITLE = "Future AGI";

// Cross-domain linker so attribution carries over from futureagi.com.
const LINKER_DOMAINS = ["futureagi.com", "app.futureagi.com"];

const CAMPAIGN_PARAMS = new Set([
  "utm_source",
  "utm_medium",
  "utm_campaign",
  "utm_term",
  "utm_content",
  "utm_id",
  "gclid",
  "gbraid",
  "wbraid",
]);

// UUIDs, long hex or digit runs, and long opaque tokens.
const ID_SEGMENT =
  /^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{16,}|\d{4,}|[A-Za-z0-9_-]{24,})$/i;

function redactSegment(segment) {
  if (!segment) return segment;
  let decoded = segment;
  try {
    decoded = decodeURIComponent(segment);
  } catch {
    return ":id";
  }
  return ID_SEGMENT.test(decoded) || decoded.includes("@") ? ":id" : segment;
}

export function analyticsPageLocation(href) {
  const url = new URL(href);
  const path = url.pathname.split("/").map(redactSegment).join("/");
  const kept = new URLSearchParams();
  url.searchParams.forEach((value, key) => {
    if (CAMPAIGN_PARAMS.has(key)) kept.append(key, value);
  });
  const query = kept.toString();
  return `${url.origin}${path}${query ? `?${query}` : ""}`;
}

let initialized = false;

export function initGoogleAnalytics() {
  if (initialized || typeof window === "undefined") return;
  const id = typeof GA_ID === "string" ? GA_ID.trim() : "";
  const urlAllowed = window.__analyticsUrlAllowed;
  if (!MEASUREMENT_ID.test(id) || typeof urlAllowed !== "function") return;
  if (window.__analyticsAllowedAtLoad !== true) return;
  initialized = true;

  window.dataLayer = window.dataLayer || [];
  function gtag() {
    window.dataLayer.push(arguments);
  }
  window.gtag = gtag;

  const disableKey = `ga-disable-${id}`;
  const pageFields = () => ({
    page_location: analyticsPageLocation(window.location.href),
    page_title: PAGE_TITLE,
  });
  const onNavigation = () => {
    const allowed = urlAllowed(window.location.href);
    window[disableKey] = !allowed;
    if (allowed) {
      gtag("config", id, {
        ...pageFields(),
        send_page_view: false,
        update: true,
      });
    }
  };

  // Installed before gtag.js loads, so these run inside gtag's own history
  // wrappers and listeners: the flag and page fields are current before it
  // records the page view for a history change.
  const { history } = window;
  ["pushState", "replaceState"].forEach((method) => {
    const original = history[method];
    history[method] = function analyticsUrlPolicy(...args) {
      const result = Reflect.apply(original, this, args);
      onNavigation();
      return result;
    };
  });
  window.addEventListener("popstate", onNavigation, true);
  window.addEventListener("hashchange", onNavigation, true);
  window[disableKey] = false;

  gtag("js", new Date());
  gtag("config", id, {
    ...pageFields(),
    linker: { domains: LINKER_DOMAINS, accept_incoming: true },
  });

  const script = document.createElement("script");
  script.async = true;
  script.src = `https://www.googletagmanager.com/gtag/js?id=${id}`;
  document.head.appendChild(script);
}
