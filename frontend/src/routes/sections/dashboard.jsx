/* eslint-disable react-refresh/only-export-components */
import React from "react";
import { Suspense } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";

import { AuthGuard } from "src/auth/guard";
import DashboardLayout from "src/layouts/dashboard";

import { LoadingScreen } from "src/components/loading-screen";
import { Events, getPageViewEvent, trackEvent } from "src/utils/Mixpanel";
import RoleProtection from "../components/role-protection";
import WorkspaceRoleProtection from "../components/workspace-role-protection";
import { GatewayProvider } from "src/sections/gateway/context/GatewayContext";
import GatewayGuard from "src/sections/gateway/components/GatewayGuard";
import lazyWithRetry from "src/utils/lazyWithRetry";
// Lazy load all route components (with retry for chunk errors after deploys)
const DevKeysPage = lazyWithRetry(
  () => import("src/pages/dashboard/keys/dev-keys"),
);
const ManageTeamPage = lazyWithRetry(
  () => import("src/pages/dashboard/settings/ManageTeamPage"),
);
const Develop = lazyWithRetry(
  () => import("src/pages/dashboard/Develop/Develop"),
);
const DevelopDetail = lazyWithRetry(
  () => import("src/pages/dashboard/Develop/DevelopDetail"),
);
const ExperimentWrapper = lazyWithRetry(
  () => import("src/pages/dashboard/Develop/ExperimentWrapper"),
);
const ExperimentData = lazyWithRetry(
  () => import("src/pages/dashboard/Develop/ExperimentData"),
);
const ExperimentSummary = lazyWithRetry(
  () => import("src/pages/dashboard/Develop/ExperimentSummary"),
);
const EditSyntheticDataDrawer = lazyWithRetry(
  () =>
    import(
      "src/sections/develop/AddRowDrawer/EditSyntheticData/EditSyntheticDataDrawer"
    ),
);
const PricingPage = lazyWithRetry(
  () => import("src/pages/dashboard/settings/pricing"),
);
const CheckoutPage = lazyWithRetry(
  () => import("src/pages/dashboard/settings/checkoutPage"),
);
const Success = lazyWithRetry(
  () => import("src/pages/dashboard/settings/Success"),
);
const Cancel = lazyWithRetry(
  () => import("src/pages/dashboard/settings/Cancel"),
);
const AIProviders = lazyWithRetry(
  () => import("src/pages/dashboard/settings/AIProviders"),
);
const IntegrationsPage = lazyWithRetry(
  () => import("src/pages/dashboard/settings/Integrations"),
);
const MCPServerPage = lazyWithRetry(
  () => import("src/pages/dashboard/settings/MCPServer"),
);
const FalconAIConnectorsPage = lazyWithRetry(
  () => import("src/pages/dashboard/settings/FalconAIConnectors"),
);
const IntegrationDetailPage = lazyWithRetry(
  () => import("src/sections/settings/integrations/IntegrationDetail"),
);
const ApiKeys = lazyWithRetry(
  () => import("src/pages/dashboard/settings/ApiKeys"),
);
const ComingSoon = lazyWithRetry(
  () => import("src/pages/dashboard/settings/ComingSoon"),
);
const UsageSummary = lazyWithRetry(
  () => import("src/pages/dashboard/settings/UsageSummary/UsageSummary"),
);
// const UserManagement = lazyWithRetry(
//   () => import("src/pages/dashboard/settings/UserManagement"),
// );
const WorkSpaceManagement = lazyWithRetry(
  () =>
    import("src/pages/dashboard/settings/UserManagementV2/WorkSpaceManagement"),
);
const UserManagementV2 = lazyWithRetry(
  () =>
    import("src/pages/dashboard/settings/UserManagementV2/UserManagementV2"),
);
const BillingPageV2 = lazyWithRetry(
  () => import("src/sections/settings/BillingV2/BillingPage"),
);
const EELicensesPage = lazyWithRetry(
  () => import("src/sections/settings/EELicenses/EELicensesPage"),
);
const ProfileSettings = lazyWithRetry(
  () => import("src/pages/dashboard/settings/ProfileSettings"),
);
const OrgSettings = lazyWithRetry(
  () => import("src/pages/dashboard/settings/OrgSettings"),
);
const Prompt = lazyWithRetry(() => import("src/pages/dashboard/Prompt/Prompt"));
const Evals = lazyWithRetry(() => import("src/pages/dashboard/evals/Evals"));
const EvalsUsage = lazyWithRetry(
  () => import("src/pages/dashboard/evals/EvalsUsage"),
);
const EvalCreate = lazyWithRetry(
  () => import("src/pages/dashboard/evals/EvalCreate"),
);
const EvalDetailView = lazyWithRetry(
  () => import("src/sections/evals/EvalDetails/EvalDetailView"),
);
const EvalDetail = lazyWithRetry(
  () => import("src/pages/dashboard/evals/EvalDetail"),
);
const EvalGroups = lazyWithRetry(
  () => import("src/pages/dashboard/EvalGroups/EvalGroups.jsx"),
);
const EvalsIndividualGroup = lazyWithRetry(
  () => import("src/pages/dashboard/EvalGroups/EvalsIndividualGroup.jsx"),
);
const AddNewPrompt = lazyWithRetry(
  () => import("src/pages/dashboard/Prompt/AddNewPrompt"),
);
const ProjectList = lazyWithRetry(
  () => import("src/pages/dashboard/projects/ProjectList"),
);
const GatewayOverview = lazyWithRetry(
  () => import("src/pages/dashboard/gateway/GatewayOverview"),
);
const GatewayLogs = lazyWithRetry(
  () => import("src/pages/dashboard/gateway/GatewayLogs"),
);
const GatewayAnalytics = lazyWithRetry(
  () => import("src/pages/dashboard/gateway/GatewayAnalytics"),
);
const GatewayKeys = lazyWithRetry(
  () => import("src/pages/dashboard/gateway/GatewayKeys"),
);
const GatewayProviders = lazyWithRetry(
  () => import("src/pages/dashboard/gateway/GatewayProviders"),
);
const GatewayGuardrails = lazyWithRetry(
  () => import("src/pages/dashboard/gateway/GatewayGuardrails"),
);
const GatewayBudgets = lazyWithRetry(
  () => import("src/pages/dashboard/gateway/GatewayBudgets"),
);
const GatewayMonitoring = lazyWithRetry(
  () => import("src/pages/dashboard/gateway/GatewayMonitoring"),
);
const GatewaySettings = lazyWithRetry(
  () => import("src/pages/dashboard/gateway/GatewaySettings"),
);
const GatewayWebhooks = lazyWithRetry(
  () => import("src/pages/dashboard/gateway/GatewayWebhooks"),
);
const GatewaySessions = lazyWithRetry(
  () => import("src/pages/dashboard/gateway/GatewaySessions"),
);
const GatewayCustomProperties = lazyWithRetry(
  () => import("src/pages/dashboard/gateway/GatewayCustomProperties"),
);
const GatewayFallbacks = lazyWithRetry(
  () => import("src/pages/dashboard/gateway/GatewayFallbacks"),
);
const GatewayMCP = lazyWithRetry(
  () => import("src/pages/dashboard/gateway/GatewayMCP"),
);
const ObserveList = lazyWithRetry(
  () => import("src/pages/dashboard/projects/ObserveList"),
);
const ProjectWrapper = lazyWithRetry(
  () => import("src/pages/dashboard/projects/ProjectWrapper"),
);
const ProjectDetail = lazyWithRetry(
  () => import("src/pages/dashboard/projects/ProjectDetail"),
);
const HuggingFacePage = lazyWithRetry(
  () => import("src/pages/dashboard/huggingface/HuggingFace"),
);
const IndividualExperimentWrapper = lazyWithRetry(
  () => import("src/pages/dashboard/Develop/IndividualExperimentWrapper"),
);
const IndividualExperimentData = lazyWithRetry(
  () => import("src/pages/dashboard/Develop/IndividualExperimentData"),
);
const IndividualExperimentSummary = lazyWithRetry(
  () => import("src/pages/dashboard/Develop/IndividualExperimentSummery"),
);
const PreviewScreen = lazyWithRetry(
  () => import("src/sections/develop-detail/AnnotationsTab/PreviewScreen"),
);
const RunInsidePage = lazyWithRetry(
  () => import("src/pages/dashboard/run-inside/run-inside"),
);
const ObserverWrapper = lazyWithRetry(
  () => import("src/pages/dashboard/observe/ObserverWrapper"),
);
const TraceFullPage = lazyWithRetry(
  () => import("src/pages/dashboard/observe/TraceFullPage"),
);
const VoiceFullPage = lazyWithRetry(
  () => import("src/pages/dashboard/observe/VoiceFullPage"),
);
// const LogsView = lazyWithRetry(
//   () => import("src/sections/projects/LogsView/LogsView"),
// );
const SessionsView = lazyWithRetry(
  () => import("src/sections/projects/SessionsView/Sessions-view"),
);
const LLMTracingView = lazyWithRetry(
  () => import("src/sections/projects/LLMTracing/LLMTracingView"),
);
const UserList = lazyWithRetry(
  () => import("src/pages/dashboard/projects/UsersList"),
);
const CrossProjectUserDetailPage = lazyWithRetry(
  () =>
    import(
      "src/sections/projects/UsersView/CrossProjectUserDetailPage/CrossProjectUserDetailPage"
    ),
);

