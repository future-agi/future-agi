/* eslint-disable react-refresh/only-export-components */
import React from "react";
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { paths } from "src/routes/paths";
import SvgColor from "src/components/svg-color";
import Iconify from "src/components/iconify";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useAuthContext } from "src/auth/hooks";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
import {
  ROLES,
  showRoleSpecific,
  RoutesName,
} from "src/utils/rolePermissionMapping";
import axiosInstance, { endpoints } from "src/utils/axios";

const icon = (name) => (
  <SvgColor src={`/assets/icons/navbar/${name}.svg`} />
  // OR
  // <Iconify icon="fluent:mail-24-filled" />
  // https://icon-sets.iconify.design/solar/
  // https://www.streamlinehq.com/icons
);

const ICONS = {
  dashboard: icon("ic_blog"),
  data: icon("ic_analytics"),
  keys: icon("ic_lock"),
  tasks: icon("ic_dash_tasks"),
  users: icon("ic_users"),
  develop: icon("hugeicons"),
  docs: icon("ic_docs"),
  journey: icon("ic_booking"),
  sync: icon("ic_chat"),
  projects: icon("ic_run"),
  prompt: icon("ic_prompt"),
  addTeam: icon("ic_add_team"),
  help: icon("ic_help"),
  getStarted: icon("ic_get_started"),
  eval: icon("ic_eval"),
  prototype: icon("ic_prototype"),
  annotate: icon("ic_annotate"),
  knowledge_base: icon("ic_knowledge_base"),
  alerts: icon("ic_alert"),
  falconAI: icon("ic_falcon_ai"),
  simulate: icon("ic_experiment"),
  agentDefinition: icon("ic_project"),
  scenarios: icon("ic_sessions"),
  simulatorAgent: icon("ic_optimize"),
  test: icon("ic_evaluate"),
  feed: icon("ic_feed"),
  persona: icon("ic_persona"),
  agents: icon("ic_agents"),
};

// ----------------------------------------------------------------------

