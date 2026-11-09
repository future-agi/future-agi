/* eslint-disable react-refresh/only-export-components */
import React from "react";
import { useMemo } from "react";
import {
  useWorkspaceFromList,
  useWorkspacesList,
} from "src/api/workspaces/list";
import { paths } from "src/routes/paths";
import { agentsSidebarPath } from "src/sections/agent-playground/agentsTabs";
import { usePathname } from "src/routes/hooks";
import { HELP_LINK } from "src/config-global";
import SvgColor from "src/components/svg-color";
import Iconify from "src/components/iconify";
import Label from "src/components/label";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useAuthContext } from "src/auth/hooks";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
import { gatewayRailGroups } from "src/sections/gateway/shell/gateway-rail";
import { useManagedModelsStatus } from "src/sections/gateway/models/hooks/useManagedModels";
import { isGatewayPath } from "src/sections/omega/shell/products";
import { useCentralBilling } from "src/sections/settings/BillingV2/useCentralBilling";
import { useRestrictSimulateToEnvironments } from "src/hooks/useRestrictSimulateToEnvironments";
import { CAPABILITY, useFeatureAllowed } from "src/hooks/useCapabilities";
import {
  ROLES,
  showRoleSpecific,
  RoutesName,
} from "src/utils/rolePermissionMapping";
import { isOmegaConsoleEnabled } from "src/sections/omega/shell/flags";
import { withAgentInfrastructure } from "src/sections/omega/shell/agent-infrastructure-nav";
import {
  OMEGA_PRODUCT,
  ROLLOUT_STALE_MS,
} from "src/sections/omega/shell/rollout";
import { useOmegaProducts } from "src/sections/omega/shell/useOmegaProducts";
import {
  hasRequestable,
  useEarlyAccess,
} from "src/sections/omega/shell/useEarlyAccess";

// HO S2 (prototype-off hygiene): a door whose page reads only prototype
// paths (the DSX_PROTOTYPE entries of api/contracts/api-contract-exceptions.js)
// exists only in a prototype build. The env check is static.
const PROTOTYPE_BUILD =
  import.meta.env.VITE_ENVIRONMENT === "local" &&
  import.meta.env.VITE_PROTOTYPE_MODE === "true";
// L7b absent-sweep (nav:other-products): products outside Datasets +
// Experiments that the mocks do not serve are absent from a prototype
// build's nav (their routes redirect to /dashboard/develop). Same static gate.
const notInPrototype = (items) => (PROTOTYPE_BUILD ? [] : items);

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
  scenarios: icon("ic_sessions"),
  simulatorAgent: icon("ic_optimize"),
  test: icon("ic_evaluate"),
  feed: icon("ic_feed"),
  agents: icon("ic_agents"),
};

// ----------------------------------------------------------------------

export function useNavData() {
  // Admission controls working entries; RL 1808 adds inert hosted-withheld rows.
  const omegaProducts = useOmegaProducts();
  // Orgs created after the cutoff see only Environments in Simulate.
  const restrictSimulateToEnvironments = useRestrictSimulateToEnvironments();
  const agentsPath = agentsSidebarPath(usePathname());
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
      // W5 C-H7: leaderboards across datasets (prototype builds only; its
      // reads are prototype paths). L7c g1: its own icon (not Dashboards').
      ...(PROTOTYPE_BUILD
        ? [
            {
              title: "Leaderboards",
              path: `${paths.dashboard.develop}/leaderboards`,
              icon: <Iconify icon="mdi:podium" />,
            },
          ]
        : []),
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
        path: agentsPath,
        activePath: paths.dashboard.agents,
        icon: ICONS.agents,
        // eventTrigger: () => {
        //   trackEvent(Events.navigationPromptClicked, {
        //     [PropertyName.click]: true,
        //   });
        // },
      },
      // {
      //   title: "Prototype",
      //   path: paths.dashboard.prototype,
      //   icon: ICONS.prototype,
      //   eventTrigger: () => {
      //     trackEvent(Events.navigationPrototypeClicked, {
      //       [PropertyName.click]: true,
      //     });
      //   },
      // },
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
    buildItems.push({
      title: "Keys",
      path: paths.dashboard.keys,
      icon: ICONS.keys,
    });
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
          ...notInPrototype([
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
          ]),
          {
            title: "Annotations",
            path: paths.dashboard.annotations.queues,
            icon: ICONS.annotate,
          },
          ...notInPrototype([
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
          ]),
        ],
      },
      // Simulate follows dev (Environment, plus Scenarios and Run Simulation for
      // orgs not restricted to Environments; Agent Definition left the sidebar).
      // None of it is served by the prototype mocks, so a prototype build has no
      // Simulate section (prototypeAbsentRoutes.js redirects its pages).
      ...notInPrototype([
        {
          subheader: "Simulate",
          items: [
            {
              title: "Environment",
              path: paths.dashboard.simulate.environments.root,
              icon: <Iconify icon="solar:server-square-cloud-linear" />,
            },
            ...(restrictSimulateToEnvironments
              ? []
              : [
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
                ]),
          ],
        },
      ]),
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
        },
      ],
    });

    return withAgentInfrastructure(
      sections,
      isOmegaConsoleEnabled(),
      omegaProducts.listed,
      { hostedWithheld: omegaProducts.hostedWithheld },
    );
  }, [agentsPath, omegaProducts, restrictSimulateToEnvironments]);
  return data;
}