const GetStarted = lazyWithRetry(
  () => import("src/pages/dashboard/get-started/GetStarted"),
);
const ErrorFallbackView = lazyWithRetry(
  () => import("src/sections/error/ErrorFallbackView"),
);
const TasksPage = lazyWithRetry(
  () => import("src/pages/dashboard/tasks/TasksPage"),
);
const TaskCreate = lazyWithRetry(
  () => import("src/pages/dashboard/tasks/TaskCreate"),
);
const TaskDetail = lazyWithRetry(
  () => import("src/pages/dashboard/tasks/TaskDetail"),
);
const UsersView = lazyWithRetry(
  () => import("src/sections/projects/UsersView/UsersView"),
);
const KnowledgeBase = lazyWithRetry(
  () => import("src/pages/dashboard/knowledge-base/KnowledgeBase"),
);
const KnowledgeBaseDetailView = lazyWithRetry(
  () => import("src/pages/dashboard/knowledge-base/KnowledgeBaseDetailView"),
);
const CreateSyntheticData = lazyWithRetry(
  () => import("src/pages/dashboard/Develop/CreateSyntheticData"),
);
// const Workbench = lazyWithRetry(() => import("src/pages/dashboard/workbench/Workbench"));
const PromptDir = lazyWithRetry(
  () => import("src/pages/dashboard/workbench-v2/PromptDir"),
);
const FolderView = lazyWithRetry(
  () => import("src/sections/workbench-v2/FolderView"),
);
const CreatePrompt = lazyWithRetry(
  () => import("src/sections/workbench/createPrompt/CreatePrompt"),
);
const Scenarios = lazyWithRetry(
  () => import("src/pages/dashboard/scenarios/Scenarios"),
);
const Personas = lazyWithRetry(
  () => import("src/pages/dashboard/personas/Personas"),
);
const CreateScenario = lazyWithRetry(
  () => import("src/pages/dashboard/scenarios/CreateScenario"),
);
const ScenarioDatasetView = lazyWithRetry(
  () => import("src/sections/scenarios/scenario-detail/ScenarioDatasetView"),
);
const AgentDefinitions = lazyWithRetry(
  () => import("src/pages/dashboard/agent-definitions/AgentDefinitions"),
);
const AgentDetails = lazyWithRetry(
  () => import("src/pages/dashboard/agent-definitions/AgentDetails"),
);
const CreateNewAgentDefinition = lazyWithRetry(
  () =>
    import("src/pages/dashboard/agent-definitions/CreateNewAgentDefinition"),
);
// const SimulatorAgent = lazyWithRetry(
//   () => import("src/pages/dashboard/simulator-agent/SimulatorAgent"),
// );
const RunTests = lazyWithRetry(
  () => import("src/pages/dashboard/run-tests/RunTests"),
);
const RunTestDetail = lazyWithRetry(
  () => import("src/pages/dashboard/run-tests/RunTestDetail"),
);
const TestRuns = lazyWithRetry(
  () => import("src/pages/dashboard/run-tests/TestRuns"),
);
const CallLogs = lazyWithRetry(
  () => import("src/pages/dashboard/run-tests/CallLogs"),
);
const TestAnalytics = lazyWithRetry(
  () => import("src/pages/dashboard/run-tests/TestAnalytics"),
);
const TestExecutionCallDetail = lazyWithRetry(
  () => import("src/pages/dashboard/run-tests/TestExecutionCallDetail"),
);
const TestExecutionPerformanceDetail = lazyWithRetry(
  () => import("src/pages/dashboard/run-tests/TestExecutionPerformance.jsx"),
);
const TestExecutionAnalyticsDetail = lazyWithRetry(
  () => import("src/pages/dashboard/run-tests/TestExecutionAnalytics"),
);
const TestExecutionOptimizationRunsDetail = lazyWithRetry(
  () =>
    import("src/pages/dashboard/run-tests/TestExecutionOptimizationRunsDetail"),
);
const TestDetail = lazyWithRetry(
  () => import("src/sections/test-detail/TestRunDetailView"),
);

