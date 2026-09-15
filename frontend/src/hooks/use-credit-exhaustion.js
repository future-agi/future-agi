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
 * Check if an API error is a credit/usage exhaustion error.
 */
export function isCreditExhaustionError(error) {
  if (!error) return false;
  const errorCode = error.errorCode || error.code;
  return (
    error.statusCode === 402 || CREDIT_ERROR_CODES.includes(errorCode)
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
        const errorCode = error.errorCode || error.code;
        trackPostHogEvent("credits_nudge_shown", {
          feature,
          error_code: errorCode,
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
    const errorCode = exhaustionError?.errorCode || exhaustionError?.code;
    trackPostHogEvent("credits_upgrade_clicked", {
      feature,
      error_code: errorCode,
      target_plan: exhaustionError?.upgradeCta?.plan,
    });
    navigate(paths.dashboard.settings.pricing);
    setExhaustionError(null);
  }, [exhaustionError, feature, navigate]);

  const handleDismiss = useCallback(() => {
    const errorCode = exhaustionError?.errorCode || exhaustionError?.code;
    trackPostHogEvent("credits_nudge_dismissed", {
      feature,
      error_code: errorCode,
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
