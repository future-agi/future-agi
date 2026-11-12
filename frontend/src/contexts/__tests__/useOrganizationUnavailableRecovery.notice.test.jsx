import React, { useContext } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createTheme, ThemeProvider } from "@mui/material/styles";
import axiosPackage from "axios";
import client, { endpoints } from "src/utils/axios";
import { AuthContext } from "src/auth/context/jwt/auth-context";
import { AuthProvider } from "src/auth/context/jwt/auth-provider";
import { SnackbarProvider } from "src/components/snackbar";
import { OrganizationProvider, useOrganization } from "../OrganizationContext";

// Notistack's CJS entry adds these exports only when its provider mounts.
// Vite's export-star helper enumerates keys when the app barrel first loads,
// before that mount. Expose live getters so it can re-export both methods and
// each remount still targets the real current provider, not a captured instance.
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

// Keep both the application snackbar wrapper and Notistack real. Only unrelated
// services and decorative/settings dependencies are stubbed; no enqueue spy.
vi.mock("src/config-global", () => ({ HOST_API: "https://offline.invalid" }));
vi.mock("src/api/contracts/openapi-contract", () => ({
  assertContractedRequestConfig: (config) => config,
  assertContractedResponse: (response) => response,
  findOpenApiEndpoint: vi.fn(),
}));
vi.mock("@tanstack/react-query", () => {
  const queryClient = { clear: vi.fn() };
  return { useQueryClient: () => queryClient };
});
vi.mock("src/utils/Mixpanel", () => ({ identifyUser: vi.fn(), resetUser: vi.fn() }));
vi.mock("src/utils/PostHog", () => ({
  identifyPostHogUser: vi.fn(), resetPostHogUser: vi.fn(),
}));
vi.mock("@sentry/react", () => ({ setUser: vi.fn() }));
vi.mock("src/utils/logger", () => ({
  default: { debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));
vi.mock("src/utils/recaptchaService", () => ({ getRecaptchaToken: vi.fn() }));
vi.mock("src/sections/falcon-ai/store/useFalconStore", () => ({
  default: { getState: () => ({ resetAll: vi.fn() }) },
}));
vi.mock("src/sections/omega/code/keyStore", () => ({ forgetKey: vi.fn() }));
vi.mock("src/components/settings", () => ({
  useSettingsContext: () => ({ themeDirection: "ltr" }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

const theme = createTheme({ palette: { border: { default: "#dddddd" } } });
const notice = "You no longer have access to that organization. Switched to Accessible.";
const adapter = vi.fn();
const oldClientAdapter = client.defaults.adapter;
const oldPackageAdapter = axiosPackage.defaults.adapter;
const header = (config, key) => config.headers?.get?.(key) ?? config.headers?.[key];
const requestPath = (config) => new URL(config.url, "https://offline.invalid").pathname;
const callsTo = (url) => adapter.mock.calls.map(([config]) => config).filter((config) => requestPath(config) === url);
let auth;
let removed;
let releaseList;
let releaseBootstrap;

function userFor(id) {
  return {
    id: "user", email: "test@example.invalid",
    organization: { id, name: id === "stale" ? "Removed" : "Accessible" },
    organization_role: "Member", org_level: 3, default_workspace_id: null,
  };
}
function success(config, data) {
  return { status: 200, data, headers: {}, config };
}
function refusal(config) {
  return Promise.reject({
    config,
    response: {
      status: 403,
      data: JSON.stringify({ code: "organization_unavailable", detail: "Refused" }),
      headers: {}, config,
    },
  });
}
function seedStaleSelection() {
  sessionStorage.setItem("organizationId", "stale");
  sessionStorage.setItem("organizationName", "Removed");
  client.defaults.headers.common["X-Organization-Id"] = "stale";
}
function ObserveAuth() {
  auth = useContext(AuthContext);
  return null;
}
function TenantTree() {
  const organization = useOrganization();
  return <div data-testid="tenant-tree">{organization.currentOrganizationId}</div>;
}
function mount() {
  render(
    <AuthProvider>
      <ObserveAuth />
      <OrganizationProvider>
        <ThemeProvider theme={theme}>
          <SnackbarProvider autoHideDuration={null} transitionDuration={0}>
            <TenantTree />
          </SnackbarProvider>
        </ThemeProvider>
      </OrganizationProvider>
    </AuthProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  localStorage.clear();
  localStorage.setItem("accessToken", "test-session");
  localStorage.setItem("refreshToken", "test-refresh");
  seedStaleSelection();
  delete client.defaults.headers.common.Authorization;
  delete client.defaults.headers.common["X-Workspace-Id"];
  client.defaults.adapter = adapter;
  axiosPackage.defaults.adapter = adapter;
  auth = undefined;
  removed = false;
  releaseList = undefined;
  releaseBootstrap = undefined;
  adapter.mockReset().mockImplementation((config) => {
    if (requestPath(config) === endpoints.organizations.list) {
      return new Promise((resolve) => {
        releaseList = () => resolve(success(config, { result: { organizations: [
          { id: "landing", name: "Accessible", role: "Member", level: 3, is_selected: true },
        ] } }));
      });
    }
    if (requestPath(config) === endpoints.auth.me) {
      const id = header(config, "X-Organization-Id");
      if (removed && id === "stale") return refusal(config);
      if (id === "landing") {
        return new Promise((resolve) => {
          releaseBootstrap = () => resolve(success(config, userFor("landing")));
        });
      }
      return Promise.resolve(success(config, userFor(id)));
    }
    return refusal(config);
  });
});
afterEach(() => {
  cleanup();
  client.defaults.adapter = oldClientAdapter;
  axiosPackage.defaults.adapter = oldPackageAdapter;
  ["Authorization", "X-Organization-Id", "X-Workspace-Id"].forEach((key) => delete client.defaults.headers.common[key]);
  sessionStorage.clear();
  localStorage.clear();
});

describe("organization recovery notice after the real snackbar host remounts", () => {
  it.each(["authenticated coalesced refusals", "initial bootstrap", "sign-in"])(
    "shows one visible notice after delayed %s and never repeats it",
    async (entry) => {
      let recovery = Promise.resolve();
      if (entry === "initial bootstrap") {
        removed = true;
        mount();
      } else if (entry === "sign-in") {
        localStorage.removeItem("accessToken");
        mount();
        await waitFor(() => expect(auth.loading).toBe(false));
        seedStaleSelection();
        removed = true;
        act(() => {
          recovery = auth.login({ status: 200, data: { access: "new-session", refresh: "new-refresh" } });
        });
      } else {
        mount();
        await waitFor(() => expect(auth.authenticated).toBe(true));
        expect(screen.getByTestId("tenant-tree")).toHaveTextContent("stale");
        adapter.mockClear();
        removed = true;
        act(() => {
          recovery = Promise.all([1, 2].map(() =>
            client.post("/model-hub/datasets/create/", {}).catch((error) => error),
          ));
        });
      }

      // Let React commit the recovery gate while the list request is pending.
      // The real SnackbarProvider is in this removed subtree, just as in App.
      await waitFor(() => expect(releaseList).toBeTypeOf("function"));
      await waitFor(() => expect(screen.queryByTestId("tenant-tree")).not.toBeInTheDocument());
      expect(auth.recoveringOrganization).toBe(true);
      expect(screen.queryByText(notice)).not.toBeInTheDocument();
      await act(async () => { releaseList(); });

      // Selecting the successor alone must not emit the notice. Its bootstrap
      // is independently delayed, and the notification host is still absent.
      await waitFor(() => expect(releaseBootstrap).toBeTypeOf("function"));
      expect(screen.queryByTestId("tenant-tree")).not.toBeInTheDocument();
      expect(screen.queryByText(notice)).not.toBeInTheDocument();
      await act(async () => { releaseBootstrap(); await recovery; });
      await waitFor(() => expect(screen.getByTestId("tenant-tree")).toHaveTextContent("landing"));
      expect(auth.authenticated).toBe(true);
      expect(auth.recoveringOrganization).toBe(false);
      expect(await screen.findByText(notice)).toBeVisible();
      expect(screen.getAllByText(notice)).toHaveLength(1);
      expect(callsTo(endpoints.organizations.list)).toHaveLength(1);
      expect(callsTo(endpoints.auth.me).filter((config) => header(config, "X-Organization-Id") === "landing")).toHaveLength(1);
      if (entry === "authenticated coalesced refusals") {
        expect(adapter.mock.calls.filter(([config]) => config.method === "post")).toHaveLength(2);
      }

      await act(async () => { auth.updateUserData({ first_name: "Updated" }); });
      expect(screen.getAllByText(notice)).toHaveLength(1);
      fireEvent.click(screen.getByRole("button", { name: "Dismiss notification" }));
      await waitFor(() => expect(screen.queryByText(notice)).not.toBeInTheDocument());
      // Dedupe cannot mask a second emission after the first toast is gone.
      await act(async () => { auth.updateUserData({ first_name: "Updated again" }); });
      expect(screen.queryByText(notice)).not.toBeInTheDocument();
    },
  );
});