const TestExecutionOptimizationDetail = lazyWithRetry(
  () => import("src/pages/dashboard/run-tests/TestExecutionOptimizationDetail"),
);
const TestExecutionOptimizationTrialDetail = lazyWithRetry(
  () =>
    import(
      "src/pages/dashboard/run-tests/TestExecutionOptimizationTrialDetail"
    ),
);

// Add SettingsLayout import at the top
const SettingsLayout = lazyWithRetry(
  () => import("src/pages/dashboard/settings/SettingsLayout"),
);

const AlertMainView = lazyWithRetry(
  () => import("src/sections/alerts/Alerts.jsx"),
);

// Workspace settings pages
const WorkspaceUsage = lazyWithRetry(
  () => import("src/pages/dashboard/settings/WorkspaceSettings/WorkspaceUsage"),
);
const WorkspaceMembers = lazyWithRetry(
  () =>
    import("src/pages/dashboard/settings/WorkspaceSettings/WorkspaceMembers"),
);
const WorkspaceIntegrations = lazyWithRetry(
  () =>
    import(
      "src/pages/dashboard/settings/WorkspaceSettings/WorkspaceIntegrations"
    ),
);
const WorkspaceAIProviders = lazyWithRetry(
  () =>
    import(
      "src/pages/dashboard/settings/WorkspaceSettings/WorkspaceAIProviders"
    ),
);
const WorkspaceGeneral = lazyWithRetry(
  () =>
    import("src/pages/dashboard/settings/WorkspaceSettings/WorkspaceGeneral"),
);
const FalconAIPage = lazyWithRetry(
  () => import("src/pages/dashboard/falcon-ai/FalconAI"),
);
const Feed = lazyWithRetry(() => import("src/pages/dashboard/feed/Feed"));
const FeedDetail = lazyWithRetry(
  () => import("src/pages/dashboard/feed/FeedDetail"),
);
const ErrorFeed = lazyWithRetry(
  () => import("src/pages/dashboard/error-feed/ErrorFeed"),
);
const ErrorFeedDetail = lazyWithRetry(
  () => import("src/pages/dashboard/error-feed/ErrorFeedDetail"),
);
const AnnotationLabelsPage = lazyWithRetry(
  () => import("src/pages/dashboard/annotations/labels"),
);
const AnnotationQueuesPage = lazyWithRetry(
  () => import("src/pages/dashboard/annotations/queues"),
);
const QueueDetailPage = lazyWithRetry(
  () => import("src/pages/dashboard/annotations/queue-detail"),
);
const AnnotateWorkspacePage = lazyWithRetry(
  () => import("src/pages/dashboard/annotations/annotate-workspace"),
);

