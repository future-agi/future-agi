import { GA_ID } from "src/config-global";
import { analyticsRoutePath } from "src/utils/analyticsRouteSegments";

// Google Analytics 4, loaded directly with gtag.js. A no-op unless the build
// sets VITE_GA_ID, so self-hosted builds send nothing.
//
// Page views are sent manually: the app decides what each page view contains
// instead of letting gtag.js read the raw browser address.
// - page_location: origin + the matched route template (IDs and splats become
//   ":id"); only campaign parameters are kept, never the fragment.
// - page_referrer: the previous page's redacted location; for the first page,
//   only the origin of an external referrer.
// - page_title: always "Future AGI" (titles can contain user-supplied names).
// The same values are kept on the tag configuration for automatic GA events.
//
// Use an app-only GA4 web stream: turn off Enhanced Measurement's history page
// changes and site search, and do not attach a Google Ads destination. Those
// features can read the raw address and cannot be controlled from here.

const MEASUREMENT_ID = /^G-[A-Z0-9]+$/;
const PAGE_TITLE = "Future AGI";

// Repeated on every configuration call so an update cannot re-enable ads.
const TAG_SETTINGS = {
  send_page_view: false,
  allow_google_signals: false,
  allow_ad_personalization_signals: false,
  linker: {
    domains: ["futureagi.com", "app.futureagi.com"],
    accept_incoming: true,
  },
};

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

export function analyticsPageLocation(href, routes) {
  const url = new URL(href);
  const path = analyticsRoutePath(routes, url.pathname);
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

function allowedNow() {
  try {
    return (
      window.__analyticsAllowedAtLoad === true &&
      window.__analyticsUrlAllowed?.(window.location.href) === true
    );
  } catch {
    return false;
  }
}

// The history policy lasts for the document: a direct push/replace or a POP to
// a blocked address must opt out immediately, even if React hasn't committed
// the destination yet. Only a committed router supplies a tree for reporting.
function startTracker(id) {
  const disableKey = `ga-disable-${id}`;
  let routes;
  let lastPathname; // Local identity only; never passed to gtag.
  let lastReportedLocation;
  let wasBlocked = false;
  let jsQueued = false;
  let script;
  let failed = false;

  // A queue can throw after consuming a command. Never retry in this document,
  // even if a React effect rebinds the tracker or a history hook is still live.
  function disable() {
    failed = true;
    try {
      window[disableKey] = true;
    } catch {
      // The opt-out property itself may be unwritable; our dispatch stays off.
    }
  }

  function safely(action) {
    if (failed) return;
    try {
      action();
    } catch {
      disable();
    }
  }

  function currentQueue() {
    const queue = window.dataLayer;
    if (!Array.isArray(queue) || typeof queue.push !== "function") {
      throw new TypeError("Invalid analytics queue");
    }
    return queue;
  }

  function gtag() {
    safely(() => {
      currentQueue().push(arguments);
      currentQueue(); // A synchronous consumer may have replaced the queue.
    });
  }

  function reportNavigation() {
    currentQueue(); // Check even on a same-path rebind, before clearing opt-out.
    const allowed = allowedNow();
    window[disableKey] = !allowed || !routes;
    if (!allowed) {
      wasBlocked = true;
      return;
    }
    if (!routes) return;

    const { href, pathname, origin } = window.location;
    if (pathname === lastPathname && !wasBlocked) return;
    const first = lastReportedLocation === undefined;
    const fields = {
      page_location: analyticsPageLocation(href, routes),
      page_referrer: first
        ? analyticsInitialReferrer(document.referrer, origin)
        : lastReportedLocation,
      page_title: PAGE_TITLE,
    };
    lastPathname = pathname;
    wasBlocked = false;
    // An existing GTM container can process a dataLayer push synchronously.
    // Do not finish dispatching a view if a push navigated away or vetoed it.
    const stillCurrent = () =>
      !failed &&
      routes &&
      window.location.pathname === pathname &&
      allowedNow();
    if (!jsQueued) {
      jsQueued = true;
      gtag("js", new Date());
    }
    if (!stillCurrent()) return;
    gtag("config", id, {
      ...TAG_SETTINGS,
      ...fields,
      ...(!first && { update: true }),
    });
    if (!stillCurrent()) return;
    lastReportedLocation = fields.page_location;
    gtag("event", "page_view", fields);

    if (script && stillCurrent()) {
      window.__analyticsTagStarted = true;
      document.head.appendChild(script);
      script = undefined;
    }
  }

  function onNavigation() {
    safely(reportNavigation);
  }

  // The load-time veto is sticky; the address may also have changed while
  // the app was loading. Check both before exposing the queue or adding hooks.
  if (!allowedNow()) return undefined;
  safely(() => {
    if (window.dataLayer === undefined) window.dataLayer = [];
    currentQueue();
    script = document.createElement("script");
    script.async = true;
    script.src = `https://www.googletagmanager.com/gtag/js?id=${id}`;
    script.onerror = disable;
    window.gtag = gtag;
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
  });

  return (committedRoutes) => {
    routes = committedRoutes;
    onNavigation(); // First view (before a <Navigate> effect), or a rebind.
    return () => {
      if (routes === committedRoutes) {
        routes = undefined;
        safely(() => {
          window[disableKey] = true;
        });
      }
    };
  };
}

let bindRoutes;

export function initGoogleAnalytics(routes) {
  if (typeof window === "undefined" || !routes?.length) return undefined;
  if (!bindRoutes) {
    const id = typeof GA_ID === "string" ? GA_ID.trim() : "";
    if (!MEASUREMENT_ID.test(id) || !allowedNow()) return undefined;
    bindRoutes = startTracker(id);
  }
  return bindRoutes?.(routes);
}
