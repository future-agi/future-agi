// ----------------------------------------------------------------------

const ROOTS = {
  AUTH: "/auth",
  DASHBOARD: "/dashboard",
};

// ----------------------------------------------------------------------

export const paths = {
  minimalUI: "https://mui.com/store/items/minimal-dashboard/",
  // OSS self-hosted first-run flow (pre-auth, no dashboard layout)
  ossSetup: "/setup",
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

      // ── Revamped simulation flow (prototype) ──
      // Environment-first: pick a world, connect an agent, add scenarios and
      // evals, then run. The legacy routes above stay mounted so the old
      // screens remain reachable for comparison.
      environments: `${ROOTS.DASHBOARD}/simulate/environments`,
      // Twins gets its own top-level nav slot — it's a discovery /
      // marketing surface for the service-twin catalog + entry point,
      // not a parallel storage layer. Twin-backed envs still live in
      // `state.myEnvironments`; this page shows a filtered view of
      // them alongside the catalog and the "create" CTA.
      twins: `${ROOTS.DASHBOARD}/simulate/twins`,
      twinDetail: (envId) => `${ROOTS.DASHBOARD}/simulate/twins/${envId}`,
      environmentNew: `${ROOTS.DASHBOARD}/simulate/environments/new`,
      // World-class differentiator: create an env where the "world" is a
      // live sandbox for third-party services (Slack, Notion, Salesforce,
      // etc.) rather than the default seeded generic tables. Beats every
      // competitor because the twin backing plugs into the existing env
      // machinery (scenarios, personas, evals, RL contract, versioning)
      // for free.
      environmentNewTwin: `${ROOTS.DASHBOARD}/simulate/environments/new/twin`,
      environmentUseTemplate: (templateId) => `${ROOTS.DASHBOARD}/simulate/environments/use/${templateId}`,
      environmentDetail: (envId) => `${ROOTS.DASHBOARD}/simulate/environments/${envId}`,
      /* Connect-your-agent step for a freshly composed twin env —
         same shape as UseTemplate's step 0. Fit-check runs on submit,
         then hands off to twin-review. */
      environmentTwinConnect: (envId) => `${ROOTS.DASHBOARD}/simulate/environments/${envId}/twin-connect`,
      /* Review layout — chat left + workspace tabs right — for a
         freshly composed twin env. Same shape the single-service
         template flow uses at step 2. */
      environmentTwinReview: (envId) => `${ROOTS.DASHBOARD}/simulate/environments/${envId}/twin-review`,
      environmentStep: (envId, step) => `${ROOTS.DASHBOARD}/simulate/environments/${envId}/${step}`,
      simulationRun: (envId, runId) => `${ROOTS.DASHBOARD}/simulate/environments/${envId}/runs/${runId}`,
      // Two or more runs of the same scenarios, read as one screen. The runs
      // travel in the query string so a comparison is a link someone can send.
      simulationCompare: (envId) => `${ROOTS.DASHBOARD}/simulate/environments/${envId}/compare`,
      simulationRuns: `${ROOTS.DASHBOARD}/simulate/runs`,
      // Where a finished run reports. The legacy execution-detail screen, fed
      // for prototype runs by the mock adapter in simulate-v2/_mock.
      executionDetail: (testId, executionId) =>
        `${ROOTS.DASHBOARD}/simulate/test/${testId}/${executionId}/call-details`,
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