export function useNavData() {
  const { user } = useAuthContext();
  const { currentWorkspaceRole } = useWorkspace();
  const { isOSS } = useDeploymentMode();
  const userOrgRole = user?.organization_role ?? user?.organizationRole;
  const userDefaultWsRole =
    user?.default_workspace_role ?? user?.defaultWorkspaceRole;
  const isOwner = userOrgRole === "Owner";
  const effectiveWsRole = currentWorkspaceRole || userDefaultWsRole;
  const isAdmin =
    userOrgRole === "Admin" || effectiveWsRole === "workspace_admin";
  const data = useMemo(() => {
    const buildItems = [
      {
        title: "Dataset",
        path: paths.dashboard.develop,
        icon: ICONS.develop,
        eventTrigger: () => {
          trackEvent(Events.navigationDatasetClicked, {
            [PropertyName.click]: true,
          });
        },
      },
      {
        title: "Prompts",
        path: paths.dashboard.workbench,
        icon: ICONS.prompt,
        eventTrigger: () => {
          trackEvent(Events.navigationPromptClicked, {
            [PropertyName.click]: true,
          });
        },
      },
      {
        title: "Agents",
        path: paths.dashboard.agents,
        icon: ICONS.agents,
        // eventTrigger: () => {
        //   trackEvent(Events.navigationPromptClicked, {
        //     [PropertyName.click]: true,
        //   });
        // },
      },
      {
        title: "Prototype",
        path: paths.dashboard.prototype,
        icon: ICONS.prototype,
        eventTrigger: () => {
          trackEvent(Events.navigationPrototypeClicked, {
            [PropertyName.click]: true,
          });
        },
      },
      {
        title: "Knowledge base",
        path: paths.dashboard.knowledge_base,
        icon: ICONS.knowledge_base,
        eventTrigger: () => {
          trackEvent(Events.navigationKnowledgebaseClicked, {
            [PropertyName.click]: true,
          });
        },
      },
      {
        title: "Evals",
        path: paths.dashboard.evals,
        icon: ICONS.eval,
        eventTrigger: () => {
          trackEvent(Events.navigationEvalsClicked, {
            [PropertyName.click]: true,
          });
        },
      },
    ];
    if (isOwner || isAdmin) {
      buildItems.push({
        title: "Keys",
        path: paths.dashboard.keys,
        icon: ICONS.keys,
      });
    }
    const sections = [
      {
        subheader: "BUILD",
        items: buildItems,
      },
      {
        subheader: "OBSERVE",
        items: [
          {
            title: "Tracing",
            path: paths.dashboard.observe,
            icon: ICONS.projects,
            eventTrigger: () => {
              trackEvent(Events.navigationObserveClicked, {
                [PropertyName.click]: true,
              });
            },
          },
          {
            title: "Error Feed",
            path: paths.dashboard.errorFeed.root,
            icon: <Iconify icon="mdi:bug-outline" />,
          },
          {
            title: "Tasks",
            path: paths.dashboard.tasks,
            icon: ICONS.tasks,
          },
          {
            title: "Users",
            path: paths.dashboard.users,
            icon: ICONS.users,
          },
          {
            title: "Annotations",
            path: paths.dashboard.annotations.queues,
            icon: ICONS.annotate,
          },
          {
            title: "Alerts",
            path: paths.dashboard.alerts,
            icon: ICONS.alerts,
            eventTrigger: () => {
              trackEvent(Events.navigationAlertTabClicked, {
                [PropertyName.click]: true,
              });
            },
          },
          {
            title: "Dashboards",
            path: paths.dashboard.dashboards.root,
            icon: ICONS.data,
          },
        ],
      },
      {
        subheader: "GATEWAY",
        items: [
          {
            title: "Gateway",
            path: paths.dashboard.gateway.root,
            icon: <Iconify icon="mdi:transit-connection-variant" />,
            hasChildren: true,
          },
        ],
      },
      {
        subheader: "Simulate",
        items: [
          {
            title: "Agent Definition",
            path: paths.dashboard.simulate.agentDefinition,
            icon: ICONS.agentDefinition,
            eventTrigger: () => {
              trackEvent(Events.navigationAlertDefinitionClicked, {
                [PropertyName.click]: true,
              });
            },
          },
          {
            title: "Scenarios",
            path: paths.dashboard.simulate.scenarios,
            icon: ICONS.scenarios,
            eventTrigger: () => {
              trackEvent(Events.navigationScenariosClicked, {
                [PropertyName.click]: true,
              });
            },
          },
          {
            title: "Personas",
            path: paths.dashboard.simulate.personas,
            icon: ICONS.persona,
            eventTrigger: () => {
              trackEvent(Events.navigationPersonasClicked, {
                [PropertyName.click]: true,
              });
            },
          },
          // {
          //   title: "Simulation Agent",
          //   path: paths.dashboard.simulate.simulatorAgent,
          //   icon: ICONS.simulatorAgent,
          //   eventTrigger: () => {
          //     trackEvent(Events.navigationSimulationAgentClicked, {
          //       [PropertyName.click]: true,
          //     });
          //   },
          // },
          {
            title: "Run Simulation",
            path: paths.dashboard.simulate.test,
            icon: ICONS.test,
            eventTrigger: () => {
              trackEvent(Events.navigationRunTestsClicked, {
                [PropertyName.click]: true,
              });
            },
          },
        ],
      },
      // {
      //   // subheader: "Main",
      //   items: [],
      // },
    ];

    sections.unshift({
      items: [
        {
          title: "Falcon AI",
          path: paths.dashboard.falconAI,
          icon: ICONS.falconAI,
          disabled: isOSS,
        },
      ],
    });

    return sections;
  }, [isOwner, isAdmin, isOSS]);
  return data;
}

export function useNavUpgradeData() {
  const { user } = useAuthContext();
  const getStartedCompleted = user?.getStartedCompleted;
  const data = useMemo(() => {
    const items = [
      {
        title: "Docs",
        path: "https://docs.futureagi.com",
        icon: ICONS.docs,
        eventTrigger: () => {
          trackEvent(Events.docLinkClicked, {
            [PropertyName.source]: "side_navigation",
          });
        },
      },

      {
        title: "Help",
        path: import.meta.env.VITE_HELP_LINK,
        icon: ICONS.help,
      },
    ];

    if (getStartedCompleted) {
      items.splice(1, 0, {
        title: "Get started",
        path: paths.dashboard.getstarted,
        icon: ICONS.getStarted,
      });
    }
    return [{ subheader: "RESOURCES", items }];
  }, [getStartedCompleted]);

  return data;
}

