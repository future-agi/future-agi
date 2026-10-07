import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  MemoryRouter,
  Outlet,
  useLocation,
  useNavigate,
} from "react-router-dom";

// TH-8084 R4: a stored return destination is one-shot. The persistent Router
// is mounted once per page load; after an auth consumer has used and removed
// `redirectUrl`, "/" must land on the default home again (Cloud included).
//
// Real: Router (routes/sections/index.jsx), AuthGuard, JwtLoginView,
// useDeploymentMode/usePostLoginPath and react-query. Mocked: the auth
// context, the HTTP transport, and leaf pages/telemetry.

const h = vi.hoisted(() => ({
  mode: "oss",
  auth: null,
  post: null,
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn((url) =>
      url === "/api/deployment-info/"
        ? Promise.resolve({ data: { result: { mode: h.mode } } })
        : Promise.reject(new Error(`unexpected GET ${url}`)),
    ),
    post: (...args) => h.post(...args),
  },
  endpoints: {
    settings: { v2: { deploymentInfo: "/api/deployment-info/" } },
    auth: { login: "/accounts/token/", service: (p) => `/auth/${p}/` },
    invite: { accept_invitation: () => "/invite/" },
  },
}));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => h.auth }));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: "Owner" }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    currentOrganizationName: "Acme",
    currentOrganizationDisplayName: "Acme",
    isReady: true,
  }),
}));
vi.mock("src/auth/context/jwt/utils", () => ({
  setSession: vi.fn(),
  setRefreshToken: vi.fn(),
}));
vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("src/utils/recaptchaService", () => ({
  getRecaptchaToken: vi.fn(async () => "recaptcha"),
}));
vi.mock("@simplewebauthn/browser", () => ({
  browserSupportsWebAuthn: () => false,
  startAuthentication: vi.fn(),
}));
vi.mock("src/sections/auth/jwt/RightSectionAuth", () => ({
  default: () => null,
}));
vi.mock("src/components/RegionSelect", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/pages/SOSLoginPage", () => ({ default: () => null }));
vi.mock("src/pages/shared/SharedView", () => ({ default: () => null }));
vi.mock("src/sections/oss-first-run/OssSetupView", () => ({
  default: () => <p>OSS setup</p>,
}));
vi.mock("src/pages/mcp/OAuthConsent", () => ({
  default: () => <p>MCP consent</p>,
}));

// Leaf route tables: the real AuthGuard and the real login view, with small
// pages in place of the product screens.
vi.mock("src/routes/sections/auth", async () => {
  const { default: JwtLoginView } = await import(
    "src/sections/auth/jwt/jwt-login-view"
  );
  return {
    authRoutes: [{ path: "/auth/jwt/login", element: <JwtLoginView /> }],
  };
});
vi.mock("src/routes/sections/dashboard", async () => {
  const { AuthGuard } = await import("src/auth/guard");
  const page = (name) => <p>{name}</p>;
  return {
    dashboardRoutes: () => [
      {
        path: "/dashboard",
        element: (
          <AuthGuard>
            <Outlet />
          </AuthGuard>
        ),
        children: [
          { path: "get-started", element: page("Get started") },
          { path: "falcon-ai", element: page("Falcon AI") },
          { path: "observe/:id", element: page("Observe project") },
        ],
      },
    ],
  };
});
vi.mock("src/routes/sections/main", () => ({ mainRoutes: [] }));

const { default: Router } = await import("../index");

const navigation = {};
function Probe() {
  navigation.navigate = useNavigate();
  const location = useLocation();
  navigation.location = location.pathname + location.search;
  return null;
}

function mount(initialEntry) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Router />
        <Probe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return view;
}

function signedIn() {
  return {
    loading: false,
    authenticated: true,
    method: "jwt",
    initialize: vi.fn(),
    login: vi.fn(),
    user: {
      id: "u1",
      email: "owner@example.com",
      organization_role: "Owner",
      onboarding_completed: true,
    },
  };
}

async function goHome() {
  await act(async () => navigation.navigate("/"));
}

const HOME = { oss: "/dashboard/get-started", cloud: "/dashboard/falcon-ai" };
const RESOURCE = "/dashboard/observe/proj-42";
const MCP = "/mcp/authorize?client_id=cli&state=s1";

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("oss_validation_done", "true");
  h.mode = "oss";
  h.auth = signedIn();
});

afterEach(() => {
  localStorage.clear();
});

describe("Router post-login landing is one-shot (R4)", () => {
  it.each(["oss", "cloud"])(
    "%s: after AuthGuard consumes a stored deep link, / lands on the default home",
    async (mode) => {
      h.mode = mode;
      // Page load with a destination stored by an earlier auth step.
      localStorage.setItem("redirectUrl", RESOURCE);
      mount("/");

      expect(await screen.findByText("Observe project")).toBeTruthy();
      expect(navigation.location).toBe(RESOURCE);
      expect(localStorage.getItem("redirectUrl")).toBeNull();
      expect(localStorage.getItem("initial-render")).toBe("done");

      await goHome();

      expect(
        await screen.findByText(mode === "cloud" ? "Falcon AI" : "Get started"),
      ).toBeTruthy();
      expect(navigation.location).toBe(HOME[mode]);
    },
  );

  it.each(["oss", "cloud"])(
    "%s: after login consumes a stored MCP destination, / lands on the default home",
    async (mode) => {
      h.mode = mode;
      // Returning from an abandoned social sign-in: the destination is
      // already stored when the app (and the Router) mounts.
      localStorage.setItem("redirectUrl", MCP);
      localStorage.setItem("initial-render", "done");
      h.auth = { ...signedIn(), authenticated: false, user: null };
      h.auth.login = vi.fn(async () => {
        h.auth = signedIn();
      });
      h.post = vi.fn(async () => ({
        status: 200,
        data: { access: "a", refresh: "r" },
      }));
      const view = mount(`/auth/jwt/login?returnTo=${encodeURIComponent(MCP)}`);

      const email = await view.findByRole("textbox", { name: /email/i });
      fireEvent.change(email, { target: { value: "owner@example.com" } });
      fireEvent.change(view.container.querySelector('input[name="password"]'), {
        target: { value: "Secret-passw0rd" },
      });
      fireEvent.click(view.getByRole("button", { name: "Continue" }));

      expect(await screen.findByText("MCP consent")).toBeTruthy();
      expect(h.post).toHaveBeenCalledTimes(1);
      expect(navigation.location).toBe(MCP);
      await waitFor(() =>
        expect(localStorage.getItem("redirectUrl")).toBeNull(),
      );

      await goHome();

      expect(
        await screen.findByText(mode === "cloud" ? "Falcon AI" : "Get started"),
      ).toBeTruthy();
      expect(navigation.location).toBe(HOME[mode]);
    },
  );

  it("still honours an unconsumed stored destination on /", async () => {
    // The explicit one-shot destination itself is preserved.
    localStorage.setItem("redirectUrl", RESOURCE);
    localStorage.setItem("initial-render", "done");
    mount("/");
    expect(await screen.findByText("Observe project")).toBeTruthy();
    expect(navigation.location).toBe(RESOURCE);
  });
});
