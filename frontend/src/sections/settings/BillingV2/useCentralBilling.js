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

/**
 * Whether the org has a central billing page: on cloud (the platform plan),
 * or wherever the gateway's managed-credit ledger answers (self-hosted EE
 * included). `enabled` gates the ledger read, so pages that never show the
 * link do not ask.
 */
export function useCentralBilling({ enabled = true } = {}) {
  const { isCloud, isLoading: modeLoading } = useDeploymentMode();
  const { user } = useAuthContext();
  const askLedger =
    enabled &&
    !CREDITS_HIDDEN &&
    ["Owner", "Admin"].includes(user?.organization_role);
  const credits = useGatewayCreditSummary({ enabled: askLedger });
  const creditsAvailable = askLedger && hasEnabledCredits(credits.data);
  return {
    isCloud,
    credits: creditsAvailable ? credits.data : null,
    creditsAvailable,
    visible: isCloud || creditsAvailable,
    isLoading: modeLoading || (askLedger && credits.isLoading),
  };
}
