import { GA_ID } from "src/config-global";

// Google Analytics 4, loaded directly with gtag.js. A no-op unless the build
// sets VITE_GA_ID, so self-hosted builds send nothing.
//
// Page views are sent manually, the way GitLab and Grafana's manual mode do
// it: the app decides what each page view contains instead of letting gtag.js
// read the raw browser address.
// - page_location: origin + path with ID-like segments replaced by ":id",
//   keeping only campaign query parameters (utm_*, gclid, gbraid, wbraid).
// - page_referrer: the previous page's redacted location; for the first page,
//   only the origin of an external referrer (index.html's no-referrer policy
//   already leaves same-origin referrers empty; any that arrive are dropped).
// - page_title: always "Future AGI" (titles can hold chat, agent or dataset
//   names).
// The same values are kept on the tag configuration, so the events gtag.js
// sends by itself (user_engagement, scroll, session_start) use them as well.
//
// The GA4 web stream must have Enhanced Measurement's "Page changes based on
// browser history events" and "Site search" turned off: those read the raw
// address on Google's side and cannot be controlled from here.
//
// index.html decides whether the page load may run analytics at all (no
// one-time links or sign-in parameters in the address) and exposes that check
// as window.__analyticsUrlAllowed; without it nothing loads. In-app navigation
// to a blocked address sets Google's opt-out flag (window["ga-disable-<id>"])
// and sends nothing for it, and that address never becomes a referrer.

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

// First page only: an external referrer keeps just its origin; a same-origin
// one is dropped, since it may be a one-time link this page was opened from.
export function analyticsInitialReferrer(referrer, origin) {
  if (!referrer) return "";
  try {
    const url = new URL(referrer);
    if (url.origin === origin) return "";
    return url.protocol === "https:" || url.protocol === "http:"
      ? `${url.origin}/`
      : "";
  } catch {
    return "";
  }
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
  let currentLocation = analyticsPageLocation(window.location.href);
  let currentReferrer = analyticsInitialReferrer(
    document.referrer,
    window.location.origin,
  );

  const sendPageView = () => {
    const fields = {
      page_location: currentLocation,
      page_referrer: currentReferrer,
      page_title: PAGE_TITLE,
    };
    gtag("config", id, { ...fields, send_page_view: false, update: true });
    gtag("event", "page_view", fields);
  };

  const onNavigation = () => {
    const allowed = urlAllowed(window.location.href);
    window[disableKey] = !allowed;
    if (!allowed) return;
    const next = analyticsPageLocation(window.location.href);
    // Query or hash changes on the same page (filters, tabs) are not views.
    if (next === currentLocation) return;
    currentReferrer = currentLocation;
    currentLocation = next;
    sendPageView();
  };

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
    page_location: currentLocation,
    page_referrer: currentReferrer,
    page_title: PAGE_TITLE,
    send_page_view: false,
    linker: { domains: LINKER_DOMAINS, accept_incoming: true },
  });
  gtag("event", "page_view", {
    page_location: currentLocation,
    page_referrer: currentReferrer,
    page_title: PAGE_TITLE,
  });

  const script = document.createElement("script");
  script.async = true;
  script.src = `https://www.googletagmanager.com/gtag/js?id=${id}`;
  document.head.appendChild(script);
}
