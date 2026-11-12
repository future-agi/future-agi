// The code-panels walk harness (walk.mjs drives it): one real Sandboxes or
// Deployments page in a real browser, with the page tests' fake Omega clients
// and the app's own theme in light or dark, so the Code drawer and "Show as
// code" are checked for layout at 1280 and 390 in Chromium, WebKit and
// Firefox without a stack. ?scene=<name>&mode=light|dark&parts=<released parts>
// &role=<Owner|Admin|Member> (the signed-in person's role; Owner by default,
// Member for a "-member" scene).
import React from "react";
import { createRoot } from "react-dom/client";
import merge from "lodash/merge";
import CssBaseline from "@mui/material/CssBaseline";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { AuthContext } from "src/auth/context/jwt/auth-context";
import { CAPABILITIES_QUERY_KEY } from "src/hooks/useCapabilities";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";
import { customShadows } from "src/theme/custom-shadows";
import { componentsOverrides } from "src/theme/overrides";
import { palette } from "src/theme/palette";
import { shadows } from "src/theme/shadows";
import { typography } from "src/theme/typography";
import { SS_KEY_ORG_ID, SS_KEY_WORKSPACE_ID } from "src/utils/sessionKeys";

import DeploymentDetailPage from "../../../deployments/DeploymentDetailPage";
import DeploymentNewPage from "../../../deployments/DeploymentNewPage";
import DeploymentSecretsPage from "../../../deployments/DeploymentSecretsPage";
import DeploymentsListPage from "../../../deployments/DeploymentsListPage";
import { DeploymentsApiProvider } from "../../../deployments/api/DeploymentsApiContext";
import {
  ID as DEPLOYMENT_ID,
  detailView,
  fakeApi as fakeDeployments,
} from "../../../deployments/__tests__/helpers";
import SandboxDetailPage from "../../../sandboxes/SandboxDetailPage";
import SandboxesListPage from "../../../sandboxes/SandboxesListPage";
import { SandboxesApiProvider } from "../../../sandboxes/api/SandboxesApiContext";
import SandboxSettingsPage from "../../../sandboxes/settings/SandboxSettingsPage";
import {
  CAPS_V2,
  PROVIDER,
  fakeApi as fakeSandboxes,
  sandboxView,
  settingsView,
} from "../../../sandboxes/__tests__/helpers";

const params = new URLSearchParams(window.location.search);
const mode = params.get("mode") === "dark" ? "dark" : "light";
const parts = (params.get("parts") || "").split(",").filter(Boolean);
const sceneName = params.get("scene") || "sandboxes-list";
// The person signed in: a held key is theirs alone (keyStore.js).
const auth = {
  authenticated: true,
  user: { id: "44444444-4444-4444-8444-444444444444" },
  role:
    params.get("role") || (sceneName.endsWith("-member") ? "Member" : "Owner"),
};

sessionStorage.setItem(SS_KEY_ORG_ID, "22222222-2222-4222-8222-222222222222");
sessionStorage.setItem(
  SS_KEY_WORKSPACE_ID,
  "33333333-3333-4333-8333-333333333333",
);

const sandboxes = fakeSandboxes({
  list: async () => ({
    sandboxes: [
      sandboxView(),
      sandboxView({
        id: "sbx_1123456789abcdef01234567",
        name: "nightly-etl",
        state: "hibernated",
      }),
    ],
    caps: CAPS_V2,
    provider: PROVIDER,
  }),
  settings: async () => settingsView({ canEdit: true }),
});
const deployments = fakeDeployments({
  get: async () => detailView(),
  credentials: async () => [
    {
      id: "cred-1",
      name: "OPENROUTER_API_KEY",
      kind: "api_key",
      status: "active",
      bindingCount: 2,
    },
  ],
});

