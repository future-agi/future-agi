import { paths } from "src/routes/paths";

// API
// ----------------------------------------------------------------------

// Frontend hostname → API hostname
const API_HOST_MAP = {
  "app.futureagi.com": "https://api.futureagi.com",
  "www.futureagi.com": "https://api.futureagi.com",
  "dev.futureagi.com": "https://dev.api.futureagi.com",
  "us.futureagi.com": "https://api-us.futureagi.com",
  "eu.futureagi.com": "https://api-eu.futureagi.com",
};

function resolveApiHost() {
  // Runtime > build-time > hostname map > dev default. See frontend/docker-entrypoint.sh.
  const runtime =
    (typeof window !== "undefined" && window.__FUTURE_AGI_CONFIG__) || {};
  if (runtime.VITE_HOST_API) return runtime.VITE_HOST_API;
  if (import.meta.env.VITE_HOST_API) return import.meta.env.VITE_HOST_API;
  return API_HOST_MAP[window.location.hostname] || "http://localhost:8000";
}

export const HOST_API = resolveApiHost();
export const GENERATE_LINK = import.meta.env.VITE_GENERATE_LINK;
export const ASSETS_API = import.meta.env.VITE_ASSETS_API;
export const CURRENT_ENVIRONMENT = import.meta.env.VITE_ENVIRONMENT;
export const GOOGLE_SITE_KEY = import.meta.env.VITE_GOOGLE_SITE_KEY;
export const SENTRY_DSN = import.meta.env.VITE_SENTRY_DSN;
export const MIXPANEL_HOST = import.meta.env.VITE_MIXPANEL_HOST;
export const POSTHOG_KEY = import.meta.env.VITE_POSTHOG_KEY;
export const POSTHOG_HOST = import.meta.env.VITE_POSTHOG_HOST;

export const GOOGLE_ADS_ID = import.meta.env.VITE_GOOGLE_ADS_ID;
export const GOOGLE_ADS_SIGNUP_LABEL = import.meta.env.VITE_GOOGLE_ADS_SIGNUP_LABEL;
export const GOOGLE_ADS_ENABLED = import.meta.env.VITE_GOOGLE_ADS_ENABLED;
export const GA_ID = import.meta.env.VITE_GA_ID;

export const REDDIT_PIXEL_ID = import.meta.env.VITE_REDDIT_PIXEL_ID;
export const REDDIT_ADS_ENABLED = import.meta.env.VITE_REDDIT_ADS_ENABLED;

export const TWITTER_PIXEL_ID = import.meta.env.VITE_TWITTER_PIXEL_ID;
export const TWITTER_ADS_ENABLED = import.meta.env.VITE_TWITTER_ADS_ENABLED;


export const AD_CONVERSION_VALUE = (() => {
  const n = Number(import.meta.env.VITE_AD_CONVERSION_VALUE);
  return Number.isFinite(n) ? n : undefined;
})();
export const AD_CONVERSION_CURRENCY = import.meta.env.VITE_AD_CONVERSION_CURRENCY;

export const FIREBASE_API = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  appId: import.meta.env.VITE_FIREBASE_APPID,
  measurementId: import.meta.env.VITE_FIREBASE_MEASUREMENT_ID,
};

export const AMPLIFY_API = {
  userPoolId: import.meta.env.VITE_AWS_AMPLIFY_USER_POOL_ID,
  userPoolWebClientId: import.meta.env.VITE_AWS_AMPLIFY_USER_POOL_WEB_CLIENT_ID,
  region: import.meta.env.VITE_AWS_AMPLIFY_REGION,
};

export const AUTH0_API = {
  clientId: import.meta.env.VITE_AUTH0_CLIENT_ID,
  domain: import.meta.env.VITE_AUTH0_DOMAIN,
  callbackUrl: import.meta.env.VITE_AUTH0_CALLBACK_URL,
};

export const MAPBOX_API = import.meta.env.VITE_MAPBOX_API;

// ROOT PATH AFTER LOGIN SUCCESSFUL
export const PATH_AFTER_LOGIN = paths.dashboard.falconAI; // as '/dashboard/falcon-ai'
