import { useLayoutEffect, useMemo } from "react";
import { useSafeAuthContext } from "src/auth/hooks/use-auth-context";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { readOmegaSquadConfig } from "src/api/omega-squad/remoteClient";

/** Bind host exchange selectors to the active UI context; the issuer authorizes them. */
export default function useOmegaSquadConfig() {
  const baseConfig = useMemo(() => readOmegaSquadConfig(), []);
  const { user, authenticated } = useSafeAuthContext();
  const { currentOrganizationId, isReady: organizationReady } =
    useOrganization();
  const { currentWorkspaceId, isReady: workspaceReady } = useWorkspace();
  const accountId = user?.id == null ? null : String(user.id);
  const scopeReady = Boolean(
    authenticated &&
      accountId &&
      organizationReady &&
      workspaceReady &&
      currentOrganizationId &&
      currentWorkspaceId,
  );
  // Only nonsecret identity belongs in the React key. Readiness transitions
  // also discard the previous tenant's data while selection is unresolved.
  const scopeKey = baseConfig.allowHostToken
    ? JSON.stringify([
        accountId,
        currentOrganizationId,
        currentWorkspaceId,
        scopeReady,
      ])
    : "native-omega-session";
  const binding = useMemo(() => {
    const lifetime = { active: false };
    return {
      scopeKey,
      lifetime,
      config: baseConfig.allowHostToken
        ? {
            ...baseConfig,
            organizationId: scopeReady ? currentOrganizationId : null,
            workspaceId: scopeReady ? currentWorkspaceId : null,
            scopeReady,
            isScopeCurrent: () => lifetime.active,
          }
        : baseConfig,
    };
  }, [
    baseConfig,
    currentOrganizationId,
    currentWorkspaceId,
    scopeKey,
    scopeReady,
  ]);
  useLayoutEffect(() => {
    binding.lifetime.active = true;
    return () => {
      binding.lifetime.active = false;
    };
  }, [binding]);
  return { config: binding.config, scopeKey: binding.scopeKey };
}
