import { useOrganization } from "src/contexts/OrganizationContext";
import {
  GATEWAY_ADMIN_LEVEL,
  GATEWAY_ADMIN_ONLY,
} from "../constants/gatewayAdmin";

export { GATEWAY_ADMIN_LEVEL, GATEWAY_ADMIN_ONLY };

// The gateway admin role check (futureagi/agentcc/permissions.py
// is_gateway_admin): only an organization Owner or Admin may change or push
// the org's gateway config, its API keys and its shadow experiments. Every
// console control that does so (a save, a toggle, a delete, a sync, a test
// that runs through the gateway's admin path, an Add that opens such a
// dialog) reads this one hook, and for a Member or a Viewer shows disabled
// with GATEWAY_ADMIN_ONLY. Reads stay open.
//
// Org levels are tfc/constants/levels.py's: Viewer 1, Member 3, Admin 8,
// Owner 15. An unknown level (null, not loaded) counts as not an Admin, as
// the workspace switcher's level check does; the backend decides either way.

export function isGatewayAdminLevel(orgLevel) {
  return typeof orgLevel === "number" && orgLevel >= GATEWAY_ADMIN_LEVEL;
}

export function useGatewayAdmin() {
  const { orgLevel } = useOrganization();
  const canEdit = isGatewayAdminLevel(orgLevel);
  return { canEdit, disabledReason: canEdit ? null : GATEWAY_ADMIN_ONLY };
}
