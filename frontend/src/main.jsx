import React from "react";
import { Suspense } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";

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

Sentry.init({
  dsn: SENTRY_DSN,
  sendDefaultPii: true,
  environment:
    CURRENT_ENVIRONMENT === "production" ? "production" : "development",
  integrations: [
    Sentry.browserTracingIntegration(),
    Sentry.replayIntegration({ maskAllText: false, blockAllMedia: false }),
  ],
  tracesSampleRate: 1.0,
  replaysSessionSampleRate: 0.1,
  replaysOnErrorSampleRate: 1.0,
  enabled: CURRENT_ENVIRONMENT !== "local",
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

if (CURRENT_ENVIRONMENT === "local") {
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