const settingsIcon = (name) => (
  <SvgColor
    src={`/assets/icons/settings/${name}.svg`}
    sx={{ width: "20px", height: "20px" }}
  />
);

const SettingsIcons = {
  Summary: settingsIcon("usage_summary_new"),
  Management: settingsIcon("userManagement"),
  Keys: settingsIcon("APIKeys"),
  Providers: settingsIcon("AIProviders_new"),
  Integrations: settingsIcon("Integrations_new"),
  MCPServer: settingsIcon("MCPServer"),
  Pricing: settingsIcon("plansPricing_new"),
  Billing: settingsIcon("Billing_new"),
  Profile: settingsIcon("Profile_new"),
  Workspaces: settingsIcon("ic_new_workspace"),
  General: settingsIcon("ic_new_setting"),
  Security: settingsIcon("Security"),
};

export function useNavSettingsData() {
  const { user } = useAuthContext();
  const { isOSS } = useDeploymentMode();
  const { currentWorkspaceRole } = useWorkspace();

  const { data: workspaces = [] } = useQuery({
    queryKey: ["workspaces-list", "settings"],
    queryFn: () => axiosInstance.get(endpoints.workspace.workspaceList),
    select: (res) => res.data?.results || [],
    enabled: !!(user?.ws_enabled ?? user?.wsEnabled),
    staleTime: 30_000,
  });

  // Use organization role for org-level settings
  // Workspace role is only relevant for workspace-specific tabs
  const effectiveRole = user?.organization_role ?? user?.organizationRole;
  const isOrgAdminPlus =
    effectiveRole === ROLES.OWNER || effectiveRole === ROLES.ADMIN;
  const isWsAdmin =
    isOrgAdminPlus || currentWorkspaceRole === ROLES.WORKSPACE_ADMIN;
  const wsEnabled = user?.ws_enabled ?? user?.wsEnabled;

  const sections = useMemo(() => {
    // Wait for user role to be loaded before rendering tabs
    // This prevents showing all tabs during initial login when role is still loading
    // Also validate that the role is a valid RBAC role to prevent stale cache issues
    const validRoles = [
      ROLES.OWNER,
      ROLES.ADMIN,
      ROLES.MEMBER,
      ROLES.VIEWER,
      ROLES.WORKSPACE_ADMIN,
      ROLES.WORKSPACE_MEMBER,
      ROLES.WORKSPACE_VIEWER,
    ];
    if (!user || !effectiveRole || !validRoles.includes(effectiveRole)) {
      return [];
    }

    // Helper to check if current role can access a route
    const canAccess = (routeName) => {
      const hasAccess = showRoleSpecific[routeName]?.[effectiveRole] ?? false;
      return hasAccess;
    };

    const result = [];

    // Section 1: Profile (all roles)
    if (canAccess(RoutesName.profile)) {
      result.push({
        items: [
          {
            title: "Profile",
            path: "/dashboard/settings/profile-settings",
            icon: SettingsIcons.Profile,
          },
        ],
      });
    }

    // Section 2: Workspace Settings (based on role permissions)
    const wsSettingsItems = [];
    const canAccessIntegrations = isOrgAdminPlus || isWsAdmin;
    if (canAccess(RoutesName.integrations) && canAccessIntegrations) {
      wsSettingsItems.push({
        title: "Integrations",
        path: "/dashboard/settings/integrations",
        icon: SettingsIcons.Integrations,
      });
    }
    if (canAccess(RoutesName.aiProvider)) {
      wsSettingsItems.push({
        title: "AI Providers",
        path: "/dashboard/settings/ai-providers",
        icon: SettingsIcons.Providers,
      });
    }
    // Falcon AI Connectors — same access as integrations
    if (!isOSS && canAccess(RoutesName.integrations)) {
      wsSettingsItems.push({
        title: "Falcon AI Connectors",
        path: "/dashboard/settings/falcon-ai-connectors",
        icon: SettingsIcons.Integrations,
      });
    }
    if (wsSettingsItems.length > 0) {
      result.push({
        subheader: "Workspace Settings",
        items: wsSettingsItems,
      });
    }

    // Section 3: Organization (based on role permissions for each item)
    const orgItems = [];
    if (canAccess(RoutesName.usageSummary)) {
      orgItems.push({
        title: "Usage Summary",
        path: "/dashboard/settings/usage-summary",
        icon: SettingsIcons.Summary,
      });
    }
    if (!isOSS && canAccess(RoutesName.planAndPricing)) {
      orgItems.push({
        title: "Plans & Pricing",
        path: "/dashboard/settings/pricing",
        icon: SettingsIcons.Pricing,
      });
    }
    if (!isOSS && canAccess(RoutesName.billing)) {
      orgItems.push({
        title: "Billing",
        path: "/dashboard/settings/billing",
        icon: SettingsIcons.Billing,
      });
    }
    if (canAccess(RoutesName.members)) {
      orgItems.push({
        title: "Members",
        path: "/dashboard/settings/user-management",
        icon: SettingsIcons.Management,
      });
    }
    // MCP Server - only for Owner/Admin
    if (isOrgAdminPlus) {
      orgItems.push({
        title: "MCP Server",
        path: "/dashboard/settings/mcp-server",
        icon: SettingsIcons.MCPServer,
      });
    }
    // Org Settings - only for Owner/Admin
    if (isOrgAdminPlus) {
      orgItems.push({
        title: "Org Settings",
        path: "/dashboard/settings/org-settings",
        icon: SettingsIcons.Security,
      });
    }
    // EE Licenses - hidden (feature not available yet)
    // if (effectiveRole === ROLES.OWNER) {
    //   orgItems.push({
    //     title: "EE Licenses",
    //     path: "/dashboard/settings/ee-licenses",
    //     icon: SettingsIcons.Keys,
    //   });
    // }
    if (wsEnabled && canAccess(RoutesName.workspace)) {
      orgItems.push({
        title: "Workspaces",
        path: "/dashboard/settings/workspace",
        icon: SettingsIcons.Workspaces,
      });
    }
    if (orgItems.length > 0) {
      result.push({
        subheader: "Organization",
        items: orgItems,
      });
    }

    // Section 4: Your Workspaces (all roles, only when workspace feature is enabled)
    if (wsEnabled) {
      result.push({
        subheader: "Your Workspaces",
        items: workspaces.map((ws) => ({
          title: ws.display_name || ws.displayName || ws.name,
          path: `/dashboard/settings/workspace/${ws.id}`,
          icon: SettingsIcons.Workspaces,
        })),
      });
    }

    return result;
  }, [
    effectiveRole,
    isOrgAdminPlus,
    isWsAdmin,
    user,
    wsEnabled,
    workspaces,
    isOSS,
  ]);

  return sections;
}

