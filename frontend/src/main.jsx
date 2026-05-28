import React from "react";
import { Suspense } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import "./utils/apexchartsCompat";

// Self-hosted Inter font — loads from bundle, no external request to Google Fonts
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";

import App from "./app";
import { SplashScreen } from "./components/loading-screen";
import {
  CellSelectionModule,
  ClipboardModule,
  MasterDetailModule,
  MenuModule,
  RichSelectModule,
  ServerSideRowModelApiModule,
  ServerSideRowModelModule,
  StatusBarModule,
} from "ag-grid-enterprise";
import { AllCommunityModule, ModuleRegistry } from "ag-grid-community";

ModuleRegistry.registerModules([
  AllCommunityModule,
  ServerSideRowModelModule,
  ServerSideRowModelApiModule,
  StatusBarModule,
  MasterDetailModule,
  RichSelectModule,
  MenuModule,
  ClipboardModule,
  CellSelectionModule,
]);
import { LicenseManager } from "ag-grid-enterprise";
import {
  CURRENT_ENVIRONMENT,
  GOOGLE_SITE_KEY,
  HOST_API,
  SENTRY_DSN,
} from "./config-global";
import { worker } from "./_mock/api/browser";
import * as Sentry from "@sentry/react";
import logger from "./utils/logger";
import { GoogleReCaptchaProvider } from "react-google-recaptcha-v3";
import { initPostHog } from "./utils/PostHog";
import { initGoogleAds } from "./utils/googleAds";
import { initReddit } from "./utils/redditAds";
import { initTwitter } from "./utils/twitterAds";

const IS_PRODUCTION = CURRENT_ENVIRONMENT === "production";
const DEFAULT_TRACES_SAMPLE_RATE = IS_PRODUCTION ? 0.1 : 1.0;
const tracesSampleRateEnv = import.meta.env.VITE_SENTRY_TRACES_SAMPLE_RATE;
const configuredTracesSampleRate = Number(
  tracesSampleRateEnv === "" ? undefined : tracesSampleRateEnv,
);
const sentryTracesSampleRate = Number.isFinite(configuredTracesSampleRate)
  ? configuredTracesSampleRate
  : DEFAULT_TRACES_SAMPLE_RATE;

// Browser/extension/network errors that are never actionable. Dropping them
// keeps the issue stream focused on real application bugs.
const SENTRY_IGNORE_ERRORS = [
  // Benign ResizeObserver loop notifications fired by many UI libraries
  "ResizeObserver loop limit exceeded",
  "ResizeObserver loop completed with undelivered notifications",
  // Stale chunk after a deploy - the app reloads to recover
  "Loading chunk",
  "Loading CSS chunk",
  "Failed to fetch dynamically imported module",
  "Importing a module script failed",
  // User connectivity / aborted navigations, not app faults
  "NetworkError when attempting to fetch resource",
  "Network request failed",
  "Failed to fetch",
  "TypeError: cancelled",
  "AbortError",
  "Non-Error promise rejection captured",
];

Sentry.init({
  dsn: SENTRY_DSN,
  // Do not attach IP/user PII automatically; user context is set explicitly
  // (and scrubbed) by the logger where it is actually needed.
  sendDefaultPii: false,
  environment: CURRENT_ENVIRONMENT || "development",
  release: import.meta.env.VITE_APP_VERSION || undefined,
  integrations: [
    Sentry.browserTracingIntegration(),
    // Mask text and media in session replays so we never stream customer
    // content (prompts, PII, uploads) to a third party.
    Sentry.replayIntegration({ maskAllText: true, blockAllMedia: true }),
  ],
  // 100% tracing in production is expensive and noisy; sample at 10% there and
  // keep full fidelity in lower environments. Override via VITE_SENTRY_TRACES_SAMPLE_RATE.
  tracesSampleRate: sentryTracesSampleRate,
  replaysSessionSampleRate: 0.1,
  replaysOnErrorSampleRate: 1.0,
  enabled: CURRENT_ENVIRONMENT !== "local",
  ignoreErrors: SENTRY_IGNORE_ERRORS,
  // Ignore noise injected by browser extensions / third-party scripts.
  denyUrls: [/extensions\//i, /^chrome:\/\//i, /^moz-extension:\/\//i],
  tracePropagationTargets: [HOST_API],
});

// Initialize PostHog (autocapture, session replay, web vitals)
initPostHog();

// Initialize Google Ads + GA4 (no-op if env vars are unset)
initGoogleAds();

// Initialize Reddit pixel (no-op if env vars are unset)
initReddit();

// Initialize Twitter (X) pixel (no-op if env vars are unset)
initTwitter();

if (
  CURRENT_ENVIRONMENT === "local" &&
  import.meta.env.VITE_ENABLE_MSW !== "false"
) {
  logger.debug("STARTING MOCK SERVER");
  worker.start({ onUnhandledRequest: "bypass" });
}

LicenseManager.setLicenseKey(import.meta.env.VITE_AG_GRID_LICENSE_KEY);

// Register service worker in production
if (CURRENT_ENVIRONMENT !== "local" && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/service-worker.js")
      .then((registration) => {
        logger.debug(
          "ServiceWorker registration successful with scope: ",
          registration.scope,
        );
        // Check for updates every 5 minutes
        setInterval(() => registration.update(), 5 * 60 * 1000);
        // When a new SW is found, activate it immediately
        registration.addEventListener("updatefound", () => {
          const newWorker = registration.installing;
          if (newWorker) {
            newWorker.addEventListener("statechange", () => {
              if (
                newWorker.state === "activated" &&
                navigator.serviceWorker.controller
              ) {
                // New SW activated — old cached chunks are now cleared
                logger.debug("New service worker activated, cache updated");
              }
            });
          }
        });
      })
      .catch((error) => {
        logger.error("ServiceWorker registration failed:", error);
      });
  });
}

// ----------------------------------------------------------------------

const root = ReactDOM.createRoot(document.getElementById("root"));

root.render(
  <HelmetProvider>
    <BrowserRouter>
      <GoogleReCaptchaProvider reCaptchaKey={GOOGLE_SITE_KEY}>
        <Suspense fallback={<SplashScreen />}>
          <App />
        </Suspense>
      </GoogleReCaptchaProvider>
    </BrowserRouter>
  </HelmetProvider>,
);