const DashboardsListView = lazyWithRetry(
  () => import("src/sections/dashboards/DashboardsListView"),
);
const DashboardDetailView = lazyWithRetry(
  () => import("src/sections/dashboards/DashboardDetailView"),
);
const WidgetEditorView = lazyWithRetry(
  () => import("src/sections/dashboards/WidgetEditorView"),
);

// const Agents = lazyWithRetry(() => import("src/sections/agent-playground/Agents"));
// const AgentPlayground = lazyWithRetry(
//   () => import("src/sections/agent-playground/AgentPlayground"),
// );
// const AgentBuilder = lazyWithRetry(
//   () => import("src/sections/agent-playground/AgentBuilder/AgentBuilder"),
// );
// const Overview = lazyWithRetry(
//   () => import("src/sections/agent-playground/Overview/Overview"),
// );
// const Executions = lazyWithRetry(
//   () => import("src/sections/agent-playground/Executions/Executions"),
// );

const Agents = lazyWithRetry(
  () => import("src/sections/agent-playground/Agents"),
);
const AgentPlayground = lazyWithRetry(
  () => import("src/sections/agent-playground/AgentPlayground"),
);
const AgentBuilder = lazyWithRetry(
  () => import("src/sections/agent-playground/AgentBuilder/AgentBuilder"),
);
const Overview = lazyWithRetry(
  () => import("src/sections/agent-playground/Overview/Overview"),
);
const Executions = lazyWithRetry(
  () => import("src/sections/agent-playground/Executions/Executions"),
);

// TODO: Remove after verifying the error boundary
const ErrorBoundaryTest = () => {
  throw new Error("This is a test error to preview the error boundary UI");
};

const DashboardRoutes = () => {
  const location = useLocation();

  React.useEffect(() => {
    const { eventName, extras = {} } = getPageViewEvent(
      location.pathname,
      location.search,
    ) || { eventName: Events.pageView, extras: {} };
    trackEvent(eventName, { path: location.pathname, ...extras });
  }, [location]);

  return (
    <AuthGuard>
      <DashboardLayout>
        <Suspense fallback={<LoadingScreen sx={undefined} />}>
          <Outlet />
        </Suspense>
      </DashboardLayout>
    </AuthGuard>
  );
};

