// ----------------------------------------------------------------------

const ROOTS = {
  AUTH: "/auth",
  DASHBOARD: "/dashboard",
};

// ----------------------------------------------------------------------

export const paths = {
  minimalUI: "https://mui.com/store/items/minimal-dashboard/",
  // AUTH
  auth: {
    jwt: {
      login: `${ROOTS.AUTH}/jwt/login`,
      register: `${ROOTS.AUTH}/jwt/register`,
      "forget-password": `${ROOTS.AUTH}/jwt/forget-password`,
      verify: `${ROOTS.AUTH}/jwt/verify`,
      sso: `${ROOTS.AUTH}/jwt/sso-sml`,
      setup_org: `${ROOTS.AUTH}/jwt/setup-org`,
      org_removed: `${ROOTS.AUTH}/jwt/org-removed`,
      twoFactor: `${ROOTS.AUTH}/jwt/two-factor`,
      inviteAccepted: `${ROOTS.AUTH}/jwt/invite-accepted`,
      inviteSetPassword: (uuid, token) =>
        `${ROOTS.AUTH}/jwt/invitation/set-password/${uuid}/${token}`,
    },
  },
  // DASHBOARD
  dashboard: {
    root: ROOTS.DASHBOARD,
    models: {
      root: `${ROOTS.DASHBOARD}/models`,
      details: (id) => `${ROOTS.DASHBOARD}/models/${id}`,
    },
    settings: {
      root: `${ROOTS.DASHBOARD}/settings/profile-settings`,
      manageteam: `${ROOTS.DASHBOARD}/settings/user-management`,
      integrations: `${ROOTS.DASHBOARD}/settings/integrations`,
      integrationDetail: (id) =>
        `${ROOTS.DASHBOARD}/settings/integrations/${id}`,
      workspaceIntegrations: (workspaceId) =>
        `${ROOTS.DASHBOARD}/settings/workspace/${workspaceId}/integrations`,
      workspaceIntegrationDetail: (workspaceId, id) =>
        `${ROOTS.DASHBOARD}/settings/workspace/${workspaceId}/integrations/${id}`,
      mcpServer: `${ROOTS.DASHBOARD}/settings/mcp-server`,
      falconAIConnectors: `${ROOTS.DASHBOARD}/settings/falcon-ai-connectors`,
      falconAIMemory: `${ROOTS.DASHBOARD}/settings/falcon-ai-memory`,
      orgSettings: `${ROOTS.DASHBOARD}/settings/org-settings`,
      usageSummary: `${ROOTS.DASHBOARD}/settings/usage-summary`,
      billing: `${ROOTS.DASHBOARD}/settings/billing`,
      pricing: `${ROOTS.DASHBOARD}/settings/pricing`,
      eeLicenses: `${ROOTS.DASHBOARD}/settings/ee-licenses`,
    },
    performance: `${ROOTS.DASHBOARD}/performance`,
    data: `${ROOTS.DASHBOARD}/data`,
    keys: `${ROOTS.DASHBOARD}/keys`,
    tasks: `${ROOTS.DASHBOARD}/tasks`,
    users: `${ROOTS.DASHBOARD}/users`,
    evals: `${ROOTS.DASHBOARD}/evaluations`,
    docs: `${ROOTS.DASHBOARD}/docs`,
    sync: `${ROOTS.DASHBOARD}/sync`,
    develop: `${ROOTS.DASHBOARD}/develop`,
    prompt: `${ROOTS.DASHBOARD}/prompt`,
    workbench: `${ROOTS.DASHBOARD}/workbench`,
    getstarted: `${ROOTS.DASHBOARD}/get-started`,
    // projects: `${ROOTS.DASHBOARD}/projects/experiment`,
    huggingface: `${ROOTS.DASHBOARD}/huggingface`,
    prototype: `${ROOTS.DASHBOARD}/prototype`,
    annotations: {
      root: `${ROOTS.DASHBOARD}/annotations`,
      labels: `${ROOTS.DASHBOARD}/annotations/labels`,
      queues: `${ROOTS.DASHBOARD}/annotations/queues`,
      queueDetail: (queueId) =>
        `${ROOTS.DASHBOARD}/annotations/queues/${queueId}`,
      annotate: (queueId) =>
        `${ROOTS.DASHBOARD}/annotations/queues/${queueId}/annotate`,
    },
    knowledge_base: `${ROOTS.DASHBOARD}/knowledge`,
    // observe: `${ROOTS.DASHBOARD}/projects/observe`,
    observe: `${ROOTS.DASHBOARD}/observe`,
    alerts: `${ROOTS.DASHBOARD}/alerts`,
    simulate: {
      root: `${ROOTS.DASHBOARD}/simulate`,
      agentDefinition: `${ROOTS.DASHBOARD}/simulate/agent-definitions`,
      scenarios: `${ROOTS.DASHBOARD}/simulate/scenarios`,
      personas: `${ROOTS.DASHBOARD}/simulate/personas`,
      simulatorAgent: `${ROOTS.DASHBOARD}/simulate/simulator-agent`,
      test: `${ROOTS.DASHBOARD}/simulate/test`,
    },
    feed: `${ROOTS.DASHBOARD}/error-feed`,
    errorFeed: {
      root: `${ROOTS.DASHBOARD}/error-feed`,
      detail: (id) => `${ROOTS.DASHBOARD}/error-feed/${id}`,
    },
    dashboards: {
      root: `${ROOTS.DASHBOARD}/dashboards`,
      detail: (id) => `${ROOTS.DASHBOARD}/dashboards/${id}`,
      widgetEditor: (dashboardId, widgetId) =>
        `${ROOTS.DASHBOARD}/dashboards/${dashboardId}/widget/${widgetId}`,
    },
    gateway: {
      root: `${ROOTS.DASHBOARD}/gateway`,
      overview: `${ROOTS.DASHBOARD}/gateway`,
      keys: `${ROOTS.DASHBOARD}/gateway/keys`,
      providers: `${ROOTS.DASHBOARD}/gateway/providers`,
      guardrails: {
        root: `${ROOTS.DASHBOARD}/gateway/guardrails`,
        overview: `${ROOTS.DASHBOARD}/gateway/guardrails`,
        configuration: `${ROOTS.DASHBOARD}/gateway/guardrails/configuration`,
        analytics: `${ROOTS.DASHBOARD}/gateway/guardrails/analytics`,
        feedback: `${ROOTS.DASHBOARD}/gateway/guardrails/feedback`,
        playground: `${ROOTS.DASHBOARD}/gateway/guardrails/playground`,
        logs: `${ROOTS.DASHBOARD}/gateway/guardrails/logs`,
      },
      budgets: `${ROOTS.DASHBOARD}/gateway/budgets`,
      monitoring: `${ROOTS.DASHBOARD}/gateway/monitoring`,
      logs: `${ROOTS.DASHBOARD}/gateway/logs`,
      analytics: `${ROOTS.DASHBOARD}/gateway/analytics`,
      webhooks: `${ROOTS.DASHBOARD}/gateway/webhooks`,
      sessions: `${ROOTS.DASHBOARD}/gateway/sessions`,
      customProperties: `${ROOTS.DASHBOARD}/gateway/custom-properties`,
      fallbacks: `${ROOTS.DASHBOARD}/gateway/fallbacks`,
      mcp: `${ROOTS.DASHBOARD}/gateway/mcp`,
      experiments: `${ROOTS.DASHBOARD}/gateway/experiments`,
      settings: `${ROOTS.DASHBOARD}/gateway/settings`,
    },
    agents: `${ROOTS.DASHBOARD}/agents`,
    falconAI: `${ROOTS.DASHBOARD}/falcon-ai`,
  },
};