const DOCS_LINK = "https://docs.futureagi.com";
const OSS_HELP_LINK = "https://discord.com/invite/n2tCUKBkAw";

export function useNavUpgradeData() {
  const { user } = useAuthContext();
  const { isOSS, isLoading: deploymentModeLoading } = useDeploymentMode();
  const getStartedCompleted = user?.getStartedCompleted;
  const data = useMemo(() => {
    // OSS images ship without VITE_HELP_LINK, so point Help at the community
    // Discord unless the operator configured a support channel of their own.
    const helpLink =
      isOSS && !deploymentModeLoading ? HELP_LINK || OSS_HELP_LINK : HELP_LINK;
    const items = [
      {
        title: "Docs",
        path: DOCS_LINK,
        icon: ICONS.docs,
        eventTrigger: () => {
          trackEvent(Events.docLinkClicked, {
            [PropertyName.source]: "side_navigation",
          });
        },
      },
    ];

    if (getStartedCompleted && !PROTOTYPE_BUILD) {
      items.splice(1, 0, {
        title: "Get started",
        path: paths.dashboard.getstarted,
        icon: ICONS.getStarted,
      });
    }

    // Without a path the item renders as a link to the current route, so drop it.
    if (helpLink) {
      items.push({
        title: "Help",
        path: helpLink,
        icon: ICONS.help,
      });
    }
    return [{ subheader: "RESOURCES", items }];
  }, [getStartedCompleted, isOSS, deploymentModeLoading]);

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

// Billing (Settings > Billing) is open to these roles; RoleProtection on the
// route uses the same list.
const BILLING_ROLES = [ROLES.OWNER, ROLES.ADMIN, ROLES.WORKSPACE_ADMIN];

export function useNavSettingsData() {
  const { user } = useAuthContext();
  const { isCloud } = useDeploymentMode();
  const { currentWorkspaceRole } = useWorkspace();
  const pathname = usePathname();
  // Central billing also shows off cloud when the gateway's credit ledger
  // answers, which it never does while gateway credits are hidden
  // (sections/gateway/shell/page-status.js). Asked only where the settings
  // nav is on screen.
  const { visible: billingVisible } = useCentralBilling({
    enabled:
      pathname.startsWith("/dashboard/settings") &&
      BILLING_ROLES.includes(user?.organization_role),
  });
  // Falcon AI connectors exist only where the deployment allows Falcon AI;
  // while capabilities load or fail to load, the item stays off the list.
  const { allowed: falconAIAllowed } = useFeatureAllowed(CAPABILITY.FALCON_AI);

  const { data: workspaces = [] } = useWorkspacesList({
    enabled: !!user?.ws_enabled,
  });

  // Use organization role for org-level settings
  // Workspace role is only relevant for workspace-specific tabs
  const effectiveRole = user?.organization_role;
  const isOrgAdminPlus =
    effectiveRole === ROLES.OWNER || effectiveRole === ROLES.ADMIN;
  const isWsAdmin =
    isOrgAdminPlus || currentWorkspaceRole === ROLES.WORKSPACE_ADMIN;
  const wsEnabled = user?.ws_enabled;
  const omegaProducts = useOmegaProducts();
  // Read only with the Omega master switch on (inside the hook).
  const { data: earlyAccess } = useEarlyAccess({ staleTime: ROLLOUT_STALE_MS });
  const showEarlyAccess =
    isOmegaConsoleEnabled() && hasRequestable(earlyAccess);

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
    // The roles that manage integrations (integrations/permissions.py): an
    // organization Owner or Admin, or an Admin of the current workspace, whose
    // organization role is Member.
    const canAccessIntegrations = isOrgAdminPlus || isWsAdmin;
    // L7b absent-sweep (settings:pages): a prototype build lists only the
    // settings pages the mocks serve (Profile, AI Providers, Workspaces,
    // Your Workspaces; per workspace General, Audit log, AI Providers).
    // Members, usage/billing, MCP, Org settings, License, Integrations and
    // (L7c g1) Falcon AI Connectors are absent there; their routes redirect.
    if (!PROTOTYPE_BUILD && canAccessIntegrations) {
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
    // Falcon AI Connectors: same roles as integrations, on a deployment that
    // allows Falcon AI
    if (
      !PROTOTYPE_BUILD &&
      falconAIAllowed &&
      canAccess(RoutesName.integrations)
    ) {
      wsSettingsItems.push({
        title: "Falcon AI Connectors",
        path: "/dashboard/settings/falcon-ai-connectors",
        icon: SettingsIcons.Integrations,
      });
    }
    // Settings → Sandboxes keeps its role/admission gates. RL 1811(2) adds
    // only the same inert hosted-withheld information as the app sidebar.
    if (isOmegaConsoleEnabled() && (isOrgAdminPlus || isWsAdmin)) {
      if (omegaProducts.listed(OMEGA_PRODUCT.SANDBOXES)) {
        wsSettingsItems.push({
          title: "Sandboxes",
          path: "/dashboard/settings/sandboxes",
          icon: SettingsIcons.General,
        });
      } else if (omegaProducts.hostedWithheld(OMEGA_PRODUCT.SANDBOXES)) {
        wsSettingsItems.push({
          title: "Sandboxes",
          icon: SettingsIcons.General,
          inert: true,
          info: (
            <Label sx={{ textTransform: "none" }}>
              Not available in this deployment
            </Label>
          ),
        });
      }
    }
    if (wsSettingsItems.length > 0) {
      result.push({
        subheader: "Workspace Settings",
        items: wsSettingsItems,
      });
    }

    // Section 3: Organization (based on role permissions for each item)
    const orgItems = [];
    const realApp = !PROTOTYPE_BUILD;
    if (realApp && billingVisible && canAccess(RoutesName.usageSummary)) {
      orgItems.push({
        title: "Usage Summary",
        path: "/dashboard/settings/usage-summary",
        icon: SettingsIcons.Summary,
      });
    }
    if (realApp && isCloud && canAccess(RoutesName.planAndPricing)) {
      orgItems.push({
        title: "Plans & Pricing",
        path: "/dashboard/settings/pricing",
        icon: SettingsIcons.Pricing,
      });
    }
    if (realApp && billingVisible && canAccess(RoutesName.billing)) {
      orgItems.push({
        title: "Billing",
        path: "/dashboard/settings/billing",
        icon: SettingsIcons.Billing,
      });
    }
    if (realApp && canAccess(RoutesName.members)) {
      orgItems.push({
        title: "Members",
        path: "/dashboard/settings/user-management",
        icon: SettingsIcons.Management,
      });
    }
    // MCP Server - only for Owner/Admin
    if (realApp && isOrgAdminPlus) {
      orgItems.push({
        title: "MCP Server",
        path: "/dashboard/settings/mcp-server",
        icon: SettingsIcons.MCPServer,
      });
    }
    // Org Settings - only for Owner/Admin
    if (realApp && isOrgAdminPlus) {
      orgItems.push({
        title: "Org Settings",
        path: "/dashboard/settings/org-settings",
        icon: SettingsIcons.Security,
      });
    }
    if (realApp && !isCloud && isOrgAdminPlus) {
      orgItems.push({
        title: "License",
        path: "/dashboard/settings/ee-licenses",
        icon: SettingsIcons.Keys,
      });
    }
    if (wsEnabled && canAccess(RoutesName.workspace)) {
      orgItems.push({
        title: "Workspaces",
        path: "/dashboard/settings/workspace",
        icon: SettingsIcons.Workspaces,
      });
    }
    // Settings → Early access: the waitlist for the hosted Omega products,
    // listed only while the server lists a product this org is not admitted
    // to (RC-01, AUDIT R1): no entry while products are internal, and none
    // for an org that already has everything it can see.
    if (showEarlyAccess) {
      orgItems.push({
        title: "Early access",
        path: "/dashboard/settings/early-access",
        icon: SettingsIcons.General,
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
          title: ws.display_name || ws.name,
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
    isCloud,
    omegaProducts,
    showEarlyAccess,
    billingVisible,
    falconAIAllowed,
  ]);

  return sections;
}

export function useWorkspaceSettingsNav(workspaceId) {
  const { user } = useAuthContext();

  const { workspace } = useWorkspaceFromList(workspaceId);

  if (!workspaceId || !workspace) return null;

  // Check org role using the canonical user-info organization_role field.
  const userOrgRole = user?.organization_role;
  const isOrgAdminPlus =
    userOrgRole === ROLES.OWNER || userOrgRole === ROLES.ADMIN;

  // Workspace level thresholds: 8=admin, 3=member, 1=viewer (from backend Level constants)
  const wsLevel = workspace.user_ws_level ?? 0;
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
    items.push(
      // L7b absent-sweep: workspace Members is absent in a prototype build.
      ...(PROTOTYPE_BUILD
        ? []
        : [
            {
              title: "Members",
              path: `/dashboard/settings/workspace/${workspaceId}/members`,
              icon: SettingsIcons.Management,
            },
          ]),
      // W4-7 (§9 integration, M6): the workspace's audit log, admins only;
      // prototype builds only (and unit tests, which render this nav).
      ...(PROTOTYPE_BUILD || import.meta.env.MODE === "test"
        ? [
            {
              title: "Audit log",
              path: `/dashboard/settings/workspace/${workspaceId}/audit-log`,
              icon: SettingsIcons.Security,
            },
          ]
        : []),
    );
  }

  if (!isWsViewer) {
    items.push(
      // L7b absent-sweep: workspace Integrations is absent in a prototype build.
      ...(PROTOTYPE_BUILD
        ? []
        : [
            {
              title: "Integrations",
              path: `/dashboard/settings/workspace/${workspaceId}/integrations`,
              icon: SettingsIcons.Integrations,
            },
          ]),
      {
        title: "AI Providers",
        path: `/dashboard/settings/workspace/${workspaceId}/ai-providers`,
        icon: SettingsIcons.Providers,
      },
    );
  }

  return {
    name: workspace.display_name || workspace.name || "Workspace",
    data: [{ items }],
  };
}

// The AI Gateway's rail (sections/gateway/shell/gateway-rail.js): the
// sidebar shows it instead of the Future AGI nav once the AI Gateway is the
// product chosen in the switcher. The gateway is not in the Future AGI nav.
// Billing is not in the rail: it lives in Settings, the one central billing
// page, and Gateway settings links it. So the rail reads no credit ledger.
// On AI Gateway pages it reads the managed-models status: the Catalog item
// is listed only where the status says managed models are offered, since a
// deployment that offers none has no catalog (its catalog URLs open Auto
// routing). The rail waits for the status, so no Catalog item shows for a
// moment on a deployment without managed models; a status that cannot be
// read lists no Catalog.
export function useNavGatewayData() {
  const pathname = usePathname();
  const status = useManagedModelsStatus({ enabled: isGatewayPath(pathname) });
  const known =
    status.data !== undefined ||
    Boolean(status.isError) ||
    (status.failureCount ?? 0) > 0;
  const noManagedModels = status.data?.available !== true;
  return useMemo(
    () => (known ? gatewayRailGroups({ noManagedModels }) : []),
    [known, noManagedModels],
  );
}

export function useNavDashBoardData() {
  const { user } = useAuthContext();
  const getStartedCompleted = user?.getStartedCompleted;
  const data = useMemo(() => {
    const arrayData = [];
    if (!getStartedCompleted && !PROTOTYPE_BUILD) {
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
