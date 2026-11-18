import { GA_ID } from "src/config-global";
import { ANALYTICS_ROUTE_SEGMENTS } from "src/utils/analyticsRouteSegments";

// Google Analytics 4, loaded directly with gtag.js. A no-op unless the build
// sets VITE_GA_ID, so self-hosted builds send nothing.
//
// Page views are sent manually, the way GitLab and Grafana's manual mode do
// it: the app decides what each page view contains instead of letting gtag.js
// read the raw browser address.
// - page_location: origin + path in which every segment that is not a literal
//   part of the app's routes (analyticsRouteSegments.js) becomes ":id", so
//   record IDs, end-user IDs and folder or dataset names are never sent;
//   only campaign query parameters (utm_*, gclid, gbraid, wbraid) are kept.
// - page_referrer: the previous page's redacted location; for the first page,
//   only the origin of an external referrer (index.html's no-referrer policy
//   already leaves same-origin referrers empty; any that arrive are dropped).
// - page_title: always "Future AGI" (titles can hold chat, agent or dataset
//   names).
// The same values are kept on the tag configuration, so the events gtag.js
// sends by itself (user_engagement, scroll, session_start) use them as well.
//
// Use a GA4 web stream made for the app: Enhanced Measurement's "Page changes
// based on browser history events" and "Site search" turned off (they read the
// raw address on Google's side), and no Google Ads destination on its Google
// tag (an Ads destination sends the raw address and title with every page
// view). Neither can be controlled from here.
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

function redactSegment(segment) {
  if (!segment) return segment;
  try {
    const decoded = decodeURIComponent(segment);
    return ANALYTICS_ROUTE_SEGMENTS.has(decoded) ? decoded : ":id";
  } catch {
    return ":id";
  }
}

export function analyticsPageLocation(href) {
  const url = new URL(href);
  const path = url.pathname
    .replace(/\/{2,}/g, "/")
    .split("/")
    .map(redactSegment)
    .join("/");
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
  // Repeated on every configuration call, so no later update can bring
  // Google signals or ads personalization back.
  const tagSettings = {
    send_page_view: false,
    allow_google_signals: false,
    allow_ad_personalization_signals: false,
    linker: { domains: LINKER_DOMAINS, accept_incoming: true },
  };
  let currentLocation = analyticsPageLocation(window.location.href);
  let currentReferrer = analyticsInitialReferrer(
    document.referrer,
    window.location.origin,
  );
  let onBlockedPage = false;

  const pageFields = () => ({
    page_location: currentLocation,
    page_referrer: currentReferrer,
    page_title: PAGE_TITLE,
  });

  const onNavigation = () => {
    const allowed = urlAllowed(window.location.href);
    window[disableKey] = !allowed;
    if (!allowed) {
      onBlockedPage = true;
      return;
    }
    const next = analyticsPageLocation(window.location.href);
    // Query or hash changes on the same page (filters, tabs) are not views,
    // but coming back from a blocked page is.
    if (next === currentLocation && !onBlockedPage) return;
    onBlockedPage = false;
    // The referrer is the last page analytics saw, never a blocked address.
    currentReferrer = currentLocation;
    currentLocation = next;
    gtag("config", id, { ...tagSettings, ...pageFields(), update: true });
    gtag("event", "page_view", pageFields());
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

  // Analytics only: no Google signals or ads personalization from the app,
  // even when the GA4 property is linked to Google Ads.
  gtag("js", new Date());
  gtag("config", id, { ...tagSettings, ...pageFields() });
  gtag("event", "page_view", pageFields());

  const script = document.createElement("script");
  script.async = true;
  script.src = `https://www.googletagmanager.com/gtag/js?id=${id}`;
  document.head.appendChild(script);
}
