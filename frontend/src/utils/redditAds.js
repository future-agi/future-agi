import {
  REDDIT_PIXEL_ID,
  REDDIT_ADS_ENABLED,
  AD_CONVERSION_VALUE,
  AD_CONVERSION_CURRENCY,
} from "src/config-global";
import logger from "src/utils/logger";

let initialized = false;

function rdtReady() {
  return typeof window !== "undefined" && typeof window.rdt === "function";
}

function isEnabled() {
  return REDDIT_ADS_ENABLED === "true" && Boolean(REDDIT_PIXEL_ID);
}

function normalizeEmail(email) {
  return typeof email === "string" ? email.trim().toLowerCase() : "";
}

/**
 * Load the Reddit pixel and fire the initial PageVisit event.
 *
 * Reddit does not ship a cross-domain linker — attribution between the
 * marketing site (futureagi.com) and this app relies on Reddit's first-party
 * `_rdt_uuid` cookie being re-set when the pixel loads here, plus
 * Advanced Matching via hashed email on the SignUp event.
 *
 * No-ops unless VITE_REDDIT_ADS_ENABLED === "true" and VITE_REDDIT_PIXEL_ID is set.
 */
export function initReddit() {
  if (initialized) return;
  if (typeof window === "undefined") return;
  if (!isEnabled()) return;

  // Reddit's standard pixel loader
  (function (w, d) {
    if (w.rdt) return;
    const p = function () {
      // eslint-disable-next-line prefer-rest-params
      p.sendEvent
        ? // eslint-disable-next-line prefer-rest-params
          p.sendEvent.apply(p, arguments)
        : // eslint-disable-next-line prefer-rest-params
          p.callQueue.push(arguments);
    };
    p.callQueue = [];
    w.rdt = p;
    const t = d.createElement("script");
    t.src = "https://www.redditstatic.com/ads/pixel.js";
    t.async = true;
    const s = d.getElementsByTagName("script")[0];
    s.parentNode.insertBefore(t, s);
  })(window, document);

  window.rdt("init", REDDIT_PIXEL_ID, {
    optOut: false,
    useDecimalCurrencyValues: true,
  });
  window.rdt("track", "PageVisit");

  initialized = true;
}


export function trackRedditSignup({ email, userId } = {}) {
  if (!rdtReady()) return;
  if (!isEnabled()) return;

  const normalizedEmail = normalizeEmail(email);
  if (!normalizedEmail) return;

  try {
    window.rdt("init", REDDIT_PIXEL_ID, {
      optOut: false,
      useDecimalCurrencyValues: true,
      email: normalizedEmail,
      externalId: String(userId || ""),
    });

    window.rdt("track", "SignUp", {
      currency: AD_CONVERSION_CURRENCY,
      value: AD_CONVERSION_VALUE,
      conversionId: String(userId || normalizedEmail),
    });
  } catch (err) {
    logger.error("Reddit signup conversion failed", err);
  }
}
