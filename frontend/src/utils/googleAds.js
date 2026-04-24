import {
  GOOGLE_ADS_ID,
  GOOGLE_ADS_SIGNUP_LABEL,
  GOOGLE_ADS_ENABLED,
  GA_ID,
  AD_CONVERSION_VALUE,
  AD_CONVERSION_CURRENCY,
} from "src/config-global";
import logger from "src/utils/logger";

const LINKER_DOMAINS = ["futureagi.com", "app.futureagi.com"];

let initialized = false;

function gtagReady() {
  return typeof window !== "undefined" && typeof window.gtag === "function";
}

function isEnabled() {
  return GOOGLE_ADS_ENABLED === "true" && Boolean(GOOGLE_ADS_ID);
}

function normalizeEmail(email) {
  return typeof email === "string" ? email.trim().toLowerCase() : "";
}

/**
 * Load the Google tag (gtag.js) and configure Google Ads + GA4.
 *
 * Cross-domain linker is enabled so gclid/_gcl_aw attribution carries over
 * from futureagi.com (landing page) to app.futureagi.com (this app).
 * Requires matching linker config on the landing page for bidirectional flow.
 *
 * No-ops if VITE_GOOGLE_ADS_ID is not set.
 */
export function initGoogleAds() {
  if (initialized) return;
  if (typeof window === "undefined") return;
  if (!isEnabled()) return;

  window.dataLayer = window.dataLayer || [];
  function gtag() {
    window.dataLayer.push(arguments);
  }
  window.gtag = gtag;

  gtag("js", new Date());
  gtag("config", GOOGLE_ADS_ID, {
    linker: { domains: LINKER_DOMAINS, accept_incoming: true },
  });
  if (GA_ID) {
    gtag("config", GA_ID, {
      linker: { domains: LINKER_DOMAINS, accept_incoming: true },
    });
  }

  const s = document.createElement("script");
  s.async = true;
  s.src = `https://www.googletagmanager.com/gtag/js?id=${GOOGLE_ADS_ID}`;
  document.head.appendChild(s);

  initialized = true;
}

/**
 * Fire a Google Ads signup conversion with Enhanced Conversions user_data.
 * Called after a real account is created (email or OAuth).
 *
 * The email is used as the transaction_id so Google Ads dedupes repeats
 * across sessions / retries. user_data is hashed client-side by gtag.
 */
export function trackSignupConversion({ email, method = "email", userId } = {}) {
  if (!gtagReady()) return;
  if (!isEnabled() || !GOOGLE_ADS_SIGNUP_LABEL) return;

  const normalizedEmail = normalizeEmail(email);
  if (!normalizedEmail) return;

  try {
    window.gtag("set", "user_data", { email: normalizedEmail });

    window.gtag("event", "conversion", {
      send_to: `${GOOGLE_ADS_ID}/${GOOGLE_ADS_SIGNUP_LABEL}`,
      value: AD_CONVERSION_VALUE,
      currency: AD_CONVERSION_CURRENCY,
      transaction_id: `signup_completed_${userId || normalizedEmail}`,
      event_callback: () => {},
    });

    window.gtag("event", "sign_up", {
      method,
    });
  } catch (err) {
    logger.error("Google Ads signup conversion failed", err);
  }
}
