import { GA_ID } from "src/config-global";

// Google Analytics 4, loaded directly with gtag.js. A no-op unless the build
// sets VITE_GA_ID, so self-hosted builds send nothing.
//
// index.html decides whether the page load may run analytics at all (no
// one-time links or sign-in parameters in the address) and exposes that check
// as window.__analyticsUrlAllowed. Later in-app navigation to such an address
// sets Google's documented opt-out flag (window["ga-disable-<id>"]) so no hit
// is sent for it. If index.html's check is missing, nothing loads.

const MEASUREMENT_ID = /^G-[A-Z0-9]+$/;

// Cross-domain linker so attribution carries over from futureagi.com.
const LINKER_DOMAINS = ["futureagi.com", "app.futureagi.com"];

let initialized = false;

export function initGoogleAnalytics() {
  if (initialized || typeof window === "undefined") return;
  const id = typeof GA_ID === "string" ? GA_ID.trim() : "";
  const urlAllowed = window.__analyticsUrlAllowed;
  if (!MEASUREMENT_ID.test(id) || typeof urlAllowed !== "function") return;
  if (window.__analyticsAllowedAtLoad !== true) return;
  initialized = true;

  // Installed before gtag.js loads, so these run inside gtag's own history
  // wrappers and listeners: the flag is current before it records a page.
  const disableKey = `ga-disable-${id}`;
  const syncDisabled = () => {
    window[disableKey] = !urlAllowed(window.location.href);
  };
  const { history } = window;
  ["pushState", "replaceState"].forEach((method) => {
    const original = history[method];
    history[method] = function analyticsUrlPolicy(...args) {
      const result = Reflect.apply(original, this, args);
      syncDisabled();
      return result;
    };
  });
  window.addEventListener("popstate", syncDisabled, true);
  window.addEventListener("hashchange", syncDisabled, true);
  syncDisabled();

  window.dataLayer = window.dataLayer || [];
  function gtag() {
    window.dataLayer.push(arguments);
  }
  window.gtag = gtag;
  gtag("js", new Date());
  gtag("config", id, {
    linker: { domains: LINKER_DOMAINS, accept_incoming: true },
  });

  const script = document.createElement("script");
  script.async = true;
  script.src = `https://www.googletagmanager.com/gtag/js?id=${id}`;
  document.head.appendChild(script);
}
