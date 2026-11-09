import { createContext, useContext } from "react";

// The workspace a workspace's own Integrations pages
// (/dashboard/settings/workspace/:workspaceId/integrations[/:connectionId])
// stand for, and whether the signed-in user manages its integrations. Under
// IntegrationsWorkspaceScope every integrations request names that workspace
// (X-Workspace-Id), so the page lists, creates and changes that workspace's
// connections, whichever workspace is current; elsewhere the scope is
// undefined and requests carry the current workspace.
export const IntegrationsScopeContext = createContext(undefined);

export const useIntegrationsScope = () => useContext(IntegrationsScopeContext);

export const useIntegrationsWorkspaceId = () =>
  useContext(IntegrationsScopeContext)?.workspaceId;

/** An axios config naming the scope's workspace, if any, on top of `config`. */
export const scopedConfig = (workspaceId, config = {}) =>
  workspaceId
    ? {
        ...config,
        headers: { ...(config.headers || {}), "X-Workspace-Id": workspaceId },
      }
    : config;