const SCENES = {
  "sandboxes-list": [
    "sandboxes",
    "/dashboard/sandboxes",
    <SandboxesListPage key="p" />,
  ],
  "sandbox-create": [
    "sandboxes",
    "/dashboard/sandboxes",
    <SandboxesListPage key="p" />,
  ],
  "sandbox-detail": [
    "sandboxes",
    `/dashboard/sandboxes/${sandboxView().id}/run`,
    <SandboxDetailPage key="p" />,
  ],
  // The same sandbox for a workspace member, whom EE shows only the sandboxes
  // they created, so every call the page offers is theirs to make.
  "sandbox-detail-member": [
    "sandboxes",
    `/dashboard/sandboxes/${sandboxView().id}/run`,
    <SandboxDetailPage key="p" />,
  ],
  "sandbox-settings": [
    "sandboxes",
    "/dashboard/settings/sandboxes",
    <SandboxSettingsPage key="p" />,
  ],
  "deployments-list": [
    "deployments",
    "/dashboard/deployments",
    <DeploymentsListPage key="p" />,
  ],
  "deployment-detail": [
    "deployments",
    `/dashboard/deployments/${DEPLOYMENT_ID}/revisions`,
    <DeploymentDetailPage key="p" />,
  ],
  "deployment-new": [
    "deployments",
    "/dashboard/deployments/new",
    <DeploymentNewPage key="p" />,
  ],
  // The same pages for a workspace member, whom EE refuses every write.
  "deployment-detail-member": [
    "deployments",
    `/dashboard/deployments/${DEPLOYMENT_ID}/revisions`,
    <DeploymentDetailPage key="p" />,
  ],
  "deployment-new-member": [
    "deployments",
    "/dashboard/deployments/new",
    <DeploymentNewPage key="p" />,
  ],
  "deployment-secrets": [
    "deployments",
    "/dashboard/deployments/secrets",
    <DeploymentSecretsPage key="p" />,
  ],
};
const [tab, route, element] = SCENES[sceneName] || SCENES["sandboxes-list"];

const theme = createTheme({
  palette: palette(mode),
  customShadows: customShadows(mode),
  shadows: shadows(mode),
  shape: { borderRadius: 8 },
  typography,
});
theme.components = merge(componentsOverrides(theme), {});
document.body.setAttribute("data-theme", mode);

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false, refetchInterval: false, staleTime: Infinity },
    mutations: { retry: false },
  },
});
queryClient.setQueryData(CAPABILITIES_QUERY_KEY, {
  data: capabilitiesAnswer({ parts }),
});

const routes =
  tab === "sandboxes" ? (
    <Routes>
      <Route path="/dashboard/sandboxes" element={element} />
      <Route path="/dashboard/sandboxes/:id/:tab?" element={element} />
      <Route path="/dashboard/settings/sandboxes" element={element} />
    </Routes>
  ) : (
    <Routes>
      <Route path="/dashboard/deployments" element={element} />
      <Route path="/dashboard/deployments/new" element={element} />
      <Route path="/dashboard/deployments/secrets" element={element} />
      <Route path="/dashboard/deployments/:id/:tab?" element={element} />
    </Routes>
  );

createRoot(document.getElementById("root")).render(
  <AuthContext.Provider value={auth}>
    <HelmetProvider>
      <QueryClientProvider client={queryClient}>
        <ThemeProvider theme={theme}>
          <CssBaseline />
          <SnackbarProvider>
            <SandboxesApiProvider api={sandboxes}>
              <DeploymentsApiProvider api={deployments}>
                <MemoryRouter initialEntries={[route]}>
                  <main
                    data-testid="walk-root"
                    data-mode={mode}
                    style={{ padding: 16 }}
                  >
                    {routes}
                  </main>
                </MemoryRouter>
              </DeploymentsApiProvider>
            </SandboxesApiProvider>
          </SnackbarProvider>
        </ThemeProvider>
      </QueryClientProvider>
    </HelmetProvider>
  </AuthContext.Provider>,
);
