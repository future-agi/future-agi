import { paths } from "src/routes/paths";

// Settings > Integrations > Webhooks > <endpoint> (DESIGN.md section 11.2), under
// the org settings or under a workspace's settings, like a connection's page.
export const endpointPath = (endpointId, workspaceId) =>
  workspaceId
    ? paths.dashboard.settings.workspaceWebhookEndpoint(workspaceId, endpointId)
    : paths.dashboard.settings.webhookEndpoint(endpointId);

export const integrationsPath = (workspaceId) =>
  workspaceId
    ? paths.dashboard.settings.workspaceIntegrations(workspaceId)
    : paths.dashboard.settings.integrations;
