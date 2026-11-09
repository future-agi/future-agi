import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ModuleRegistry } from "ag-grid-community";
import { AllEnterpriseModule } from "ag-grid-enterprise";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import axios from "src/utils/axios";
import DevKeysView from "../../dev-keys-view";
import ApiKeysLandingPage from "src/pages/dashboard/settings/api-keys/ApiKeysLandingPage";
import NewObserve from "src/sections/project/NewProject/NewObserve";
import NewExperiment from "src/sections/project/NewProject/NewExperiment";
import UploadKnowledgeBaseSDK from "src/sections/knowledge-base/CreateKnowledgeBase/UploadKnowledgeBaseSDK";
import AddRowData from "src/sections/develop-detail/DataTab/AddRowData";
import AddSDKModal from "src/sections/develop/AddRowDrawer/AddSDKModal";
import CreateDatasetSDK from "src/sections/develop/AddDatasetDrawer/AddSDKModal";
import SimulationSDK from "src/sections/test/TestRuns/SDkComponentVoiceTestRun";
import GatewaySnippetKey from "src/sections/gateway/overview/GatewaySnippetKey";
import QuickstartCode from "src/sections/gateway/overview/QuickstartCode";
import { quickstartSnippets } from "src/sections/gateway/overview/quickstart";
import CodePanel from "src/sections/omega/code/CodePanel";
import ModelApi from "src/sections/gateway/models/detail/ModelApi";
import { SettingsProvider } from "src/components/settings";
import { configureLocalMonaco } from "src/utils/localMonaco";

configureLocalMonaco();
ModuleRegistry.registerModules([AllEnterpriseModule]);
axios.defaults.baseURL = window.location.origin;
sessionStorage.setItem(
  "organizationId",
  "22222222-2222-4222-8222-222222222222",
);
sessionStorage.setItem("workspaceId", "33333333-3333-4333-8333-333333333333");
const id = "11111111-1111-4111-8111-111111111111";
function DatasetCreate() {
  const [open, setOpen] = useState(true);
  return (
    <CreateDatasetSDK
      open={open}
      onClose={() => setOpen(false)}
      refreshGrid={() => {}}
    />
  );
}
const scene =
  new URLSearchParams(window.location.search).get("scene") || "keys";
const scenes = {
  keys: <DevKeysView />,
  settings: <ApiKeysLandingPage />,
  observe: <NewObserve />,
  experiment: <NewExperiment />,
  knowledge: (
    <UploadKnowledgeBaseSDK
      pythonCode={'api_key="YOUR_API_KEY"\nsecret_key="YOUR_SECRET_KEY"'}
    />
  ),
  dataset: <AddRowData dataset={id} />,
  rows: (
    <AddSDKModal
      open
      datasetId={id}
      onClose={() => {}}
      closeDrawer={() => {}}
    />
  ),
  "dataset-create": <DatasetCreate />,
  simulation: (
    <Routes>
      <Route
        path="/tests/:testId"
        element={<SimulationSDK agentType="chat" />}
      />
    </Routes>
  ),
  gateway: (
    <GatewaySnippetKey gatewayId={id}>
      {(secret) => (
        <QuickstartCode
          personalKey={secret}
          snippets={quickstartSnippets(
            window.location.origin + "/v1",
            "example-model",
          )}
        />
      )}
    </GatewaySnippetKey>
  ),
  "model-api": (
    <ModelApi
      gatewayId={id}
      baseUrl={`${window.location.origin}/v1`}
      managedOffered={false}
      row={{
        id: "example-model",
        type: "chat",
        access: "own",
        managedOn: false,
        ownRoutes: [
          {
            sendAs: "example-model",
            providerId: "example",
            providerName: "Example",
          },
        ],
      }}
    />
  ),
  omega: (
    <CodePanel
      requests={[
        {
          id: "list",
          title: "List sandboxes",
          method: "GET",
          path: "/workspace/sandboxes",
        },
      ]}
    />
  ),
};
const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
});
createRoot(document.getElementById("root")).render(
  <MemoryRouter initialEntries={[`/tests/${id}`]}>
    <SettingsProvider>
      <ThemeProvider
        theme={createTheme({ palette: palette("light"), typography })}
      >
        <SnackbarProvider>
          <QueryClientProvider client={queryClient}>
            <main style={{ height: "90vh", padding: 16 }}>{scenes[scene]}</main>
          </QueryClientProvider>
        </SnackbarProvider>
      </ThemeProvider>
    </SettingsProvider>
  </MemoryRouter>,
);