export const dashboardRoutes = (
  user,
  workspaceRole,
  { isOSS = false } = {},
) => {
  const userOrgRole = user?.organization_role ?? user?.organizationRole;
  const userDefaultWsRole =
    user?.default_workspace_role ?? user?.defaultWorkspaceRole;
  const isOwner = user === null ? true : userOrgRole === "Owner";
  const effectiveWsRole = workspaceRole || userDefaultWsRole;
  const isAdmin =
    userOrgRole === "Admin" || effectiveWsRole === "workspace_admin";
  const settingsRoute = [
    {
      index: true,
      element: <Navigate to="/dashboard/settings/profile-settings" replace />,
    },
    {
      path: "manage-team",
      element: (
        <RoleProtection allowedRoles={["Owner"]}>
          <ManageTeamPage />
        </RoleProtection>
      ),
    },

    {
      path: "profile-settings",
      element: (
        <RoleProtection
          allowedRoles={[
            "Owner",
            "Admin",
            "Member",
            "Viewer",
            "workspace_admin",
            "workspace_member",
            "workspace_viewer",
          ]}
        >
          <ProfileSettings />
        </RoleProtection>
      ),
    },

    {
      path: "org-settings",
      element: (
        <RoleProtection allowedRoles={["Owner", "Admin"]}>
          <OrgSettings />
        </RoleProtection>
      ),
    },

    {
      path: "api_keys",
      element: (
        <RoleProtection allowedRoles={["Owner"]}>
          <ApiKeys />
        </RoleProtection>
      ),
    },
    {
      path: "usage-summary",
      element: (
        <RoleProtection
          allowedRoles={[
            "Owner",
            "Admin",
            "Member",
            "Viewer",
            "workspace_admin",
            "workspace_member",
            "workspace_viewer",
          ]}
        >
          <UsageSummary />
        </RoleProtection>
      ),
    },
    {
      path: "user-management",
      element: (
        <RoleProtection allowedRoles={["Owner", "Admin"]}>
          <UserManagementV2 />
        </RoleProtection>
      ),
    },
    {
      path: "coming-soon",
      element: (
        <RoleProtection allowedRoles={["Owner"]}>
          <ComingSoon />
        </RoleProtection>
      ),
    },
    {
      path: "checkout",
      element: (
        <RoleProtection allowedRoles={["Owner"]}>
          <CheckoutPage />
        </RoleProtection>
      ),
    },

    {
      path: "payment-success",
      element: (
        <RoleProtection allowedRoles={["Owner"]}>
          <Success />
        </RoleProtection>
      ),
    },
    {
      path: "payment-cancel",
      element: (
        <RoleProtection allowedRoles={["Owner"]}>
          <Cancel />
        </RoleProtection>
      ),
    },
    // {
    //   path: "custom-model",
    //   element: (
    //     <RoleProtection allowedRoles={["Owner"]}>
    //       <UsersCustomModel />
    //     </RoleProtection>
    //   ),
    // },
    {
      path: "ai-providers",
      element: (
        <RoleProtection
          allowedRoles={[
            "Owner",
            "Admin",
            "Member",
            "workspace_admin",
            "workspace_member",
          ]}
        >
          <AIProviders />
        </RoleProtection>
      ),
    },
    {
      path: "integrations",
      element: (
        <RoleProtection
          allowedRoles={[
            "Owner",
            "Admin",
            "Member",
            "workspace_admin",
            "workspace_member",
          ]}
        >
          <IntegrationsPage />
        </RoleProtection>
      ),
    },
    {
      path: "integrations/:connectionId",
      element: (
        <RoleProtection
          allowedRoles={[
            "Owner",
            "Admin",
            "Member",
            "workspace_admin",
            "workspace_member",
          ]}
        >
          <IntegrationDetailPage />
        </RoleProtection>
      ),
    },
    {
      path: "mcp-server",
      element: (
        <RoleProtection allowedRoles={["Owner", "Admin"]}>
          <MCPServerPage />
        </RoleProtection>
      ),
    },
    ...(!isOSS
      ? [
          {
            path: "falcon-ai-connectors",
            element: (
              <RoleProtection
                allowedRoles={[
                  "Owner",
                  "Admin",
                  "Member",
                  "workspace_admin",
                  "workspace_member",
                ]}
              >
                <FalconAIConnectorsPage />
              </RoleProtection>
            ),
          },
        ]
      : []),
  ];

  // Conditionally include billing routes:
  // - Hidden entirely in OSS mode (no billing infrastructure)
  // - Role-gated in Cloud/EE mode
  const billingAllowedRoles = ["Owner", "Admin", "workspace_admin"];
  const hasBillingAccess =
    !isOSS && (isOwner || billingAllowedRoles.includes(effectiveWsRole));

  if (hasBillingAccess) {
    settingsRoute.push(
      ...[
        {
          path: "billing",
          element: (
            <RoleProtection allowedRoles={billingAllowedRoles}>
              <BillingPageV2 />
            </RoleProtection>
          ),
        },
        {
          path: "ee-licenses",
          element: (
            <RoleProtection allowedRoles={billingAllowedRoles}>
              <EELicensesPage />
            </RoleProtection>
          ),
        },
        {
          path: "pricing",
          element: (
            <RoleProtection allowedRoles={billingAllowedRoles}>
              <PricingPage />
            </RoleProtection>
          ),
        },
      ],
    );
  }

  if (user === null || (user?.ws_enabled ?? user?.wsEnabled)) {
    settingsRoute.push({
      path: "workspace",
      children: [
        {
          index: true,
          element: (
            <RoleProtection allowedRoles={["Owner", "Admin"]}>
              <WorkSpaceManagement />
            </RoleProtection>
          ),
        },
        {
          path: ":workspaceId",
          children: [
            {
              index: true,
              element: <Navigate to="general" replace />,
            },
            {
              path: "general",
              element: (
                <WorkspaceRoleProtection
                  allowedRoles={[
                    "workspace_admin",
                    "workspace_member",
                    "workspace_viewer",
                  ]}
                >
                  <WorkspaceGeneral />
                </WorkspaceRoleProtection>
              ),
            },
            {
              path: "usage",
              element: (
                <WorkspaceRoleProtection
                  allowedRoles={[
                    "workspace_admin",
                    "workspace_member",
                    "workspace_viewer",
                  ]}
                >
                  <WorkspaceUsage />
                </WorkspaceRoleProtection>
              ),
            },
            {
              path: "members",
              element: (
                <WorkspaceRoleProtection allowedRoles={["workspace_admin"]}>
                  <WorkspaceMembers />
                </WorkspaceRoleProtection>
              ),
            },
            {
              path: "integrations",
              element: (
                <WorkspaceRoleProtection
                  allowedRoles={["workspace_admin", "workspace_member"]}
                >
                  <WorkspaceIntegrations />
                </WorkspaceRoleProtection>
              ),
            },
            {
              path: "ai-providers",
              element: (
                <WorkspaceRoleProtection
                  allowedRoles={["workspace_admin", "workspace_member"]}
                >
                  <WorkspaceAIProviders />
                </WorkspaceRoleProtection>
              ),
            },
          ],
        },
      ],
    });
  }

  const dashboardChildren = [
    {
      index: true,
      element: <Navigate to="/dashboard/prototype" replace />,
    },
    {
      path: "/dashboard/get-started",
      children: [
        {
          index: true,
          element: <GetStarted />,
        },
      ],
    },
    // {
    //   path: "models",
    //   children: [
    //     { element: <Models />, index: true },
    //     {
    //       path: ":id",
    //       element: (
    //         <DatasetContextProvider>
    //           <ModelDetail />
    //         </DatasetContextProvider>
    //       ),
    //       children: [
    //         {
    //           index: true,
    //           element: <Navigate to="/dashboard/models" replace />,
    //         },
    //         { path: "performance", element: <Performance /> },
    //         { path: "custom-metrics", element: <CustomMetric /> },
    //         {
    //           path: "datasets",

    //           children: [
    //             { index: true, element: <Datasets /> },

    //             {
    //               path: ":dataset",
    //               element: <DatasetDetail />,
    //             },
    //           ],
    //         },
    //         {
    //           path: "optimize",
    //           children: [
    //             { index: true, element: <OptimizeList /> },
    //             {
    //               path: ":optimizeId",
    //               element: <OptimizeDetail />,
    //             },
    //           ],
    //         },
    //         { path: "report", element: <PerformanceReport /> },
    //         { path: "config", element: <ModeConfig /> },
    //       ],
    //     },
    //   ],
    // },

    ...(!isOSS
      ? [
          {
            path: "falcon-ai/:conversationId?",
            element: <FalconAIPage />,
          },
        ]
      : []),
    {
      path: "tasks",
      children: [
        { index: true, element: <TasksPage /> },
        { path: "create", element: <TaskCreate /> },
        { path: ":taskId", element: <TaskDetail /> },
      ],
    },
    {
      path: "users",
      element: <UserList />,
    },
    // {
    //   path: "feed",
    //   element: <TasksPage />,
    // },
    {
      path: "users/:userId",
      element: <CrossProjectUserDetailPage />,
    },
    // {
    //   path: "prototype",
    //   element: <Typography>Coming Soon...</Typography>,
    // },
    {
      path: "annotations",
      children: [
        {
          index: true,
          element: <Navigate to="queues" replace />,
        },
        {
          path: "labels",
          element: <AnnotationLabelsPage />,
        },
        {
          path: "queues",
          element: <AnnotationQueuesPage />,
        },
        {
          path: "queues/:queueId",
          element: <QueueDetailPage />,
        },
        {
          path: "queues/:queueId/annotate",
          element: <AnnotateWorkspacePage />,
        },
      ],
    },
    // {
    //   path: "evaluations",
    //   element: <Evals />,
    // },
    {
      path: "evaluations",
      children: [
        {
          index: true,
          element: <Evals />,
        },
        {
          path: "usage",
          element: <EvalsUsage />,
        },
        {
          path: "create",
          element: <EvalCreate />,
        },
        {
          path: "create/:draftId",
          element: <EvalCreate />,
        },
        {
          path: ":evalId",
          element: <EvalDetail />,
        },
        {
          path: "groups",
          element: <EvalGroups />,
        },
        {
          path: "groups/:id", // child of "groups"
          element: <EvalsIndividualGroup />,
        },
      ],
    },
    // {
    //   path: "sync",
    //   element: <SyncData />,
    //   children: [
    //     {
    //       index: true,
    //       element: <Navigate to="/dashboard/sync/connectors" replace />,
    //     },
    //     {
    //       path: "connectors",
    //       children: [
    //         { index: true, element: <ConnectorView /> },
    //         {
    //           path: "big-query",
    //           element: <BigQueryWizard />,
    //         },
    //         {
    //           path: "upload-file",
    //           element: <UploadFileWizard />,
    //         },
    //       ],
    //     },
    //     {
    //       path: "job-status",
    //       element: <JobStatusView />,
    //     },
    //   ],
    // },
    {
      path: "prototype",
      element: <ProjectWrapper />,
      children: [
        {
          index: true,
          element: <ProjectList />,
        },
      ],
    },
    {
      path: "prototype/:projectId",
      children: [
        {
          index: true,
          element: <ProjectDetail />,
        },
        {
          path: ":runId",
          element: <RunInsidePage />,
        },
      ],
    },
    // {
    //   path: "projects",
    //   element: <ProjectWrapper />,
    //   children: [
    //     {
    //       index: true,
    //       element: <Navigate to="/dashboard/projects/experiment" replace />,
    //     },
    //     {
    //       path: "experiment",
    //       element: <ProjectList />,
    //     },
    // {
    //   path: "observe",
    //   element: <ObserveList />,
    // },
    // ],
    // },
    // {
    //   path: "project",
    //   children: [
    //     {
    //       index: true,
    //       element: <Navigate to="/dashboard/projects/experiment" replace />,
    //     },
    //     {
    // path: "runID",
    //       element: <RunInsidePage />,
    //     },
    //     {
    //       path: ":projectId",
    //       element: <ProjectDetail />,
    //     },
    //     {
    //       path: ":projectId/:runId",
    //       element: <RunInsidePage />,
    //     },
    //   ],
    // },
    {
      path: "observe",
      element: <ProjectWrapper />,
      children: [
        {
          index: true,
          element: <ObserveList />,
        },
      ],
    },
    {
      path: "observe/:observeId/trace/:traceId",
      element: <TraceFullPage />,
    },
    {
      path: "observe/:observeId/voice/:callId",
      element: <VoiceFullPage />,
    },
    {
      path: "observe/:observeId",
      element: <ObserverWrapper />,
      children: [
        {
          index: true,
          element: <Navigate to="llm-tracing" replace />,
        },
        // {
        //   path: "logs",
        //   element: <LogsView />,
        // },
        {
          path: "llm-tracing",
          element: <LLMTracingView />,
        },
        {
          path: "voice",
          element: (
            <div style={{ padding: 32, color: "#888", textAlign: "center" }}>
              Voice observability coming soon.
            </div>
          ),
        },
        {
          path: "sessions",
          element: <SessionsView />,
        },
        {
          path: "users",
          element: <UsersView />,
        },
      ],
    },
    {
      path: "develop",
      children: [
        {
          index: true,
          element: <Develop />,
        },
        ...(!isOSS
          ? [
              {
                path: "create-synthetic-dataset",
                element: <CreateSyntheticData />,
              },
              {
                path: "edit-synthetic-dataset/:dataset",
                element: <EditSyntheticDataDrawer />,
              },
            ]
          : []),

        {
          path: ":dataset",
          element: <DevelopDetail />,
          children: [
            {
              path: "preview/:id",
              element: <PreviewScreen />,
            },
          ],
        },
        {
          path: "experiment/:experimentId",
          element: <ExperimentWrapper />,
          children: [
            {
              index: true,
              element: <Navigate to="data" replace />,
            },
            {
              path: "data",
              element: <ExperimentData />,
            },
            {
              path: "summary",
              element: <ExperimentSummary />,
            },
          ],
        },
        {
          path: "individual-experiment/:individualExperimentId",
          element: <IndividualExperimentWrapper />,
          children: [
            {
              index: true,
              element: <Navigate to="data" replace />,
            },
            {
              path: "data",
              element: <IndividualExperimentData />,
            },
            {
              path: "summary",
              element: <IndividualExperimentSummary />,
            },
          ],
        },
      ],
    },
    {
      path: "knowledge",
      children: [
        {
          index: true,
          element: <KnowledgeBase />,
        },
        {
          path: ":knowledgeId",
          element: <KnowledgeBaseDetailView />,
        },
      ],
    },
    {
      path: "prompt",
      children: [
        {
          index: true,
          element: <Prompt />,
        },
        {
          path: "add/:id",
          element: <AddNewPrompt />,
        },
      ],
    },
    {
      path: "workbench",
      children: [
        {
          element: <PromptDir />, // 👈 wrapper (with sidebar)
          children: [
            {
              index: true,
              element: <Navigate to={"all"} replace />,
            },
            {
              path: ":folder",
              element: <FolderView />,
            },
          ],
        },
        {
          path: "create/:id",
          element: <CreatePrompt />,
        },
      ],
    },
    {
      path: "agents",
      children: [
        {
          index: true,
          element: <Agents />,
        },
        {
          path: "playground/:agentId",
          element: <AgentPlayground />,
          children: [
            {
              index: true,
              element: <Navigate to="build" replace />,
            },
            {
              path: "build",
              element: <AgentBuilder />,
            },
            {
              path: "changelog",
              element: <Overview />,
            },
            {
              path: "executions",
              element: <Executions />,
            },
          ],
        },
      ],
    },
    {
      path: "alerts",
      element: <AlertMainView />,
    },
    {
      path: "feed",
      children: [
        {
          index: true,
          element: <Feed />,
        },
        {
          path: ":id",
          element: <FeedDetail />,
        },
      ],
    },
    {
      path: "error-feed",
      children: [
        {
          index: true,
          element: <ErrorFeed />,
        },
        {
          path: ":id",
          element: <ErrorFeedDetail />,
        },
      ],
    },
    {
      path: "huggingface",
      element: <HuggingFacePage />,
    },
    {
      path: "loremipsum",
      element: <ErrorFallbackView />,
    },
    {
      path: "simulate",
      children: [
        {
          path: "agent-definitions",
          element: <AgentDefinitions />,
        },
        {
          path: "agent-definitions/create-new-agent-definition",
          element: <CreateNewAgentDefinition />,
        },
        {
          path: "agent-definitions/:agentDefinitionId",
          element: <AgentDetails />,
        },
        {
          path: "scenarios",
          children: [
            {
              index: true,
              element: <Scenarios />,
            },
            {
              path: "create",
              element: <CreateScenario />,
            },
          ],
        },
        {
          path: "scenarios/:scenarioId",
          element: <ScenarioDatasetView />,
        },
        // {
        //   path: "simulator-agent",
        //   element: <SimulatorAgent />,
        // },
        {
          path: "personas",
          element: <Personas />,
        },
        {
          path: "test",
          element: <RunTests />,
        },
        {
          path: "test/:testId",
          element: <RunTestDetail />,

          children: [
            {
              index: true,
              element: <Navigate to="runs" replace />,
            },
            {
              path: "runs",
              element: <TestRuns />,
            },
            {
              path: "call-logs",
              element: <CallLogs />,
            },
            {
              path: "analytics",
              element: <TestAnalytics />,
            },
          ],
        },
        {
          path: "test/:testId/:executionId",
          element: <TestDetail />,
          children: [
            {
              index: true,
              element: <Navigate to="call-details" replace />,
            },
            {
              path: "call-details",
              element: <TestExecutionCallDetail />,
            },
            {
              path: "performance",
              element: <TestExecutionPerformanceDetail />,
            },
            {
              path: "analytics",
              element: <TestExecutionAnalyticsDetail />,
            },
            {
              path: "optimization_runs",
              element: <TestExecutionOptimizationRunsDetail />,
            },
          ],
        },
        {
          path: "test/:testId/:executionId/:optimizationId",
          element: <TestExecutionOptimizationDetail />,
        },
        {
          path: "test/:testId/:executionId/:optimizationId/:trialId",
          element: <TestExecutionOptimizationTrialDetail />,
        },
      ],
    },
    {
      path: "settings",
      element: <SettingsLayout />,
      children: settingsRoute,
    },
    {
      path: "gateway",
      element: <GatewayProvider />,
      children: [
        {
          element: (
            <GatewayGuard>
              <Outlet />
            </GatewayGuard>
          ),
          children: [
            { index: true, element: <GatewayOverview /> },
            { path: "keys", element: <GatewayKeys /> },
            { path: "providers", element: <GatewayProviders /> },
            { path: "providers/:tab", element: <GatewayProviders /> },
            { path: "guardrails", element: <GatewayGuardrails /> },
            { path: "guardrails/:tab", element: <GatewayGuardrails /> },
            { path: "budgets", element: <GatewayBudgets /> },
            { path: "budgets/:tab", element: <GatewayBudgets /> },
            { path: "monitoring", element: <GatewayMonitoring /> },
            { path: "monitoring/:tab", element: <GatewayMonitoring /> },
            { path: "logs", element: <GatewayLogs /> },
            { path: "analytics", element: <GatewayAnalytics /> },
            { path: "analytics/:tab", element: <GatewayAnalytics /> },
            { path: "webhooks", element: <GatewayWebhooks /> },
            { path: "webhooks/:tab", element: <GatewayWebhooks /> },
            { path: "sessions", element: <GatewaySessions /> },
            { path: "custom-properties", element: <GatewayCustomProperties /> },
            { path: "fallbacks", element: <GatewayFallbacks /> },
            { path: "mcp", element: <GatewayMCP /> },
            { path: "mcp/:tab", element: <GatewayMCP /> },
            // { path: "experiments", element: <GatewayExperiments /> },
            // {
            //   path: "experiments/:experimentId",
            //   element: <GatewayExperiments />,
            // },
            { path: "settings", element: <GatewaySettings /> },
            { path: "settings/:tab", element: <GatewaySettings /> },
          ],
        },
      ],
    },
    {
      path: "dashboards",
      element: <DashboardsListView />,
    },
    {
      path: "dashboards/:dashboardId",
      element: <DashboardDetailView />,
    },
    {
      path: "dashboards/:dashboardId/widget/:widgetId",
      element: <WidgetEditorView />,
    },
    // TODO: Remove this test route after verifying the error boundary
    {
      path: "error-test",
      element: <ErrorBoundaryTest />,
    },
  ];

  if (isOwner || isAdmin) {
    dashboardChildren.push({
      path: "keys",
      element: <DevKeysPage />,
    });
  }

  return [
    {
      path: "dashboard",
      element: <DashboardRoutes />,
      children: dashboardChildren,
    },
  ];
};
