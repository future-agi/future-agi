/* eslint-disable react-refresh/only-export-components */
import React from "react";
import { Suspense } from "react";
import PropTypes from "prop-types";
import { Navigate, Outlet, useLocation, useParams } from "react-router-dom";
import {
  PROTOTYPE_ABSENT_FALLBACK,
  isAbsentInPrototype,
} from "./prototypeAbsentRoutes";
import { launchRedirect } from "src/launch/launchRoutes";
import { CONNECTED_AGENTS_PATH } from "src/sections/agent-playground/agentsTabs";

import { AuthGuard } from "src/auth/guard";
import { SplashScreen } from "src/components/loading-screen";

import RouteSkeleton from "src/layouts/dashboard/RouteSkeleton";
import { Events, getPageViewEvent, trackEvent } from "src/utils/Mixpanel";
import HarnessToEnvironment from "../components/harness-to-environment";
import RoleProtection from "../components/role-protection";
// Shown on the Billing URL to a role that cannot open Billing.
import BillingNoAccess from "src/sections/settings/BillingV2/BillingNoAccess";
import WorkspaceRoleProtection from "../components/workspace-role-protection";
import { GatewayProvider } from "src/sections/gateway/context/GatewayContext";
import GatewayGuard from "src/sections/gateway/components/GatewayGuard";
import GatewayRedirect from "src/sections/gateway/shell/GatewayRedirect";
import CapabilitiesRedirect from "src/sections/gateway/shell/CapabilitiesRedirect";
import {
  hiddenPageRedirect,
  isGatewayControlHidden,
  isGatewayPageHidden,
} from "src/sections/gateway/shell/page-status";
import lazyWithRetry from "src/utils/lazyWithRetry";
import CapabilityGate from "src/components/capability-gate";
import { CAPABILITY } from "src/hooks/useCapabilities";
import { omegaDashboardRoutes, omegaSettingsRoutes } from "./omega";
import { paths } from "src/routes/paths";
import { useDatasetRuns, useExperimentDetail } from "src/api/develop/runs";
import { defaultRunsTab } from "src/sections/common/Runs/runKind";
import DatasetTabGate from "./DatasetTabGate";
import DatasetListPageGate from "./DatasetListPageGate";
// The dashboard shell (nav, palette, run tray, rerun host) is its own chunk:
// sign-in and other public pages never download it (perf #1).
const DashboardLayout = lazyWithRetry(() => import("src/layouts/dashboard"));
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
// Matrix tab of an experiment (R2-SPEC P1).
const ExperimentMatrix = lazyWithRetry(
  () => import("src/pages/dashboard/Develop/ExperimentMatrix"),
);
// R4 (implementation): the Paths tab and the prototype-only lab.
const ExperimentPaths = lazyWithRetry(
  () => import("src/pages/dashboard/Develop/ExperimentPaths"),
);
// the interface contract: the Traces tab of an experiment.
const ExperimentTraces = lazyWithRetry(
  () => import("src/sections/experiment-detail/traces/ExperimentTraces"),
);
// Prototype builds only: the env check is static, so production bundles do
// not emit the lab chunk or its fixture id generators (tests F7).
const PROTOTYPE_BUILD =
  import.meta.env.VITE_ENVIRONMENT === "local" &&
  import.meta.env.VITE_PROTOTYPE_MODE === "true";
// L7b absent-sweep (url:dev-pages): the R4 lab is a dev-server page only;
// preview and walk builds (vite build) do not ship it.
const R4Lab =
  PROTOTYPE_BUILD && import.meta.env.DEV
    ? lazyWithRetry(() => import("src/pages/dashboard/Develop/R4Lab"))
    : null;
