import {
  TWITTER_PIXEL_ID,
  TWITTER_ADS_ENABLED,
  AD_CONVERSION_VALUE,
  AD_CONVERSION_CURRENCY,
} from "src/config-global";
import logger from "src/utils/logger";

let initialized = false;

function twqReady() {
  return typeof window !== "undefined" && typeof window.twq === "function";
}

function isEnabled() {
  return TWITTER_ADS_ENABLED === "true" && Boolean(TWITTER_PIXEL_ID);
}

function normalizeEmail(email) {
  return typeof email === "string" ? email.trim().toLowerCase() : "";
}

/**
 * Load the Twitter (X) Universal Website Tag and fire the initial PageView.
 *
 * Attribution between the marketing site (futureagi.com) and this app relies
 * on Twitter's first-party cookies being re-set when the pixel loads here,
 * plus hashed email matching on the Signup event.
 *
 * No-ops unless VITE_TWITTER_ADS_ENABLED === "true" and VITE_TWITTER_PIXEL_ID is set.
 */
export function initTwitter() {
  if (initialized) return;
  if (typeof window === "undefined") return;
  if (!isEnabled()) return;

  // Twitter's standard UWT loader
  (function (e, t, n) {
    let s, u, a;
    if (e.twq) return;
    s = e.twq = function () {
      // eslint-disable-next-line prefer-rest-params
      s.exe
        ? // eslint-disable-next-line prefer-rest-params
          s.exe.apply(s, arguments)
        : // eslint-disable-next-line prefer-rest-params
          s.queue.push(arguments);
    };
    s.version = "1.1";
    s.queue = [];
    u = t.createElement(n);
    u.async = true;
    u.src = "https://static.ads-twitter.com/uwt.js";
    a = t.getElementsByTagName(n)[0];
    a.parentNode.insertBefore(u, a);
  })(window, document, "script");

  window.twq("config", TWITTER_PIXEL_ID);

  initialized = true;
}

/**
 * Fire a Twitter signup conversion with email matching.
 * Called after a real account is created (email or OAuth/SSO).
 *
 * `conversion_id` dedupes repeats server-side on Twitter's end.
 * `email_address` is hashed by Twitter for match-back against their user graph.
 */
export function trackTwitterSignup({ email, method = "email", userId } = {}) {
  if (!twqReady()) return;
  if (!isEnabled()) return;

  const normalizedEmail = normalizeEmail(email);
  if (!normalizedEmail) return;

  try {
    window.twq("event", "Signup", {
      value: AD_CONVERSION_VALUE,
      currency: AD_CONVERSION_CURRENCY,
      conversion_id: String(userId || normalizedEmail),
      email_address: normalizedEmail,
      contents: [{ content_id: method }],
    });
  } catch (err) {
    logger.error("Twitter signup conversion failed", err);
  }
}
