import React from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { createTheme, ThemeProvider } from "@mui/material/styles";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
  useMutation,
} from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import client, { endpoints } from "src/utils/axios";
import { SnackbarProvider } from "src/components/snackbar";
import AddSimulatorAgentModal from "src/components/simulator-agent/AddSimulatorAgentModal";
import { handleError } from "src/utils/queryErrorHandler";
import { getSafeActionErrorMessage } from "src/utils/errorUtils";
import { WorkspaceProvider, writeSessionWorkspace } from "../WorkspaceContext";

// What a person sees after an admin removes them from the selected workspace:
// the screen that made the call must not add the refusal in red on top of the
// recovery's one calm line. Notistack and the app snackbar stay real (no
// enqueue spy); assertions read the rendered toasts.
//
// Notistack's CJS entry adds enqueueSnackbar/closeSnackbar only when its
// provider mounts, after the snackbar barrel's export-star ran. Expose live
// getters so every import reaches the mounted provider.
vi.mock("notistack", async () => {
  const actual = await vi.importActual("notistack");
  const live = actual.default ?? actual;
  return {
    ...actual,
    get enqueueSnackbar() {
      return live.enqueueSnackbar;
    },
    get closeSnackbar() {
      return live.closeSnackbar;
    },
  };
});

const h = vi.hoisted(() => ({
  auth: { authenticated: true, loading: false, user: null },
}));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => h.auth }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org" }),
}));
vi.mock("src/utils/Mixpanel", () => ({ resetUser: vi.fn() }));
vi.mock("src/utils/logger", () => ({
  default: { debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));
vi.mock("src/config-global", () => ({ HOST_API: "https://offline.invalid" }));
vi.mock("src/api/contracts/openapi-contract", () => ({
  assertContractedRequestConfig: (x) => x,
  assertContractedResponse: (x) => x,
  findOpenApiEndpoint: vi.fn(),
}));
vi.mock("src/auth/context/jwt/utils", () => ({
  addToQueue: vi.fn(),
  clearTokens: vi.fn(),
  getIsRefreshing: vi.fn(),
  getRefreshToken: vi.fn(),
  getRememberMe: vi.fn(),
  processQueue: vi.fn(),
  refreshTokenRequest: vi.fn(),
  setIsRefreshing: vi.fn(),
  setSession: vi.fn(),
}));
vi.mock("src/components/settings", () => ({
  useSettingsContext: () => ({ themeDirection: "ltr" }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

const theme = createTheme({ palette: { border: { default: "#dddddd" } } });
const refusal =
  "The selected workspace is unavailable. Select an accessible workspace and retry.";
const landed =
  "You no longer have access to that workspace. Switched to Landing.";
const adapter = vi.fn();
const oldAdapter = client.defaults.adapter;
let finishRead;
let failRead;

// The server sets the same refusal text in every message field.
const refuse = (config) =>
  Promise.reject({
    config,
    response: {
      status: 403,
      data: {
        code: "workspace_unavailable",
        message: refusal,
        detail: refusal,
        error: refusal,
        result: refusal,
      },
      headers: {},
      config,
    },
  });

beforeEach(() => {
  sessionStorage.clear();
  sessionStorage.setItem("organizationId", "org");
  h.auth.user = { organization: { id: "org" }, default_workspace_id: "stale" };
  writeSessionWorkspace({
    id: "stale",
    orgId: "org",
    role: "Workspace Admin",
    wsLevel: 15,
  });
  client.defaults.headers.common["X-Organization-Id"] = "org";
  client.defaults.adapter = adapter;
  finishRead = undefined;
  failRead = undefined;
  adapter.mockReset().mockImplementation((config) => {
    if (config.url === endpoints.auth.me) {
      return new Promise((resolve, reject) => {
        finishRead = () =>
          resolve({
            status: 200,
            data: {
              organization: { id: "org" },
              default_workspace_id: "landing",
              default_workspace_role: "Member",
              default_workspace_name: "Landing",
              ws_level: 3,
            },
            headers: {},
            config,
          });
        failRead = () =>
          reject({
            config,
            response: {
              status: 500,
              data: { code: "server_error" },
              headers: {},
              config,
            },
          });
      });
    }
    return refuse(config);
  });
});
afterEach(() => {
  cleanup();
  client.defaults.adapter = oldAdapter;
  delete client.defaults.headers.common["X-Workspace-Id"];
  delete client.defaults.headers.common["X-Organization-Id"];
});

function renderScreen(ui) {
  const queryClient = new QueryClient({
    // The app's global handler (app.jsx) for reads and writes.
    mutationCache: new MutationCache({ onError: handleError }),
    defaultOptions: { mutations: { retry: false } },
  });
  return render(
    <ThemeProvider theme={theme}>
      <SnackbarProvider>
        <QueryClientProvider client={queryClient}>
          <WorkspaceProvider>{ui}</WorkspaceProvider>
        </QueryClientProvider>
      </SnackbarProvider>
    </ThemeProvider>,
  );
}

async function createSimulatorAgent() {
  renderScreen(<AddSimulatorAgentModal open onClose={() => {}} />);
  for (const [label, value] of [
    ["Name", "Agent"],
    ["Prompt", "Be brief."],
    ["Voice Provider", "provider"],
    ["Voice Name", "voice"],
    ["Model", "model"],
  ]) {
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  }
  fireEvent.click(screen.getByRole("button", { name: "Create Agent" }));
  // The modal's own catch has run (it toasts error.message) once the button
  // is back from "Creating...".
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Create Agent" }),
    ).not.toBeDisabled(),
  );
}

function MutationSave() {
  const save = useMutation({
    mutationFn: () => client.post("/model-hub/datasets/create/", {}),
  });
  return <button onClick={() => save.mutate()}>Save with mutation</button>;
}

function DirectSave() {
  // A screen that imports notistack itself and toasts the safe action text.
  const onClick = () =>
    client
      .post("/model-hub/datasets/create/", {})
      .catch((error) =>
        enqueueSnackbar(
          getSafeActionErrorMessage(error, "Could not save the dataset."),
          { variant: "error" },
        ),
      );
  return <button onClick={onClick}>Save directly</button>;
}

describe("a workspace_unavailable refusal is explained once", () => {
  it("keeps one refusal error when the landing read itself fails", async () => {
    await createSimulatorAgent();
    await waitFor(() => expect(failRead).toBeTypeOf("function"));
    await act(async () => failRead());
    expect(await screen.findAllByText(refusal)).toHaveLength(1);
    expect(screen.queryByText(landed)).toBeNull();
  });

  it("a screen that toasts error.message shows only the landing line", async () => {
    await createSimulatorAgent();
    await waitFor(() => expect(finishRead).toBeTypeOf("function"));
    await act(async () => finishRead());
    expect(await screen.findByText(landed)).toBeInTheDocument();
    expect(screen.queryByText(refusal)).toBeNull();
    expect(screen.queryByText("Failed to create simulator agent")).toBeNull();
  });

  it("the react-query error handler does not add a red line", async () => {
    renderScreen(<MutationSave />);
    fireEvent.click(screen.getByText("Save with mutation"));
    await waitFor(() => expect(finishRead).toBeTypeOf("function"));
    // Let the mutation settle and its cache onError run before landing.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await act(async () => finishRead());
    expect(await screen.findByText(landed)).toBeInTheDocument();
    expect(screen.queryByText("Something went wrong")).toBeNull();
    expect(screen.queryByText(refusal)).toBeNull();
  });

  it("a direct notistack caller using the safe action text stays quiet", async () => {
    renderScreen(<DirectSave />);
    await act(async () => {
      fireEvent.click(screen.getByText("Save directly"));
    });
    await waitFor(() => expect(finishRead).toBeTypeOf("function"));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await act(async () => finishRead());
    expect(await screen.findByText(landed)).toBeInTheDocument();
    expect(screen.queryByText("Could not save the dataset.")).toBeNull();
    expect(screen.queryByText(refusal)).toBeNull();
  });

  // A recovery that ends without a line of its own (a workspace was chosen
  // while the landing was read) must not swallow the screen's own toast.
  it("keeps a direct caller's toast when a workspace is chosen during the read", async () => {
    renderScreen(<DirectSave />);
    await act(async () => {
      fireEvent.click(screen.getByText("Save directly"));
    });
    await waitFor(() => expect(finishRead).toBeTypeOf("function"));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    // Held while the recovery may still explain it.
    expect(screen.queryByText(refusal)).toBeNull();
    client.defaults.headers.common["X-Workspace-Id"] = "chosen";
    await act(async () => finishRead());
    expect(await screen.findByText(refusal)).toBeInTheDocument();
    expect(screen.getAllByText(refusal)).toHaveLength(1);
    expect(screen.queryByText(landed)).toBeNull();
    expect(client.defaults.headers.common["X-Workspace-Id"]).toBe("chosen");
  });

  it("keeps the react-query error toast when a workspace is chosen during the read", async () => {
    renderScreen(<MutationSave />);
    fireEvent.click(screen.getByText("Save with mutation"));
    await waitFor(() => expect(finishRead).toBeTypeOf("function"));
    // Let the mutation settle and its cache onError run before the landing.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    client.defaults.headers.common["X-Workspace-Id"] = "chosen";
    await act(async () => finishRead());
    expect(await screen.findByText("Something went wrong")).toBeInTheDocument();
    expect(screen.queryByText(landed)).toBeNull();
  });
});