export function useWorkspaceSettingsNav(workspaceId) {
  const { user } = useAuthContext();

  const { data: workspace = null } = useQuery({
    queryKey: ["workspaces-list", "settings"],
    queryFn: () => axiosInstance.get(endpoints.workspace.workspaceList),
    select: (res) =>
      (res.data?.results || []).find((w) => w.id === workspaceId) || null,
    enabled: !!workspaceId,
    staleTime: 30_000,
  });

  if (!workspaceId || !workspace) return null;

  // Check org role using organizationRole, not workspace role
  const userOrgRole = user?.organization_role ?? user?.organizationRole;
  const isOrgAdminPlus =
    userOrgRole === ROLES.OWNER || userOrgRole === ROLES.ADMIN;

  // Workspace level thresholds: 8=admin, 3=member, 1=viewer (from backend Level constants)
  const wsLevel = workspace.user_ws_level ?? workspace.userWsLevel ?? 0;
  const isWsAdmin = isOrgAdminPlus || wsLevel >= 8;
  const isWsViewer = !isOrgAdminPlus && wsLevel < 3;

  const items = [
    {
      title: "General",
      path: `/dashboard/settings/workspace/${workspaceId}/general`,
      icon: SettingsIcons.General,
    },
    // Workspace usage summary — commented out until workspace-level view is ready
    // {
    //   title: "Usage Summary",
    //   path: `/dashboard/settings/workspace/${workspaceId}/usage`,
    //   icon: SettingsIcons.Summary,
    // },
  ];

  if (isWsAdmin) {
    items.push({
      title: "Members",
      path: `/dashboard/settings/workspace/${workspaceId}/members`,
      icon: SettingsIcons.Management,
    });
  }

  if (!isWsViewer) {
    items.push(
      {
        title: "Integrations",
        path: `/dashboard/settings/workspace/${workspaceId}/integrations`,
        icon: SettingsIcons.Integrations,
      },
      {
        title: "AI Providers",
        path: `/dashboard/settings/workspace/${workspaceId}/ai-providers`,
        icon: SettingsIcons.Providers,
      },
    );
  }

  return {
    name:
      workspace.display_name ||
      workspace.displayName ||
      workspace.name ||
      "Workspace",
    data: [{ items }],
  };
}

