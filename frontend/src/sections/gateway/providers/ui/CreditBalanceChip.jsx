import React from "react";
import Chip from "@mui/material/Chip";

import { useAuthContext } from "src/auth/hooks";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { paths } from "src/routes/paths";
import { RouterLink } from "src/routes/components";
import { ROLES } from "src/utils/rolePermissionMapping";
import { CREDITS_ANCHOR } from "src/sections/settings/BillingV2/gateway-credits/links";
import { formatBalance } from "src/sections/settings/BillingV2/gateway-credits/format";
import { useCentralBilling } from "src/sections/settings/BillingV2/useCentralBilling";

import { GatewayIcon } from "../../shell/gateway-icons";

// The roles central Billing is for (the rail's Billing link uses the same).
const BILLING_ROLES = [ROLES.OWNER, ROLES.ADMIN, ROLES.WORKSPACE_ADMIN];

export function canOpenBilling(user, workspaceRole) {
  return (
    BILLING_ROLES.includes(user?.organization_role) ||
    workspaceRole === ROLES.WORKSPACE_ADMIN
  );
}

// The gateway side of the one central billing: the managed-credit balance
// from the ledger, one click from Settings > Billing. Only billing roles ask
// for it, and nothing shows where the ledger does not answer.
export default function CreditBalanceChip() {
  const { user } = useAuthContext();
  const { currentWorkspaceRole } = useWorkspace();
  const allowed = canOpenBilling(user, currentWorkspaceRole);
  const { credits, creditsAvailable } = useCentralBilling({ enabled: allowed });

  if (!allowed || !creditsAvailable || !credits) return null;

  return (
    <Chip
      component={RouterLink}
      href={`${paths.dashboard.settings.billing}#${CREDITS_ANCHOR}`}
      clickable
      variant="outlined"
      size="small"
      icon={<GatewayIcon name="billing" size={16} />}
      label={`Credits ${formatBalance(credits.available_usd)}`}
      aria-label={`Credits ${formatBalance(credits.available_usd)}, open Billing`}
      // As tall as the page header's 38 px buttons it sits between.
      sx={{ height: 38, px: 1, typography: "s1", fontWeight: 500 }}
    />
  );
}
