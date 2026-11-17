import { useAuthContext } from "src/auth/hooks";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
import { isGatewayControlHidden } from "src/sections/gateway/shell/page-status";

import { useGatewayCreditSummary } from "./gateway-credits/api";

// Credit surfaces share this availability check. Only organization billing
// administrators request the ledger; a cloud workspace administrator can
// still use platform billing without making a credit request.
const CREDITS_HIDDEN = isGatewayControlHidden("managedCredits");

// New managed-credit surfaces require explicit organization enablement.
// Credit history and deployment purchasing capability do not enable a wallet.
export function hasEnabledCredits(summary) {
  return summary?.enabled === true;
}

function isLedgerUnavailable(error) {
  const status = error?.statusCode ?? error?.response?.status;
  const data = error?.response?.data ?? error;
  if (data?.code === "billing_unavailable") {
    // The summary uses this same 503 code for dependency failures. Only the
    // missing-ledger reply proves absence; an operation failure needs Retry.
    return (
      status === 501 ||
      (status === 503 &&
        data.result ===
          "The managed credit ledger is not installed in this build.")
    );
  }
  // Older callers represented the unsupported-ledger signal as an Error.
  // Never treat a generic 503 carrying this message as proof of absence.
  return (
    error instanceof Error &&
    error.message === "billing_unavailable" &&
    !error.response &&
    (status === undefined || status === 501)
  );
}

/**
 * Whether the org has a central billing page: on cloud (the platform plan),
 * or wherever the gateway's managed-credit ledger answers (self-hosted EE
 * included). A failed read keeps the link available for recovery, without
 * enabling a wallet. `enabled` gates the ledger read, so pages that never
 * show the link do not ask.
 */
export function useCentralBilling({ enabled = true } = {}) {
  const { isCloud, isLoading: modeLoading } = useDeploymentMode();
  const { user } = useAuthContext();
  const askLedger =
    enabled &&
    !CREDITS_HIDDEN &&
    ["Owner", "Admin"].includes(user?.organization_role);
  const credits = useGatewayCreditSummary({ enabled: askLedger });
  const ledgerUnavailable =
    credits.isError && isLedgerUnavailable(credits.error);
  const creditsAvailable =
    askLedger && !ledgerUnavailable && hasEnabledCredits(credits.data);
  const creditsError = Boolean(
    askLedger &&
      !ledgerUnavailable &&
      (credits.isError ||
        (credits.isSuccess && typeof credits.data?.enabled !== "boolean")),
  );
  return {
    isCloud,
    credits: creditsAvailable ? credits.data : null,
    creditsAvailable,
    creditsError,
    retryCredits: credits.refetch,
    creditsRetrying: credits.isFetching,
    visible: isCloud || creditsAvailable || creditsError,
    isLoading: modeLoading || (askLedger && credits.isLoading),
  };
}
