import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";
import { trackPostHogEvent } from "src/utils/PostHog/posthog";

const CREDIT_ERROR_CODES = [
  "FREE_TIER_LIMIT",
  "BUDGET_PAUSED",
  "ENTITLEMENT_LIMIT",
  "ENTITLEMENT_DENIED",
  "PAYMENT_REQUIRED",
];

/**
 * Extract the billing/usage error code from an API error.
 *
 * The axios layer spreads the response body onto the rejected error, so the
 * backend envelope's top-level `code` ("FREE_TIER_LIMIT", …) is what arrives
 * here, with nested `result` shapes as fallbacks. There is deliberately no
 * `error.errorCode` read: the axios layer never sets that field, so keying
 * off it (the previous implementation) matched nothing for real responses.
 */
function extractCreditErrorCode(error) {
  if (!error) return undefined;
  return (
    error.code ||
    error.error_code ||
    error.result?.error_code ||
    error.result?.errorCode
  );
}

/**
 * Check if an API error is a credit/usage exhaustion error.
 */
export function isCreditExhaustionError(error) {
  if (!error) return false;
  return (
    error.statusCode === 402 ||
    CREDIT_ERROR_CODES.includes(extractCreditErrorCode(error))
  );
}

/**
 * Reusable hook for handling credit exhaustion errors from API calls.
 *
 * Usage:
 *   const { exhaustionError, handleError, handleUpgradeClick, handleDismiss } =
 *     useCreditExhaustion({ feature: "evals" });
 *
 *   // In mutation onError or catch block:
 *   if (!handleError(err)) {
 *     enqueueSnackbar(err.message, { variant: "error" });
 *   }
 *
 *   // In render:
 *   <CreditExhaustionBanner error={exhaustionError} onUpgrade={handleUpgradeClick} onDismiss={handleDismiss} />
 *
 * @param {Object} options
 * @param {string} options.feature - Feature name for analytics segmentation (e.g., "evals", "gateway")
 */
export function useCreditExhaustion({ feature = "unknown" } = {}) {
  const navigate = useNavigate();
  const [exhaustionError, setExhaustionError] = useState(null);

  const handleError = useCallback(
    (error) => {
      if (isCreditExhaustionError(error)) {
        setExhaustionError(error);
        trackPostHogEvent("credits_nudge_shown", {
          feature,
          error_code: extractCreditErrorCode(error),
          dimension: error.dimension,
          current_usage: error.currentUsage,
          limit: error.limit,
        });
        return true;
      }
      return false;
    },
    [feature],
  );

  const handleUpgradeClick = useCallback(() => {
    // The pricing route is a frontend concern; don't accept a URL from the
    // server — it can rot when routes move, and would be an open-redirect
    // if an attacker-controlled string ever landed in the payload.
    trackPostHogEvent("credits_upgrade_clicked", {
      feature,
      error_code: extractCreditErrorCode(exhaustionError),
      target_plan: exhaustionError?.upgradeCta?.plan,
    });
    navigate(paths.dashboard.settings.pricing);
    setExhaustionError(null);
  }, [exhaustionError, feature, navigate]);

  const handleDismiss = useCallback(() => {
    trackPostHogEvent("credits_nudge_dismissed", {
      feature,
      error_code: extractCreditErrorCode(exhaustionError),
    });
    setExhaustionError(null);
  }, [exhaustionError, feature]);

  return {
    exhaustionError,
    isExhausted: !!exhaustionError,
    handleError,
    handleUpgradeClick,
    handleDismiss,
    clearError: () => setExhaustionError(null),
  };
}