// The one global host of the Add-data sheet (R2-SPEC P5): every entry point
// opens it through openAddData(...) (sections/common/AddData/addDataStore).
const AddDataSheet = lazyWithRetry(
  () => import("src/sections/common/AddData/AddDataSheet"),
);
// Compare runs across experiments and the focused review page
// (PRODUCT-SPEC §2.1); siblings of :dataset.
const DatasetCompare = lazyWithRetry(
  () => import("src/pages/dashboard/Develop/DatasetCompare"),
);
const TaskCompare = lazyWithRetry(
  () => import("src/pages/dashboard/Develop/TaskCompare"),
);
const DatasetReview = lazyWithRetry(
  () => import("src/pages/dashboard/Develop/DatasetReview"),
);
// the interface contract: workspace leaderboards across datasets. Its paths are
// prototype-only, so other builds emit no Leaderboards chunk (HO S2).
const Leaderboards = PROTOTYPE_BUILD
  ? lazyWithRetry(() => import("src/pages/dashboard/Develop/Leaderboards"))
  : null;
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
const WebhookEndpointPage = lazyWithRetry(
  () =>
    import("src/sections/settings/integrations/webhooks/WebhookEndpointPage"),
);
const IntegrationsWorkspaceScope = lazyWithRetry(
  () => import("src/sections/settings/integrations/IntegrationsWorkspaceScope"),
);
const ApiKeys = lazyWithRetry(
  () => import("src/pages/dashboard/settings/ApiKeys"),
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
const LicensePage = lazyWithRetry(
  () => import("src/sections/settings/License/LicensePage"),
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
const GatewayIntegrations = lazyWithRetry(
  () => import("src/pages/dashboard/gateway/GatewayIntegrations"),
);
const GatewayModels = lazyWithRetry(
  () => import("src/pages/dashboard/gateway/GatewayModels"),
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
const GatewayRuns = lazyWithRetry(
  () => import("src/pages/dashboard/gateway/GatewayRuns"),
);
const GatewayRunDetail = lazyWithRetry(
  () => import("src/pages/dashboard/gateway/GatewayRunDetail"),
);
const GatewayPolicies = lazyWithRetry(
  () => import("src/pages/dashboard/gateway/GatewayPolicies"),
);
const GatewayPlayground = lazyWithRetry(
  () => import("src/pages/dashboard/gateway/GatewayPlayground"),
);
const GatewayCapabilities = lazyWithRetry(
  () => import("src/pages/dashboard/gateway/GatewayCapabilities"),
);
const ObserveList = lazyWithRetry(
  () => import("src/pages/dashboard/projects/ObserveList"),
);
const ProjectWrapper = lazyWithRetry(
  () => import("src/pages/dashboard/projects/ProjectWrapper"),
);
const HuggingFacePage = lazyWithRetry(
  () => import("src/pages/dashboard/huggingface/HuggingFace"),
);
const Models = lazyWithRetry(() => import("src/pages/dashboard/models/Models"));
const ModelDetail = lazyWithRetry(
  () => import("src/pages/dashboard/models/ModelDetail"),
);
const Performance = lazyWithRetry(
  () => import("src/pages/dashboard/models/Performance/Performance"),
);
const CustomMetric = lazyWithRetry(
  () => import("src/pages/dashboard/models/CustomMetric/CustomMetric"),
);
const Datasets = lazyWithRetry(
  () => import("src/sections/model/datasets/Datasets"),
);
const DatasetDetail = lazyWithRetry(
  () => import("src/pages/dashboard/models/DatasetDetail"),
);
const OptimizeList = lazyWithRetry(
  () => import("src/sections/model/optimize/OptimizeList"),
);
const OptimizeDetail = lazyWithRetry(
  () => import("src/pages/dashboard/models/OptimizeDetail"),
);
const PerformanceReport = lazyWithRetry(
  () =>
    import("src/pages/dashboard/models/PerformanceReport/PerformanceReport"),
);
const ModeConfig = lazyWithRetry(
  () => import("src/pages/dashboard/models/ModelConfig/ModeConfig"),
);
const DatasetContextProvider = lazyWithRetry(() =>
  import("src/pages/dashboard/models/DatasetContext").then((module) => ({
    default: module.DatasetContextProvider,
  })),
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
const CreateScenario = lazyWithRetry(
  () => import("src/pages/dashboard/scenarios/CreateScenario"),
);
const ScenarioDatasetView = lazyWithRetry(
  () => import("src/sections/scenarios/scenario-detail/ScenarioDatasetView"),
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
const SimulateEnvironmentsHome = lazyWithRetry(
  () => import("src/pages/dashboard/simulate/environments/EnvironmentsHome"),
);
const SimulatePrebuiltEnvironments = lazyWithRetry(
  () =>
    import("src/pages/dashboard/simulate/environments/PrebuiltEnvironments"),
);
const SimulateUseTemplate = lazyWithRetry(
  () => import("src/pages/dashboard/simulate/environments/UseTemplate"),
);
const SimulateEnvironmentWorkspace = lazyWithRetry(
  () =>
    import("src/pages/dashboard/simulate/environments/EnvironmentWorkspace"),
);
const WorkspaceExecutionDetail = lazyWithRetry(
  () =>
    import(
      "src/sections/simulate/environments/workspace/runs/WorkspaceExecutionDetail"
    ),
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
// the interface contract (§9 transfer, M6): Settings › Audit log for the workspace. It reads
// the prototype-only /model-hub/audit/, so other builds emit no chunk (HO S2).
const WorkspaceAuditLog = PROTOTYPE_BUILD
  ? lazyWithRetry(
      () =>
        import(
          "src/pages/dashboard/settings/WorkspaceSettings/WorkspaceAuditLog"
        ),
    )
  : null;
const WorkspaceGeneral = lazyWithRetry(
  () =>
    import("src/pages/dashboard/settings/WorkspaceSettings/WorkspaceGeneral"),
);
const FalconAIPage = lazyWithRetry(
  () => import("src/pages/dashboard/falcon-ai/FalconAI"),
);
const ErrorFeed = lazyWithRetry(
  () => import("src/pages/dashboard/error-feed/ErrorFeed"),
);
const ErrorFeedAlerts = lazyWithRetry(
  () => import("src/pages/dashboard/error-feed/ErrorFeedAlerts"),
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

function LegacyFeedDetailRedirect() {
  const { id } = useParams();
  return <Navigate to={`/dashboard/error-feed/${id}`} replace />;
}
function ModelDetailDefaultRedirect() {
  const { id } = useParams();
  return <Navigate to={`/dashboard/models/${id}/performance`} replace />;
}

// A tabbed gateway page renders only for one of its known tabs. `tabPaths` is
// its paths entry: `root` plus one URL per tab, in tab order. A missing or
// unknown tab goes to the first tab.
function GatewayTabRoute({ tabPaths, children }) {
  const { tab } = useParams();
  const location = useLocation();
  const { root: _root, ...tabUrls } = tabPaths;
  if (!Object.prototype.hasOwnProperty.call(tabUrls, tab)) {
    // Keep the query and hash: a bookmarked filter survives the redirect.
    return (
      <Navigate
        to={{
          pathname: Object.values(tabUrls)[0],
          search: location.search,
          hash: location.hash,
        }}
        replace
      />
    );
  }
  return children;
}
GatewayTabRoute.propTypes = {
  tabPaths: PropTypes.objectOf(PropTypes.string).isRequired,
  children: PropTypes.node.isRequired,
};
const gatewayPoliciesPage = (
  <GatewayTabRoute tabPaths={paths.dashboard.gateway.policies}>
    <GatewayPolicies />
  </GatewayTabRoute>
);
// A hidden gateway page (sections/gateway/shell/page-status.js) redirects to
// the nearest page that works, query and hash kept; the page itself comes
// back when its entry there is deleted.
const gatewayPage = (pageId, element) =>
  isGatewayPageHidden(pageId) ? (
    <GatewayRedirect to={hiddenPageRedirect(pageId)} />
  ) : (
    element
  );
// `/compare` -> `/compare/<default tab>`, keeping ?runs=&baseline=. The
// default tab is `matrix` when the runs are of more than one kind, else
// `data` (R2-SPEC §0.3); nothing renders until the dataset's runs resolve.
function CompareIndexRedirect() {
  const { search } = useLocation();
  const { dataset } = useParams();
  const runIds = (new URLSearchParams(search).get("runs") ?? "")
    .split(",")
    .filter(Boolean);
  const { data, isError } = useDatasetRuns(dataset, {
    enabled: runIds.length > 0,
  });
  if (runIds.length && !data && !isError) return null;
  const runs = (data?.results ?? []).filter((r) => runIds.includes(r.run_id));
  return <Navigate to={{ pathname: defaultRunsTab(runs), search }} replace />;
}
// Experiment index -> `data`, or `matrix` when its runs are of more than one
// kind (R2-SPEC §0.3).
function ExperimentIndexRedirect() {
  const { search } = useLocation();
  const { experimentId } = useParams();
  const { data, isError } = useExperimentDetail(experimentId);
  if (!data && !isError) return null;
  return (
    <Navigate
      to={{ pathname: defaultRunsTab(data?.runs ?? []), search }}
      replace
    />
  );
}
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
const AgentsIndexRedirect = lazyWithRetry(
  () => import("src/sections/agent-playground/AgentsIndexRedirect"),
);
const ConnectedAgentsTab = lazyWithRetry(
  () => import("src/sections/agent-playground/ConnectedAgentsTab"),
);
const PlaygroundAgentsTab = lazyWithRetry(
  () => import("src/sections/agent-playground/PlaygroundAgentsTab"),
);
const AgentDetailLayout = lazyWithRetry(
  () =>
    import("src/sections/agent-playground/connected/detail/AgentDetailLayout"),
);
const AgentDetailIndexRedirect = lazyWithRetry(
  () =>
    import(
      "src/sections/agent-playground/connected/detail/AgentDetailIndexRedirect"
    ),
);
const AgentOverviewTab = lazyWithRetry(
  () =>
    import("src/sections/agent-playground/connected/detail/AgentOverviewTab"),
);
const AgentDefinitionTab = lazyWithRetry(
  () =>
    import("src/sections/agent-playground/connected/detail/AgentDefinitionTab"),
);
const AgentVersionsTab = lazyWithRetry(
  () =>
    import("src/sections/agent-playground/connected/detail/AgentVersionsTab"),
);
const AgentEnvironmentsTab = lazyWithRetry(
  () =>
    import(
      "src/sections/agent-playground/connected/detail/AgentEnvironmentsTab"
    ),
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

const DashboardRoutes = () => {
  const location = useLocation();
  // L6: null outside a launch build (prototype builds and unit tests).
  const launchTo = PROTOTYPE_BUILD ? null : launchRedirect(location);

  React.useEffect(() => {
    const { eventName, extras = {} } = getPageViewEvent(
      location.pathname,
      location.search,
    ) || { eventName: Events.pageView, extras: {} };
    trackEvent(eventName, { path: location.pathname, ...extras });
  }, [location]);

  return (
    <AuthGuard>
      {/* The shell chunk loads once, at boot; after that it stays. */}
      <Suspense fallback={<SplashScreen />}>
        <DashboardLayout>
          {/* R3 G0: the shell stays; never the full-screen rocket after boot. */}
          <Suspense fallback={<RouteSkeleton />}>
            {/* L7b absent-sweep: a page absent from a prototype build lands
                on the Datasets list (prototypeAbsentRoutes.js). */}
            {PROTOTYPE_BUILD && isAbsentInPrototype(location.pathname) ? (
              <Navigate to={PROTOTYPE_ABSENT_FALLBACK} replace />
            ) : launchTo ? (
              // L6 launch switch: a hidden surface's route or deep-link key
              // lands on its fallback (src/launch/launchRoutes.js).
              <Navigate to={launchTo.to} replace />
            ) : (
              <Outlet />
            )}
          </Suspense>
          <Suspense fallback={null}>
            <AddDataSheet />
          </Suspense>
        </DashboardLayout>
      </Suspense>
    </AuthGuard>
  );
};

// `falconAI`: whether the deployment allows Falcon AI, as the router reads
// it (useFeatureRouteAnswer): "allowed", "denied" or "pending". Left out, it
// is "denied".
export const dashboardRoutes = (
  user,
  workspaceRole,
  { isCloud = false, falconAI = "denied" } = {},
) => {
  const userOrgRole = user?.organization_role;
  const userDefaultWsRole = user?.default_workspace_role;
  const isOwner = user === null ? true : userOrgRole === "Owner";
  const effectiveWsRole = workspaceRole || userDefaultWsRole;
  // Orgs created after the cutoff see only Environments in Simulate; the other
  // Simulate pages are not registered for them, so their URLs reach the 404.
  const restrictSimulateToEnvironments = Boolean(
    user?.restrict_simulate_to_environments,
  );
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
      path: "integrations/webhooks/:endpointId",
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
          <WebhookEndpointPage />
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
  ];

  // Falcon AI Connectors, with the same roles as Integrations, where the
  // deployment allows Falcon AI ("allowed"), and while that answer is still
  // coming ("pending": the page's capability gate shows loading or retry).
  // Where it is not allowed the URL is no route, so the app's 404 answers.
  if (falconAI === "allowed" || falconAI === "pending") {
    settingsRoute.push({
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
    });
  }

  // Billing routes, role-gated:
  // - Billing is the one central billing page. It registers in every mode and
  //   for every role: on cloud it holds the platform plan, and wherever the
  //   gateway's managed credit ledger answers it holds the AI Gateway credits
  //   (the page sends anyone else to their profile). A role that cannot open
  //   it is told who can, on the same URL, and the page never loads.
  // - Plans & Pricing and the cloud license page are cloud only.
  const billingAllowedRoles = ["Owner", "Admin", "workspace_admin"];
  const canOpenBilling =
    isOwner || billingAllowedRoles.includes(effectiveWsRole);
  const hasBillingAccess = isCloud && canOpenBilling;
  // Where the old gateway billing URLs go: Settings > Billing on cloud (the
  // platform plan), or off cloud once gateway credits are shown; else the
  // gateway overview.
  const gatewayBillingTarget =
    isCloud || !isGatewayControlHidden("managedCredits")
      ? paths.dashboard.settings.billing
      : paths.dashboard.gateway.overview;

  settingsRoute.push({
    path: "billing",
    element: (
      <RoleProtection
        allowedRoles={isCloud ? billingAllowedRoles : ["Owner", "Admin"]}
        fallback={<BillingNoAccess />}
      >
        <BillingPageV2 />
      </RoleProtection>
    ),
  });

  if (hasBillingAccess) {
    settingsRoute.push(
      ...[
        {
          path: "ee-licenses",
          element: (
            <RoleProtection allowedRoles={billingAllowedRoles}>
              <LicensePage />
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

  // License management is available on self-hosted (EE) deployments too. The
  // nav shows Settings → License for org admins off-cloud, so the route must
  // register there or it 404s. (pricing above stays cloud-only.)
  const hasLicenseAccess =
    !isCloud && (isOwner || billingAllowedRoles.includes(effectiveWsRole));
  if (hasLicenseAccess) {
    settingsRoute.push({
      path: "ee-licenses",
      element: (
        <RoleProtection allowedRoles={billingAllowedRoles}>
          <LicensePage />
        </RoleProtection>
      ),
    });
  }

  // Settings → Sandboxes (Omega): workspace owners and admins narrow the
  // operator's sandbox policy. Registered only with VITE_OMEGA_CONSOLE=1.
  settingsRoute.push(...omegaSettingsRoutes());

  if (user === null || user?.ws_enabled) {
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
            ...(WorkspaceAuditLog
              ? [
                  {
                    path: "audit-log",
                    element: (
                      <WorkspaceRoleProtection
                        allowedRoles={["workspace_admin"]}
                      >
                        <WorkspaceAuditLog />
                      </WorkspaceRoleProtection>
                    ),
                  },
                ]
              : []),
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
              path: "integrations/:connectionId",
              element: (
                <WorkspaceRoleProtection
                  allowedRoles={["workspace_admin", "workspace_member"]}
                >
                  <IntegrationsWorkspaceScope>
                    <IntegrationDetailPage />
                  </IntegrationsWorkspaceScope>
                </WorkspaceRoleProtection>
              ),
            },
            {
              path: "integrations/webhooks/:endpointId",
              element: (
                <WorkspaceRoleProtection
                  allowedRoles={["workspace_admin", "workspace_member"]}
                >
                  <WebhookEndpointPage />
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
      element: <Navigate to="/dashboard/develop" replace />,
    },
    // dataset integration FE-0 removed the Runners page (connect / generated runners);
    // an old link lands on Datasets, not on a 404 (options-truth L5 G5).
    {
      path: "runners/*",
      element: <Navigate to="/dashboard/develop" replace />,
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
    {
      path: "models",
      children: [
        { element: <Models />, index: true },
        {
          path: ":id",
          element: (
            <DatasetContextProvider>
              <ModelDetail />
            </DatasetContextProvider>
          ),
          children: [
            { index: true, element: <ModelDetailDefaultRedirect /> },
            { path: "performance", element: <Performance /> },
            { path: "custom-metrics", element: <CustomMetric /> },
            {
              path: "datasets",
              children: [
                { index: true, element: <Datasets /> },
                {
                  path: ":dataset",
                  element: <DatasetDetail />,
                },
              ],
            },
            {
              path: "optimize",
              children: [
                { index: true, element: <OptimizeList /> },
                {
                  path: ":optimizeId",
                  element: <OptimizeDetail />,
                },
              ],
            },
            { path: "report", element: <PerformanceReport /> },
            { path: "config", element: <ModeConfig /> },
          ],
        },
      ],
    },

    {
      path: "falcon-ai/:conversationId?",
      element: <FalconAIPage />,
    },
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
    // {
    //   path: "prototype",
    //   element: <ProjectWrapper />,
    //   children: [
    //     {
    //       index: true,
    //       element: <ProjectList />,
    //     },
    //   ],
    // },
    // {
    //   path: "prototype/:projectId",
    //   children: [
    //     {
    //       index: true,
    //       element: <ProjectDetail />,
    //     },
    //     {
    //       path: ":runId",
    //       element: <RunInsidePage />,
    //     },
    //   ],
    // },
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
        // L7b absent-sweep (url:observe-voice): no voice page; the path lands
        // on the project's LLM tracing.
        {
          path: "voice",
          element: <Navigate to="../llm-tracing" replace />,
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
          // L7c g1 (url:owned-elsewhere): ?page= past the end opens the
          // last page with a notice.
          element: (
            <DatasetListPageGate>
              <Develop />
            </DatasetListPageGate>
          ),
        },
        // Synthetic data ships open on self-hosted; cloud plans enforce via
        // the backend capability check. Gate the routes too (not just the
        // AddDatasetDrawer tile) so a deep-link on a deployment/plan without
        // it shows the upgrade screen instead of a page that 402s on generate.
        {
          path: "create-synthetic-dataset",
          // the interface contract-8a: prototype builds generate from the world's generator.
          element: (
            <CapabilityGate feature={CAPABILITY.SYNTHETIC_DATA}>
              <CreateSyntheticData />
            </CapabilityGate>
          ),
        },
        {
          path: "edit-synthetic-dataset/:dataset",
          element: (
            <CapabilityGate feature={CAPABILITY.SYNTHETIC_DATA}>
              <EditSyntheticDataDrawer />
            </CapabilityGate>
          ),
        },

        // Declared before :dataset; the static second segment never collides
        // with its only child, preview/:id. The page renders the Data, Matrix
        // or Summary body from the last path segment.
        // R3: side by side, full width (no compare tabs).
        {
          path: ":dataset/compare/tasks",
          element: <TaskCompare />,
        },
        {
          path: ":dataset/compare",
          element: <DatasetCompare />,
          children: [
            { index: true, element: <CompareIndexRedirect /> },
            { path: "data", element: null },
            { path: "matrix", element: null },
            { path: "paths", element: null }, // R4-F frozen line
            { path: "summary", element: null },
          ],
        },
        // R4 (implementation): prototype-only lab (logos, lenses); not in the nav.
        ...(R4Lab ? [{ path: "r4-lab", element: <R4Lab /> }] : []),
        // the interface contract: leaderboards across datasets (static, before :dataset),
        // prototype builds only.
        ...(Leaderboards
          ? [
              { path: "leaderboards", element: <Leaderboards /> },
              {
                path: "leaderboards/:leaderboardId",
                element: <Leaderboards />,
              },
            ]
          : []),
        // AnnotateWorkspaceView reads the queue with useParams().queueId.
        {
          path: ":dataset/review/:queueId",
          element: <DatasetReview />,
        },
        {
          path: ":dataset",
          // L7c g1 (url:dataset): an unknown ?tab= opens Data with a notice.
          element: (
            <DatasetTabGate>
              <DevelopDetail />
            </DatasetTabGate>
          ),
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
              element: <ExperimentIndexRedirect />,
            },
            {
              path: "data",
              element: <ExperimentData />,
            },
            {
              path: "matrix",
              element: <ExperimentMatrix />,
            },
            { path: "paths", element: <ExperimentPaths /> }, // R4-F frozen line
            {
              path: "summary",
              element: <ExperimentSummary />,
            },
            { path: "traces", element: <ExperimentTraces /> },
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
    // dataset integration FE-0: the Runners page (machines joined with a live-connection
    // command that does not exist) is absent until that path is real.
    {
      path: "agents",
      children: [
        {
          element: <Agents />,
          children: [
            { index: true, element: <AgentsIndexRedirect /> },
            { path: "connected", element: <ConnectedAgentsTab /> },
            { path: "playground", element: <PlaygroundAgentsTab /> },
          ],
        },
        {
          path: "connected/:agentId",
          element: <AgentDetailLayout />,
          children: [
            { index: true, element: <AgentDetailIndexRedirect /> },
            { path: "overview", element: <AgentOverviewTab /> },
            { path: "definition", element: <AgentDefinitionTab /> },
            { path: "versions", element: <AgentVersionsTab /> },
            { path: "environments", element: <AgentEnvironmentsTab /> },
          ],
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
          element: <Navigate to="/dashboard/error-feed" replace />,
        },
        {
          path: ":id",
          element: <LegacyFeedDetailRedirect />,
        },
      ],
    },
    {
      path: "error-feed",
      children: [
        {
          index: true,
          element: (
            <CapabilityGate feature={CAPABILITY.ERROR_FEED}>
              <ErrorFeed />
            </CapabilityGate>
          ),
        },
        {
          path: "alerts",
          element: (
            <CapabilityGate feature={CAPABILITY.ERROR_FEED}>
              <ErrorFeedAlerts />
            </CapabilityGate>
          ),
        },
        {
          path: ":id",
          element: (
            <CapabilityGate feature={CAPABILITY.ERROR_FEED}>
              <ErrorFeedDetail />
            </CapabilityGate>
          ),
        },
      ],
    },
    {
      path: "huggingface",
      // the interface contract: prototype builds import from the world hub (repo@revision).
      element: <HuggingFacePage />,
    },
    {
      path: "simulate",
      children: [
        {
          index: true,
          element: (
            <Navigate to={paths.dashboard.simulate.environments.root} replace />
          ),
        },
        {
          path: "environments",
          element: <SimulateEnvironmentsHome />,
        },
        {
          path: "environments/templates",
          element: <SimulatePrebuiltEnvironments />,
        },
        {
          path: "environments/templates/:templateId",
          element: <SimulateUseTemplate />,
        },
        {
          // Build is no longer a page of its own — the source panels create the
          // job and route straight to the workspace, which hosts the build. An
          // old /build link lands back on the Build entry tab.
          path: "environments/build",
          element: (
            <Navigate
              to={`${paths.dashboard.simulate.environments.root}?tab=build`}
              replace
            />
          ),
        },
        {
          path: "environments/:envId",
          element: <SimulateEnvironmentWorkspace />,
          children: [
            {
              path: "runs/:testId/:executionId",
              element: <WorkspaceExecutionDetail />,
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
          ],
        },
        {
          // The harness pages were the first version of Environments.
          path: "harness",
          children: [
            {
              index: true,
              element: (
                <Navigate
                  to={paths.dashboard.simulate.environments.root}
                  replace
                />
              ),
            },
            {
              path: "new",
              element: (
                <Navigate
                  to={`${paths.dashboard.simulate.environments.root}?tab=build`}
                  replace
                />
              ),
            },
            { path: ":jobId", element: <HarnessToEnvironment /> },
            {
              path: "*",
              element: (
                <Navigate
                  to={paths.dashboard.simulate.environments.root}
                  replace
                />
              ),
            },
          ],
        },
        {
          path: "simulator-agent",
          element: (
            <Navigate to={paths.dashboard.simulate.environments.root} replace />
          ),
        },
        // Agent Definition is retiring: its old list link opens Connected
        // agents. The agent and version pages under it still work.
        {
          path: "agent-definitions",
          element: <Navigate to={CONNECTED_AGENTS_PATH} replace />,
        },
        // Personas is a tab on Environments.
        {
          path: "personas",
          element: (
            <Navigate
              to={`${paths.dashboard.simulate.environments.root}?tab=personas`}
              replace
            />
          ),
        },
        ...(restrictSimulateToEnvironments
          ? []
          : [
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
            ]),
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
        // Billing lives in the central Settings > Billing page; the old
        // gateway billing URLs land there, query and hash kept, whether or
        // not the org has a gateway yet. Off cloud that page has nothing to
        // show while gateway credits are hidden, so they open the gateway
        // overview instead.
        {
          path: "billing",
          element: <GatewayRedirect to={gatewayBillingTarget} />,
        },
        {
          path: "billing/*",
          element: <GatewayRedirect to={gatewayBillingTarget} />,
        },
        // One playground: this URL opens the Prompts workbench playground.
        { path: "playground", element: <GatewayPlayground /> },
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
            { path: "integrations", element: <GatewayIntegrations /> },
            { path: "models", element: <GatewayModels /> },
            { path: "models/:tab", element: <GatewayModels /> },
            // A model page; futureagi.com/models deep-links here.
            { path: "models/:provider/:model", element: <GatewayModels /> },
            // Hidden: the Catalog where managed models are offered, else the
            // Overview (a deployment with none has no Catalog).
            {
              path: "capabilities",
              element: isGatewayPageHidden("capabilities") ? (
                <CapabilitiesRedirect />
              ) : (
                <GatewayCapabilities />
              ),
            },
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
            { path: "runs", element: gatewayPage("runs", <GatewayRuns />) },
            {
              path: "runs/:runId",
              element: gatewayPage("runs", <GatewayRunDetail />),
            },
            { path: "custom-properties", element: <GatewayCustomProperties /> },
            { path: "fallbacks", element: <GatewayFallbacks /> },
            {
              path: "policies",
              element: gatewayPage("policies", gatewayPoliciesPage),
            },
            {
              path: "policies/:tab",
              element: gatewayPage("policies", gatewayPoliciesPage),
            },
            { path: "mcp", element: gatewayPage("mcp", <GatewayMCP />) },
            {
              path: "mcp/:tab",
              element: gatewayPage("mcp", <GatewayMCP />),
            },
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
  ];

  // Everyone manages their own personal keys. The API scopes the list and
  // management of other people's keys to organization Owners and Admins.
  dashboardChildren.push({
    path: "keys",
    element: <DevKeysPage />,
  });

  // Studio, Deployments, Sandboxes and the /dashboard/squad redirect; empty
  // unless VITE_OMEGA_CONSOLE=1.
  dashboardChildren.push(...omegaDashboardRoutes());

  return [
    {
      path: "dashboard",
      element: <DashboardRoutes />,
      children: dashboardChildren,
    },
  ];
};
