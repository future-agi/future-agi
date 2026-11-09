// Auth URLs can appear verbatim or inside next=/returnTo=. Keep routing and
// non-secret query values intact; replace only the credential value/segment.
// Include GCP Marketplace and the SOS handoff emitted by accounts, in addition
// to OAuth/SAML and AWS Marketplace. No real credential examples belong here.
export const AUTH_CREDENTIAL_PARAMS = Object.freeze([
  "sso_token",
  "access_token",
  "refresh_token",
  "onboarding_token",
  "onboarding_gcp_token",
  "access",
  "refresh",
]);
export const REDACTED_AUTH_SECRET = "auth-secret-redacted";

const encoded = (value, depth) => {
  let result = value;
  for (let i = 0; i < depth; i += 1) result = encodeURIComponent(result);
  return result.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
};

// Bound inspection work, NOT the depth at which secrets are protected. A URL
// encoded past this budget contains a percent escape with eight `25` layers.
// Fail closed on that entire whitespace/quote-delimited token before applying
// the bounded layer-specific rules. Anchoring at token boundaries makes the
// lookahead scan each token once, not once for every possible suffix. This is
// also expressible as a plain rule for New Relic's string-only obfuscator.
const ENCODING_INSPECTION_BUDGET = 8;
const overBudget = new RegExp(
  `(^|[\\s"'<>])(?=[^\\s"'<>]*%(?:25){${ENCODING_INSPECTION_BUDGET}})[^\\s"'<>]+`,
  "gi",
);

// Inspect each permitted layer independently: in a raw query %26 belongs to
// the value; in an encoded query it terminates the value. Do not decode a raw
// value first and accidentally retain a credential suffix after its %26.
export const AUTH_URL_RULES = Object.freeze([
  Object.freeze({
    regex: overBudget,
    replacement: `$1${REDACTED_AUTH_SECRET}`,
  }),
  ...Array.from(
    { length: ENCODING_INSPECTION_BUDGET + 1 },
    (_, depth) => depth,
  ).flatMap((depth) => {
    const slash = encoded("/", depth);
    const query = encoded("?", depth);
    const amp = encoded("&", depth);
    const hash = encoded("#", depth);
    const equal = encoded("=", depth);
    const semi = encoded(";", depth);
    const queryValue = `(?:(?!${amp}|${hash}|${semi})[^&#;\\s"'<>])+`;
    const segment = `(?:(?!${slash}|${query}|${amp}|${hash}|${semi})[^/?#&;\\s"'<>])+`;
    const authPath = `auth${slash}jwt${slash}(?:invitation${slash}(?:accept|set-password)|verify)${slash}${segment}`;
    // Request URLs retain credentials even after an address-bar intake. These
    // paths feed xhr/fetch breadcrumbs, http.client/replay spans and NR AJAX.
    const accountPath = `accounts${slash}(?:accept-invitation|password-reset-confirm)${slash}${segment}`;
    const reviewPath = `model-hub${slash}review-links`;
    return [
      Object.freeze({
        regex: new RegExp(
          `((?:${query}|${amp}|${hash}|${semi})(?:${AUTH_CREDENTIAL_PARAMS.join("|")})${equal})${queryValue}`,
          "gi",
        ),
        replacement: `$1${REDACTED_AUTH_SECRET}`,
      }),
      Object.freeze({
        regex: new RegExp(
          `(${slash}(?:${authPath}|${accountPath}|${reviewPath}|shared|review)${slash})${segment}`,
          "gi",
        ),
        replacement: `$1${REDACTED_AUTH_SECRET}`,
      }),
    ];
  }),
]);

export const AUTH_URL_PATTERNS = Object.freeze(
  AUTH_URL_RULES.map(({ regex }) => new RegExp(regex.source, "i")),
);

export const hasCredentialUrl = (value) =>
  typeof value === "string" &&
  AUTH_URL_PATTERNS.some((rule) => rule.test(value));

export const AUTH_REPLAY_URL_BLOCKLIST = Object.freeze(
  AUTH_URL_PATTERNS.map((regex) =>
    Object.freeze({
      // PostHog's remote matcher constructs RegExp(source) without flags.
      // Expand literal ASCII letters, preserving escapes such as \\s.
      url: regex.source.replace(/\\.|[a-z]/gi, (part) =>
        part.startsWith("\\")
          ? part
          : `[${part.toLowerCase()}${part.toUpperCase()}]`,
      ),
      matching: "regex",
    }),
  ),
);
