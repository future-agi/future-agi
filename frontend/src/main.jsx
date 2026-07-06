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
import { applyTranslationDomGuard } from "./utils/translationDomGuard";

applyTranslationDomGuard();

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
  // Transient third-party / backend load failures: surfaced to the user via
  // snackbars but not actionable as Sentry issues (adblock/HTTP for Stripe.js,
  // a backend 500 for pricing-card-details, transient CDN fetch on SW update).
  "Failed to load Stripe.js",
  "Failed to get pricing card details",
  "Failed to update a ServiceWorker",
  // SW update() rejects with these when the worker is redundant or the
  // registration is gone (FRONTEND-194 / FRONTEND-1BF). Not actionable.
  "newestWorker is null",
  "Cannot update a null/nonexistent service worker registration",
  // Background request hit a 401 with no refresh token: the refresh helper
  // throws this and the user is logged out as expected (FRONTEND-1D9 / 1D7).
  "No refresh token",
  // Vite dev-server only: when the dep-optimizer re-bundles .vite/deps and
  // forces a reload, a transformed chunk can transiently inject the same
  // top-level declaration twice, throwing e.g. "Identifier 'PROJECT_SOURCE'
  // has already been declared". These are caught by React's dev
  // invokeguardedcallback with no stacktrace, so the .vite/deps frame filter
  // in beforeSend below never matches them. A real prod build fails at build
  // time, so this message never originates from a shipped bundle.
  "has already been declared",
];

Sentry.init({
  dsn: SENTRY_DSN,
  // Do not attach IP/user PII automatically; user context is set explicitly
  // (and scrubbed) by the logger where it is actually needed.
  sendDefaultPii: false,
  environment: CURRENT_ENVIRONMENT || "development",
  release: import.meta.env.VITE_APP_VERSION || undefined,
  integrations: [
    Sentry.browserTracingIntegration({
      // Don't instrument third-party marketing/analytics beacons (Google Ads,
      // GTM, GA, Reddit/Twitter pixels) — their repeated conversion pings get
      // flagged as N+1 API-call perf issues even though they aren't app fetches.
      shouldCreateSpanForRequest: (url) =>
        !/(?:googleadservices|googletagmanager|google-analytics|doubleclick|googlesyndication|redditstatic|reddit\.com|ads-twitter|t\.co|analytics\.twitter)\.com/i.test(
          url,
        ),
    }),
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
  // Drop Vite dev-server dep-optimizer artifacts: when Vite re-bundles
  // .vite/deps it forces a reload, and an in-flight dep chunk can transiently
  // be null (e.g. "Cannot read properties of null (reading 'useContext')").
  // These frames only exist under /node_modules/.vite/deps/* in dev, never in a
  // production build, so genuine production errors stay reported.
  beforeSend(event) {
    const fromViteDeps = event?.exception?.values?.some((value) =>
      value?.stacktrace?.frames?.some((frame) =>
        frame?.filename?.includes("/.vite/deps/"),
      ),
    );
    if (fromViteDeps) return null;
    // Drop errors originating entirely inside the Mixpanel/rrweb session
    // recorder. These surface as generic "reading 'logs'" TypeErrors thrown
    // from third-party recorder internals (no app frames) and are not
    // actionable application bugs.
    const fromSessionRecorder = event?.exception?.values?.some((value) =>
      value?.stacktrace?.frames?.some((frame) =>
        /recording-registry\.js|session-recording\.js|rrweb-compiled\.js/.test(
          frame?.filename || "",
        ),
      ),
    );
    if (fromSessionRecorder) return null;
    return event;
  },
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
        setInterval(
          () => {
            // Transient CDN/network failures fetching service-worker.js are
            // non-fatal; swallow so they don't surface as unhandled rejections.
            // Guard against a missing registration and a synchronous throw:
            // some browsers (Safari) throw InvalidStateError synchronously from
            // update() when the worker is redundant, which .catch() can't cover.
            try {
              registration?.update().catch((err) => {
                logger.debug("ServiceWorker update check failed:", err);
              });
            } catch (err) {
              logger.debug("ServiceWorker update check threw:", err);
            }
          },
          5 * 60 * 1000,
        );
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
        // SW registration/install failures (extension-wrapped "Rejected",
        // transient install errors) are non-actionable. Record a breadcrumb
        // instead of a Sentry error (FRONTEND-1DQ / FRONTEND-13X).
        logger.warn("ServiceWorker registration failed:", error);
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
