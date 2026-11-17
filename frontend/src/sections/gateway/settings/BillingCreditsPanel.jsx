import React from "react";
import Button from "@mui/material/Button";

import { useAuthContext } from "src/auth/hooks";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
import { CREDITS_ANCHOR } from "src/sections/settings/BillingV2/gateway-credits/links";
import { formatBalance } from "src/sections/settings/BillingV2/gateway-credits/format";
import { useCentralBilling } from "src/sections/settings/BillingV2/useCentralBilling";

import { canOpenBilling } from "../providers/ui/CreditBalanceChip";
import { isGatewayControlHidden } from "../shell/page-status";
import { KeyValueList, SettingsPanel } from "./ui";

// Billing & credits lives in Settings, the one central billing: the gateway
// rail has no Billing item, and Gateway settings says where it is and links
// it. Only the roles that can open Billing see it, and only where central
// Billing has something for this organization (the same check the Settings
// nav makes).
function CreditsPanel() {
  const { user } = useAuthContext();
  const { currentWorkspaceRole } = useWorkspace();
  const allowed = canOpenBilling(user, currentWorkspaceRole);
  const { isCloud, visible, credits, creditsAvailable } = useCentralBilling({
    enabled: allowed,
  });
  if (
    !allowed ||
    !visible ||
    !creditsAvailable ||
    !["Owner", "Admin"].includes(user?.organization_role)
  )
    return null;

  // Top-ups only where central Billing can sell credit (its "Add credits",
  // GatewayCreditsSection canBuy); a self-hosted ledger or a build with no
  // checkout has none.
  const what =
    credits?.billing_available === true
      ? "balance, ledger, monthly statement, top-ups and low-balance alert"
      : "balance, ledger, monthly statement and low-balance alert";

  return (
    <SettingsPanel
      title="Billing & credits"
      description={
        isCloud
          ? `Calls to Future AGI models are paid from your organization's Future AGI credits. Its ${what} are in Settings > Billing, with the rest of your plan.`
          : `Settings > Billing shows your organization's Future AGI credit ${what}.`
      }
      testId="gateway-billing"
      actions={
        <Button
          variant="outlined"
          component={RouterLink}
          href={`${paths.dashboard.settings.billing}#${CREDITS_ANCHOR}`}
        >
          Open Billing & credits
        </Button>
      }
    >
      {creditsAvailable && credits && (
        <KeyValueList
          rows={[
            {
              label: "Credit available",
              value: formatBalance(credits.available_usd),
            },
          ]}
        />
      )}
    </SettingsPanel>
  );
}

// While gateway credits are hidden (shell/page-status.js) the panel renders
// nothing and reads nothing: the balance, ledger, statement and top-ups it
// would point to are not served.
const CREDITS_HIDDEN = isGatewayControlHidden("managedCredits");

export default function BillingCreditsPanel() {
  return CREDITS_HIDDEN ? null : <CreditsPanel />;
}
