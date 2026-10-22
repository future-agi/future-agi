import { useOrganization } from "src/contexts/OrganizationContext";

// The role check (futureagi/agentcc/permissions.py is_gateway_admin): only
// an organization Owner or Admin may change the org's gateway config. Every
// console control the backend holds to this check (a save, a toggle, a
// delete, the guardrail test that pushes the config, an Add that opens such a
// dialog) reads this one hook, and for a Member or a Viewer shows disabled
// with GATEWAY_ADMIN_ONLY. Every member has read access and Reload.
//
// Org levels are tfc/constants/levels.py's: Viewer 1, Member 3, Admin 8,
// Owner 15. An unknown level (null, not loaded) counts as not an Admin, as
// the workspace switcher's level check does; the backend answers 403 to any
// config write that is not an Owner's or Admin's.
export const GATEWAY_ADMIN_LEVEL = 8;
export const GATEWAY_ADMIN_ONLY = "Owner or Admin only";

export function isGatewayAdminLevel(orgLevel) {
  return typeof orgLevel === "number" && orgLevel >= GATEWAY_ADMIN_LEVEL;
}

export function useGatewayAdmin() {
  const { orgLevel } = useOrganization();
  const canEdit = isGatewayAdminLevel(orgLevel);
  return { canEdit, disabledReason: canEdit ? null : GATEWAY_ADMIN_ONLY };
}
