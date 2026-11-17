import { useCallback } from "react";
import { useAuthContext } from "src/auth/hooks";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";

// `user` and `organization` are canonical IDs in AgentccAPIKeySerializer.
// The editable `owner` contact label and metadata never establish authority.
// The server remains authoritative for live membership/session/scope checks.
export function useKeyRevocation() {
  const { user } = useAuthContext();
  const { orgLevel, currentOrganizationId, isReady } = useOrganization();
  const { canEdit } = useGatewayAdmin();
  const userId = user?.id;
  const activeMember = Boolean(
    userId &&
      isReady &&
      currentOrganizationId &&
      Number.isFinite(orgLevel) &&
      orgLevel >= 1,
  );
  const canRevoke = useCallback(
    (key) =>
      Boolean(
        activeMember &&
          ["active", "pending", "failed"].includes(key?.status) &&
          key.organization === currentOrganizationId &&
          (canEdit || (typeof key.user === "string" && key.user === userId)),
      ),
    [activeMember, canEdit, currentOrganizationId, userId],
  );
  return { canRevoke, userId, organizationId: currentOrganizationId };
}