export function useNavGatewayData() {
  return [
    {
      items: [
        {
          title: "Overview",
          path: paths.dashboard.gateway.overview,
          iconName: "mdi:view-dashboard-outline",
        },
      ],
    },
    {
      subheader: "CONFIGURE",
      items: [
        {
          title: "Providers",
          path: paths.dashboard.gateway.providers,
          iconName: "mdi:cloud-cog-outline",
        },
        {
          title: "API Keys",
          path: paths.dashboard.gateway.keys,
          iconName: "mdi:key-outline",
        },
        {
          title: "Guardrails",
          path: paths.dashboard.gateway.guardrails.root,
          iconName: "mdi:shield-check-outline",
        },
        {
          title: "Fallbacks",
          path: paths.dashboard.gateway.fallbacks,
          iconName: "mdi:swap-horizontal",
        },
      ],
    },
    {
      subheader: "INSIGHTS",
      items: [
        {
          title: "Request Logs",
          path: paths.dashboard.gateway.logs,
          iconName: "mdi:text-box-search-outline",
        },
        {
          title: "Analytics",
          path: paths.dashboard.gateway.analytics,
          iconName: "mdi:chart-line",
        },
        {
          title: "Monitoring",
          path: paths.dashboard.gateway.monitoring,
          iconName: "mdi:bell-ring-outline",
        },
        {
          title: "Sessions",
          path: paths.dashboard.gateway.sessions,
          iconName: "mdi:message-text-outline",
        },
        // {
        //   title: "Experiments",
        //   path: paths.dashboard.gateway.experiments,
        //   iconName: "mdi:flask-outline",
        // },
      ],
    },
    {
      subheader: "MANAGE",
      items: [
        {
          title: "Budgets",
          path: paths.dashboard.gateway.budgets,
          iconName: "mdi:wallet-outline",
        },
        {
          title: "Webhooks",
          path: paths.dashboard.gateway.webhooks,
          iconName: "mdi:webhook",
        },
        {
          title: "Custom Properties",
          path: paths.dashboard.gateway.customProperties,
          iconName: "mdi:tag-multiple-outline",
        },
        {
          title: "MCP Tools",
          path: paths.dashboard.gateway.mcp,
          iconName: "mdi:tools",
        },
        {
          title: "Settings",
          path: paths.dashboard.gateway.settings,
          iconName: "mdi:cog-outline",
        },
      ],
    },
  ];
}

export function useNavDashBoardData() {
  const { user } = useAuthContext();
  const getStartedCompleted = user?.getStartedCompleted;
  const data = useMemo(() => {
    const arrayData = [];
    if (!getStartedCompleted) {
      arrayData.push({
        title: "Get started",
        path: paths.dashboard.getstarted,
        icon: ICONS.getStarted,
      });
    }
    return arrayData;
  }, [getStartedCompleted]);
  return [{ items: data }];
}
