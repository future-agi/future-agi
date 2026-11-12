import React, { StrictMode } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  resetIntake,
  runIntake,
  stashKey,
  visit,
} from "../__tests__/auth-url-fixtures";
import AuthGuard from "./auth-guard";
import { requestWebSocketUrl } from "src/components/websocket/use-socket";
import genericSocketSource from "src/components/websocket/use-socket.jsx?raw";
import simulationSocketSource from "src/hooks/use-simulation-socket.js?raw";
import promptSocketSource from "src/sections/workbench/createPrompt/hooks/usePromptStreamUrl.jsx?raw";
import { usePromptStreamUrl } from "src/sections/workbench/createPrompt/hooks/usePromptStreamUrl";
import falconSocketSource from "src/sections/falcon-ai/hooks/useFalconSocket.js?raw";
import clusterSocketSource from "src/pages/dashboard/error-feed/clusterAnalyzeSocket.js?raw";
import WorkbenchProvider from "src/sections/workbench/createPrompt/WorkbenchProvider";
import EvaluationData from "src/sections/workbench/createPrompt/Evaluation/EvaluationData/EvaluationData";
import { OriginTypes } from "src/sections/common/DevelopCellRenderer/CellRenderers/cellRendererHelper";

// CR-07: a sign-in handoff arrives as a
// one-time code that is redeemed by POST; the session comes only from that
// response body, and legacy token params in the URL are dropped unread.

const REDEEM_URL = "https://api.handoff.test/accounts/handoff/redeem/";
// synthetic test value
const SYNTHETIC_CODE = "synthetic-handoff-code-not-a-credential";
// synthetic test value
const SYNTHETIC_ACCESS = "synthetic-access-not-a-credential";
// synthetic test value
const SYNTHETIC_REFRESH = "synthetic-refresh-not-a-credential";
// synthetic test value
const SYNTHETIC_OLD_ACCESS = "synthetic-old-access-not-a-credential";
// synthetic test value
const SYNTHETIC_LEGACY = "synthetic-legacy-url-token-not-a-credential";
// synthetic test value
const SYNTHETIC_PROVIDER_CODE = "synthetic-provider-code-not-a-credential";

// W10-AUTH-ORG-U02: what the person sees when a sign-in code can't be redeemed.
const HANDOFF_FAILED_TITLE = "We couldn't finish signing you in";
const HANDOFF_REFUSED_BODY =
  "This sign-in attempt timed out or was already used. Sign in again to continue.";
const HANDOFF_UNREACHABLE_BODY =
  "We couldn't reach FutureAGI to finish signing you in. Check your connection and sign in again.";

const LEGACY_QUERY = [
  "sso_token",
  "access_token",
  "refresh_token",
  "access",
  "refresh",
]
  .map((key) => `${key}=${SYNTHETIC_LEGACY}`)
  .join("&");

const mocks = vi.hoisted(() => ({
  initialize: vi.fn(),
  logout: vi.fn(),
  replace: vi.fn(),
  post: vi.fn(),
  authenticated: false,
  user: null,
  axios: { defaults: { headers: { common: {} } }, get: vi.fn(), post: vi.fn() },
  mutations: [],
  runs: [],
  evaluationData: undefined,
  gridApi: null,
  gridProps: null,
  deletableRows: false,
  versions: [{ version: "V1" }, { version: "V2" }],
  invalidateQueries: vi.fn(),
  notify: vi.fn(),
}));
vi.mock("axios", () => ({ default: { post: mocks.post } }));
// use-socket.jsx -> src/utils/logger reads CURRENT_ENVIRONMENT at import time.
vi.mock("src/config-global", () => ({
  HOST_API: "https://api.handoff.test",
  CURRENT_ENVIRONMENT: "local",
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    loading: false,
    authenticated: mocks.authenticated,
    method: "jwt",
    initialize: mocks.initialize,
    logout: mocks.logout,
    user: mocks.user,
  }),
}));
vi.mock("src/routes/hooks", () => ({
  useRouter: () => ({ replace: mocks.replace }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ isReady: false }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  usePostLoginPath: () => "/dashboard/develop",
}));
vi.mock("src/components/loading-screen", () => ({
  SplashScreen: () => (
    <div role="status" aria-label="Loading">
      Loading
    </div>
  ),
}));
vi.mock("src/utils/googleAds", () => ({ trackSignupConversion: vi.fn() }));
vi.mock("src/utils/redditAds", () => ({ trackRedditSignup: vi.fn() }));
vi.mock("src/utils/twitterAds", () => ({ trackTwitterSignup: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: mocks.axios,
  endpoints: { develop: { runPrompt: {
    getPrompt: () => "/FAKE-prompt",
    compareVersions: () => "/FAKE-compare",
    runTemplatePrompt: () => "/FAKE-run",
    getStatus: () => "/FAKE-status",
    getEvaluationData: () => "/FAKE-evaluations",
  } } },
}));
// Keep the real callers, ticket hook, ticket POST builder and socket helper.
// Stub only surrounding queries/UI; invoke the actual registered mutations.
vi.mock("@tanstack/react-query", () => ({
  useQuery: ({ queryKey }) => ({
    data: queryKey[0] === "evaluations-workbench" ? mocks.evaluationData : undefined,
    isPending: false, isLoading: false,
  }),
  useQueryClient: () => ({ invalidateQueries: mocks.invalidateQueries }),
  useMutation: (options) => {
    mocks.mutations.push(options);
    return {
      mutate: vi.fn((payload) => {
        const pending = options.mutationFn(payload);
        mocks.runs.push(pending);
        pending.catch(() => {});
      }),
      mutateAsync: vi.fn(), isPending: false,
    };
  },
}));
vi.mock("react-router", () => ({
  useParams: () => ({ id: "FAKE-template" }),
  useLocation: () => ({ state: null, search: "" }),
  useNavigate: () => mocks.replace,
}));
vi.mock("src/routes/hooks/use-url-state", () => ({
  useUrlState: (key, fallback) => [key === "selected-versions" ? mocks.versions : fallback, vi.fn()],
}));
vi.mock("src/hooks/useRestrictSimulateToEnvironments", () => ({ useRestrictSimulateToEnvironments: () => false }));
vi.mock("src/hooks/use-debounced-function", () => ({ useDebouncedFunction: (fn) => fn }));
vi.mock("src/components/snackbar", () => ({ useSnackbar: () => ({ enqueueSnackbar: mocks.notify }) }));
vi.mock("notistack", () => ({ enqueueSnackbar: (...args) => mocks.notify(...args) }));
vi.mock("src/sections/workbench/createPrompt/WorkbenchContext", async () => {
  const { createContext } = await import("react");
  return { PromptWorkbenchContext: createContext(null), modelConfigDefault: {} };
});
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemePrompt: () => ({ withParams: () => ({}) }) }));
vi.mock("ag-grid-react", async () => {
  const { forwardRef, useImperativeHandle } = await import("react");
  return {
    AgGridReact: forwardRef((props, ref) => {
      mocks.gridProps = props;
      useImperativeHandle(ref, () => ({ api: mocks.gridApi }));
      return null;
    }),
  };
});
vi.mock("src/sections/workbench/createPrompt/Evaluation/context/WorkbenchEvaluationContext", () => ({
  useWorkbenchEvaluationContext: () => ({
    showPrompts: true, versions: ["V1", "V2"], showVariables: true,
    setVariables: vi.fn(), setShowVariables: vi.fn(),
  }),
}));
vi.mock("src/sections/workbench/createPrompt/Evaluation/common", () => ({
  getColumnConfig: (config) => config, calculateRowHeight: () => 40,
  CELL_STATE: { EMPTY: "", LOADING: "Loading" },
  isUnsavedRow: () => mocks.deletableRows, mergeUnsavedRows: (next) => next,
}));
vi.mock("src/sections/common/EvaluationDrawer/getEvalsList", () => ({ useRunEvalMutation: () => ({ mutate: vi.fn() }) }));
vi.mock("src/sections/workbench/createPrompt/Evaluation/EvaluationSkeleton", () => ({ default: () => null }));
vi.mock("src/components/VariableDrawer/AddRowStatusPanel", () => ({ default: () => null }));
vi.mock("src/sections/workbench/createPrompt/Evaluation/EvaluationData/DeleteRowCellRenderer", () => ({ default: () => null }));
vi.mock("src/sections/develop-detail/Common/SingleImageViewer/SingleImageViewerProvider", () => ({ default: ({ children }) => children }));

function captureDocumentNavigation() {
  const browser = window;
  const destinations = [];
  const locationView = {
    get search() {
      return browser.location.search;
    },
    get pathname() {
      return browser.location.pathname;
    },
    get hash() {
      return browser.location.hash;
    },
    get href() {
      return browser.location.href;
    },
    set href(value) {
      destinations.push(value);
    },
  };
  vi.stubGlobal(
    "window",
    new Proxy(browser, {
      get(target, key) {
        return key === "location"
          ? locationView
          : Reflect.get(target, key, target);
      },
    }),
  );
  return destinations;
}

async function flush() {
  for (let turn = 0; turn < 5; turn += 1) {
    // eslint-disable-next-line no-await-in-loop
    await act(async () => {
      await Promise.resolve();
    });
  }
}

function renderGuard({ strict = true } = {}) {
  const tree = (
    <AuthGuard>
      <span>Protected</span>
    </AuthGuard>
  );
  return render(strict ? <StrictMode>{tree}</StrictMode> : tree);
}

function redeemed(access, refresh = null) {
  mocks.post.mockResolvedValue({ data: { access, refresh } });
}

function deferredResponse() {
  let resolve;
  let reject;
  const promise = new Promise((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  mocks.post.mockReturnValue(promise);
  return { resolve, reject };
}

function previousOrglessUser() {
  mocks.authenticated = true;
  mocks.user = {
    id: "FAKE-previous-user",
    email: "previous@example.test",
    requires_org_setup: true,
    onboarding_completed: false,
    organization_role: null,
  };
  localStorage.setItem("accessToken", SYNTHETIC_OLD_ACCESS);
}

function expectRecovery(view) {
  const alert = view.getByRole("alert");
  expect(
    within(alert).getByRole("heading", {
      level: 1,
      name: HANDOFF_FAILED_TITLE,
    }),
  ).toBeTruthy();
  expect(
    within(alert).getByRole("button", { name: "Sign in again" }),
  ).toBeTruthy();
  expect(view.queryByText("Protected")).toBeNull();
}

function loginReturnTo() {
  expect(mocks.replace).toHaveBeenCalled();
  const href = mocks.replace.mock.calls.at(-1)[0];
  return new URLSearchParams(href.split("?")[1]).get("returnTo");
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.post.mockReset();
  mocks.mutations = [];
  mocks.runs = [];
  mocks.evaluationData = undefined;
  mocks.gridApi = null;
  mocks.gridProps = null;
  mocks.deletableRows = false;
  mocks.axios.get.mockReset();
  mocks.axios.post.mockReset();
  resetIntake();
  vi.stubGlobal("localStorage", window.localStorage);
  vi.stubGlobal("sessionStorage", window.sessionStorage);
  localStorage.clear();
  sessionStorage.clear();
  mocks.authenticated = false;
  mocks.user = null;
  mocks.logout.mockReset();
  mocks.logout.mockResolvedValue(undefined);
  mocks.axios.defaults.headers.common = {};
  vi.stubGlobal("crypto", { randomUUID: () => "fake-generation" });
});
afterEach(async () => {
  cleanup();
  await act(async () => {});
  vi.unstubAllGlobals();
  vi.useRealTimers();
  resetIntake();
});

it("removes legacy token params encoded or duplicated, keeping every other byte", async () => {
  visit(
    `/dashboard/develop?%73so_token=${SYNTHETIC_LEGACY}&sso_token=&keep=a%20b&flag&refresh+=x#stay`,
  );
  const state = window.history.state;
  captureDocumentNavigation();

  renderGuard({ strict: false });
  await flush();

  expect(window.location.search).toBe("?keep=a%20b&flag&refresh+=x");
  expect(window.location.hash).toBe("#stay");
  expect(window.history.state).toEqual(state);
  expect(localStorage.getItem("accessToken")).toBeNull();
});

describe("SSO handoff code", () => {
  it("is redeemed by one POST and the session comes from the response body only", async () => {
    visit(
      `/dashboard/develop?code=${SYNTHETIC_CODE}&kind=sso&is_new_user=true&keep=1`,
    );
    redeemed(SYNTHETIC_ACCESS);
    const destinations = captureDocumentNavigation();

    renderGuard();

    // The code leaves the address bar before the redeem settles.
    expect(window.location.search).toBe("?is_new_user=true&keep=1");
    expect(localStorage.getItem("accessToken")).toBeNull();
    await flush();
    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(mocks.post).toHaveBeenCalledWith(
      REDEEM_URL,
      { code: SYNTHETIC_CODE, kind: "sso" },
      expect.objectContaining({
        headers: expect.objectContaining({
          "Content-Type": "application/json",
        }),
      }),
    );
    expect(localStorage.getItem("accessToken")).toBe(SYNTHETIC_ACCESS);
    expect(localStorage.getItem("refreshToken")).toBeNull();
    expect(localStorage.getItem("isNewOAuthSignup")).toBe("1");
    expect(destinations).toEqual(["/dashboard/develop"]);
    expect(mocks.replace).not.toHaveBeenCalled();
  });

  it("keeps a stored returnTo, stores a body refresh token and redeems once across rechecks", async () => {
    visit(`/dashboard/develop?code=${SYNTHETIC_CODE}&kind=sso&is_new_user=false`);
    localStorage.setItem("redirectUrl", "/shared/fake-return");
    redeemed(SYNTHETIC_ACCESS, SYNTHETIC_REFRESH);
    const destinations = captureDocumentNavigation();

    const view = renderGuard();
    await flush();
    view.rerender(
      <StrictMode>
        <AuthGuard>
          <span>Protected again</span>
        </AuthGuard>
      </StrictMode>,
    );
    await flush();

    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem("accessToken")).toBe(SYNTHETIC_ACCESS);
    expect(localStorage.getItem("refreshToken")).toBe(SYNTHETIC_REFRESH);
    expect(localStorage.getItem("isNewOAuthSignup")).toBeNull();
    expect(localStorage.getItem("redirectUrl")).toBeNull();
    expect(localStorage.getItem("initial-render")).toBe("done");
    expect(destinations).toEqual(["/shared/fake-return"]);
  });

  it("ignores legacy token params that arrive beside a code", async () => {
    visit(`/dashboard/develop?code=${SYNTHETIC_CODE}&kind=sso&${LEGACY_QUERY}`);
    redeemed(SYNTHETIC_ACCESS);
    captureDocumentNavigation();

    renderGuard();
    await flush();

    expect(localStorage.getItem("accessToken")).toBe(SYNTHETIC_ACCESS);
    expect(localStorage.getItem("refreshToken")).toBeNull();
    expect(window.location.search).toBe("");
  });

  it.each(["replayed", "expired", "unknown"])(
    "offers sign-in recovery for a %s code without storing a session",
    async () => {
      visit(`/dashboard/develop?code=${SYNTHETIC_CODE}&kind=sso&keep=1`);
      mocks.post.mockRejectedValue(
        Object.assign(new Error("refused"), {
          response: { status: 400, data: { error: "handoff_refused" } },
        }),
      );
      const destinations = captureDocumentNavigation();

      const view = renderGuard();
      await flush();

      expect(mocks.post).toHaveBeenCalledTimes(1);
      expect(localStorage.getItem("accessToken")).toBeNull();
      expect(destinations).toEqual([]);
      expect(mocks.replace).not.toHaveBeenCalled();
      expectRecovery(view);
      fireEvent.click(view.getByRole("button", { name: "Sign in again" }));
      await flush();
      expect(mocks.logout).toHaveBeenCalledTimes(1);
      expect(destinations).toEqual(["/auth/jwt/login"]);
    },
  );

  it("treats a response without an access token as refused", async () => {
    visit(`/dashboard/develop?code=${SYNTHETIC_CODE}&kind=sso`);
    mocks.post.mockResolvedValue({ data: { refresh: SYNTHETIC_REFRESH } });
    const destinations = captureDocumentNavigation();

    const view = renderGuard({ strict: false });
    await flush();

    expect(localStorage.getItem("accessToken")).toBeNull();
    expect(localStorage.getItem("refreshToken")).toBeNull();
    expect(destinations).toEqual([]);
    expect(mocks.replace).not.toHaveBeenCalled();
    expectRecovery(view);
  });

  it("is not redeemed over an existing session but still leaves the address bar", async () => {
    mocks.authenticated = true;
    visit(`/dashboard/develop?code=${SYNTHETIC_CODE}&kind=sso&keep=1`);

    const view = renderGuard();
    await flush();

    expect(mocks.post).not.toHaveBeenCalled();
    expect(window.location.search).toBe("?keep=1");
    expect(view.getByText("Protected")).toBeTruthy();
  });

  it.each([
    ["no kind", `code=${SYNTHETIC_PROVIDER_CODE}&state=fake-state`],
    ["the SOS kind", `code=${SYNTHETIC_PROVIDER_CODE}&kind=sos`],
  ])(
    "leaves a code with %s to its own page",
    async (_label, query) => {
      mocks.authenticated = true;
      visit(`/dashboard/integrations?${query}`);

      renderGuard();
      await flush();

      expect(mocks.post).not.toHaveBeenCalled();
      expect(window.location.search).toBe(`?${query}`);
    },
  );
});

describe.each(["URL fallback", "stash"])(
  "legacy token params using %s",
  (source) => {
    it("are never stored as a session and never ride into returnTo", async () => {
      visit(`/dashboard/develop?${LEGACY_QUERY}&keep=1`);
      if (source === "stash") {
        runIntake();
        expect(window[stashKey]).toBeDefined();
      }
      const destinations = captureDocumentNavigation();

      renderGuard({ strict: false });
      await flush();

      expect(mocks.post).not.toHaveBeenCalled();
      expect(localStorage.getItem("accessToken")).toBeNull();
      expect(localStorage.getItem("refreshToken")).toBeNull();
      expect(destinations).toEqual([]);
      expect(window.location.search).toBe("?keep=1");
      for (const [href] of mocks.replace.mock.calls) {
        expect(href).not.toContain(SYNTHETIC_LEGACY);
      }
      expect(loginReturnTo()).toBe("/dashboard/develop?keep=1");
    });
  },
);

describe("SAML handoff code", () => {
  it("replaces an existing session with the redeemed one and keeps a safe next", async () => {
    mocks.authenticated = true;
    visit(
      `/dashboard/develop?code=${SYNTHETIC_CODE}&kind=saml&next=%2Fdashboard%2Fruns`,
    );
    localStorage.setItem("accessToken", SYNTHETIC_OLD_ACCESS);
    localStorage.setItem("refreshToken", SYNTHETIC_REFRESH);
    localStorage.setItem("sosMode", "true");
    localStorage.setItem("redirectUrl", "/old");
    sessionStorage.setItem("organizationId", "fake-old-org");
    redeemed(SYNTHETIC_ACCESS);
    const destinations = captureDocumentNavigation();

    renderGuard();
    expect(window.location.search).toBe("?next=%2Fdashboard%2Fruns");
    await flush();

    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(mocks.post.mock.calls[0].slice(0, 2)).toEqual([
      REDEEM_URL,
      { code: SYNTHETIC_CODE, kind: "saml" },
    ]);
    expect(localStorage.getItem("accessToken")).toBe(SYNTHETIC_ACCESS);
    expect(localStorage.getItem("refreshToken")).toBeNull();
    expect(localStorage.getItem("sosMode")).toBeNull();
    expect(localStorage.getItem("redirectUrl")).toBeNull();
    expect(sessionStorage.getItem("organizationId")).toBeNull();
    expect(sessionStorage.getItem("fai_saml_next")).toBe("/dashboard/runs");
    expect(localStorage.getItem("fai_auth_generation")).toBe("fake-generation");
    expect(sessionStorage.getItem("fai_auth_generation")).toBe(
      "fake-generation",
    );
    expect(destinations).toEqual(["/dashboard/develop"]);
    expect(mocks.replace).not.toHaveBeenCalled();
  });

  it("drops an unsafe next", async () => {
    visit(
      `/dashboard/develop?code=${SYNTHETIC_CODE}&kind=saml&next=%2F%2Fevil.example`,
    );
    redeemed(SYNTHETIC_ACCESS);
    captureDocumentNavigation();

    renderGuard();
    await flush();

    expect(localStorage.getItem("accessToken")).toBe(SYNTHETIC_ACCESS);
    expect(sessionStorage.getItem("fai_saml_next")).toBeNull();
  });

  it.each(["replayed", "expired", "unknown"])(
    "surfaces a %s code over the old account and offers sign-in again",
    async () => {
      previousOrglessUser();
      visit(`/dashboard/develop?code=${SYNTHETIC_CODE}&kind=saml`);
      mocks.post.mockRejectedValue(
        Object.assign(new Error("refused"), {
          response: { status: 400, data: { error: "handoff_refused" } },
        }),
      );
      const destinations = captureDocumentNavigation();

      const view = renderGuard();
      await flush();

      expect(localStorage.getItem("accessToken")).toBe(SYNTHETIC_OLD_ACCESS);
      expect(destinations).toEqual([]);
      expect(mocks.replace).not.toHaveBeenCalled();
      expectRecovery(view);
      fireEvent.click(view.getByRole("button", { name: "Sign in again" }));
      await flush();
      expect(mocks.logout).toHaveBeenCalledTimes(1);
      expect(destinations).toEqual(["/auth/jwt/login"]);
    },
  );

  it("blocks old-user routing while deferred SAML redemption keeps its next", async () => {
    previousOrglessUser();
    visit(`/dashboard/develop?code=${SYNTHETIC_CODE}&kind=saml&next=%2Fdashboard%2Fruns`);
    const pending = deferredResponse();
    const destinations = captureDocumentNavigation();

    const view = renderGuard();
    await flush();
    expect(mocks.replace).not.toHaveBeenCalled();
    expect(localStorage.getItem("accessToken")).toBe(SYNTHETIC_OLD_ACCESS);
    expect(view.queryByText("Protected")).toBeNull();
    // A recheck must not run either old-session routing effect or redeem twice.
    mocks.user = { ...mocks.user };
    view.rerender(<StrictMode><AuthGuard>Protected</AuthGuard></StrictMode>);
    await flush();
    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(mocks.replace).not.toHaveBeenCalled();

    pending.resolve({ data: { access: SYNTHETIC_ACCESS, refresh: null } });
    await flush();
    expect(localStorage.getItem("accessToken")).toBe(SYNTHETIC_ACCESS);
    expect(sessionStorage.getItem("fai_saml_next")).toBe("/dashboard/runs");
    expect(destinations).toEqual(["/dashboard/develop"]);
    expect(mocks.replace).not.toHaveBeenCalled();
  });

  it.each(["unmounted", "path changed", "session superseded", "new handoff"])(
    "does not commit a response after its reader is %s",
    async (reason) => {
      previousOrglessUser();
      visit(`/dashboard/develop?code=${SYNTHETIC_CODE}&kind=saml&next=%2Fdashboard%2Fruns`);
      const pending = deferredResponse();
      const destinations = captureDocumentNavigation();
      const view = renderGuard();
      await flush();
      if (reason === "unmounted") view.unmount();
      if (reason === "path changed") visit("/dashboard/other");
      if (reason === "session superseded") {
        localStorage.setItem("fai_auth_generation", "FAKE-new-generation");
      }
      if (reason === "new handoff") {
        visit(`/dashboard/develop?code=${SYNTHETIC_PROVIDER_CODE}&kind=saml`);
        view.rerender(<StrictMode><AuthGuard>Protected</AuthGuard></StrictMode>);
        await flush();
        expect(window.location.search).toContain(SYNTHETIC_PROVIDER_CODE);
      }
      pending.resolve({ data: { access: SYNTHETIC_ACCESS, refresh: null } });
      await flush();

      expect(localStorage.getItem("accessToken")).toBe(SYNTHETIC_OLD_ACCESS);
      expect(sessionStorage.getItem("fai_saml_next")).toBeNull();
      expect(destinations).toEqual([]);
      expect(mocks.replace).not.toHaveBeenCalled();
      if (reason !== "unmounted") {
        expectRecovery(view);
        fireEvent.click(view.getByRole("button", { name: "Sign in again" }));
        await flush();
        expect(destinations).toEqual(["/auth/jwt/login"]);
      }
    },
  );

  it("still reaches sign-in if remote logout fails after clearing the old session", async () => {
    previousOrglessUser();
    visit(`/dashboard/develop?code=${SYNTHETIC_CODE}&kind=saml`);
    mocks.post.mockRejectedValue(new Error("refused"));
    mocks.logout.mockRejectedValue(new Error("logout unavailable"));
    const destinations = captureDocumentNavigation();
    const view = renderGuard();
    await flush();
    fireEvent.click(view.getByRole("button", { name: "Sign in again" }));
    await flush();
    expect(destinations).toEqual(["/auth/jwt/login"]);
  });

  it("lands on the stored next once the new session has loaded", () => {
    mocks.authenticated = true;
    sessionStorage.setItem("fai_saml_next", "/dashboard/runs");
    visit("/dashboard/develop");

    renderGuard();

    expect(mocks.replace).toHaveBeenCalledWith("/dashboard/runs");
    expect(sessionStorage.getItem("fai_saml_next")).toBeNull();
  });
});

it("redeems marketplace login over an old session and preserves new_org", async () => {
  previousOrglessUser();
  visit(`/dashboard/develop?code=${SYNTHETIC_CODE}&kind=aws_marketplace&new_org=True`);
  redeemed(SYNTHETIC_ACCESS, SYNTHETIC_REFRESH);
  const destinations = captureDocumentNavigation();
  const view = renderGuard();
  await flush();
  expect(mocks.post.mock.calls[0][1]).toEqual({ code: SYNTHETIC_CODE, kind: "aws_marketplace" });
  expect(view.queryByText("Protected")).toBeNull();
  expect(mocks.replace).not.toHaveBeenCalled();
  expect(localStorage.getItem("accessToken")).toBe(SYNTHETIC_ACCESS);
  expect(localStorage.getItem("refreshToken")).toBe(SYNTHETIC_REFRESH);
  expect(destinations).toEqual(["/dashboard/develop?new_org=True"]);
});

// W10-AUTH-ORG-U02: the redeem wait is a splash screen, not a blank page, and a
// failed redeem is a styled sign-in screen that names what happened. An OAuth
// or SAML sign-in is not a "link", and a dropped connection has not "expired".
describe("sign-in handoff screens", () => {
  it.each(["sso", "saml", "aws_marketplace"])(
    "shows the splash screen, not a blank page, while a %s code is redeemed",
    async (kind) => {
      visit(`/dashboard/develop?code=${SYNTHETIC_CODE}&kind=${kind}`);
      deferredResponse();
      const destinations = captureDocumentNavigation();

      const view = renderGuard();
      await flush();

      expect(mocks.post).toHaveBeenCalledTimes(1);
      expect(view.getByRole("status", { name: "Loading" })).toBeTruthy();
      expect(view.queryByRole("alert")).toBeNull();
      expect(view.queryByText("Protected")).toBeNull();
      expect(destinations).toEqual([]);
    },
  );

  it.each([
    [
      "a refused code",
      () =>
        mocks.post.mockRejectedValue(
          Object.assign(new Error("refused"), {
            response: { status: 400, data: { error: "handoff_refused" } },
          }),
        ),
      HANDOFF_REFUSED_BODY,
    ],
    [
      "a reply without a session",
      () => mocks.post.mockResolvedValue({ data: { refresh: SYNTHETIC_REFRESH } }),
      HANDOFF_REFUSED_BODY,
    ],
    [
      "a dropped connection",
      () =>
        mocks.post.mockRejectedValue(
          Object.assign(new Error("Network Error"), { code: "ERR_NETWORK" }),
        ),
      HANDOFF_UNREACHABLE_BODY,
    ],
    [
      "a server error",
      () =>
        mocks.post.mockRejectedValue(
          Object.assign(new Error("Request failed with status code 502"), {
            response: { status: 502, data: "Bad Gateway" },
          }),
        ),
      HANDOFF_UNREACHABLE_BODY,
    ],
  ])(
    "explains %s on a styled sign-in screen, not as an expired link",
    async (_label, respond, body) => {
      visit(`/dashboard/develop?code=${SYNTHETIC_CODE}&kind=sso`);
      respond();
      const destinations = captureDocumentNavigation();

      const view = renderGuard();
      await flush();

      const alert = view.getByRole("alert");
      expect(
        within(alert).getByRole("heading", {
          level: 1,
          name: HANDOFF_FAILED_TITLE,
        }),
      ).toBeTruthy();
      expect(within(alert).getByText(body)).toBeTruthy();
      expect(alert.textContent).not.toMatch(/link|expired/i);
      const button = within(alert).getByRole("button", {
        name: "Sign in again",
      });
      expect(button.disabled).toBe(false);
      // The design-system primary button, not a bare browser button.
      expect(button.className).toContain("MuiButton-contained");
      expect(view.queryByRole("status")).toBeNull();
      expect(view.queryByText("Protected")).toBeNull();
      expect(localStorage.getItem("accessToken")).toBeNull();
      expect(destinations).toEqual([]);
    },
  );
});

it("fetches a fresh socket ticket by header POST for every connect", async () => {
  // # synthetic test value
  const ticket = `FAKE${"0".repeat(39)}`;
  const fetchTicket = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ticket }) });
  vi.stubGlobal("fetch", fetchTicket);
  const first = await requestWebSocketUrl(SYNTHETIC_ACCESS, "/ws/falcon-ai/", { workspace_id: "FAKE-workspace" });
  const second = await requestWebSocketUrl(SYNTHETIC_ACCESS, "/ws/falcon-ai/", { workspace_id: "FAKE-workspace" });
  expect(first).toBe(`wss://api.handoff.test/ws/falcon-ai/?ticket=${ticket}`);
  expect(second).not.toContain(SYNTHETIC_ACCESS);
  expect(fetchTicket).toHaveBeenCalledTimes(2);
  const [url, options] = fetchTicket.mock.calls[0];
  expect(url).toBe("https://api.handoff.test/accounts/handoff/ws-ticket/");
  expect(options.method).toBe("POST");
  expect(options.headers.Authorization).toBe(`Bearer ${SYNTHETIC_ACCESS}`);
  expect(options.cache).toBe("no-store");
  expect(JSON.parse(options.body)).toEqual({ path: "/ws/falcon-ai/", query: { workspace_id: "FAKE-workspace" } });
  expect(options.body).not.toContain(SYNTHETIC_ACCESS);
  expect([...new URL(first).searchParams.keys()]).toEqual(["ticket"]);
});

function promptConnectionHarness() {
  mocks.user = { accessToken: "FAKE-prompt-access" };
  const minted = new Map();
  const spent = new Set();
  const sockets = [];
  const mint = vi.fn().mockImplementation(async () => {
    // # synthetic test value
    const ticket = `FAKE${String(minted.size).padStart(39, "0")}`;
    minted.set(ticket, Date.now());
    return { ok: true, json: async () => ({ ticket }) };
  });
  vi.stubGlobal("fetch", mint);
  class Socket {
    constructor(url) {
      const parsed = new URL(url);
      const ticket = parsed.searchParams.get("ticket");
      if (!minted.has(ticket) || spent.has(ticket) || Date.now() - minted.get(ticket) >= 60000) {
        throw new Error("Spent, expired or missing prompt ticket");
      }
      expect([...parsed.searchParams.keys()]).toEqual(["ticket"]);
      expect(url).not.toContain(mocks.user.accessToken);
      spent.add(ticket);
      this.url = url;
      this.close = vi.fn();
      this.send = vi.fn();
      sockets.push(this);
    }
  }
  vi.stubGlobal("WebSocket", Socket);
  return { mint, sockets };
}

function promptMutation(marker) {
  const mutation = mocks.mutations.find(({ mutationFn }) => mutationFn.toString().includes(marker));
  expect(mutation).toBeTruthy();
  return mutation.mutationFn;
}

async function completePromptSocket(socket) {
  await act(async () => {
    socket.onopen();
    socket.onmessage({ data: JSON.stringify({ type: "run_prompt", streaming_status: "all_completed" }) });
  });
}

it("returns a lazy factory and mints independently for simultaneous calls without an attempt argument", async () => {
  const { mint } = promptConnectionHarness();
  const hook = renderHook(() => usePromptStreamUrl());
  await flush();
  expect(mint).not.toHaveBeenCalled();
  const firstFactory = hook.result.current;
  const urls = await Promise.all([firstFactory(), firstFactory()]);
  expect(new Set(urls).size).toBe(2);
  hook.rerender();
  expect(hook.result.current).toBe(firstFactory);
  expect(await hook.result.current()).not.toBe(urls[0]);
  expect(mint).toHaveBeenCalledTimes(3);
});

it("rejects a mint that resolves after the prompt hook unmounts", async () => {
  const { mint } = promptConnectionHarness();
  let finish;
  mint.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const hook = renderHook(() => usePromptStreamUrl());
  const pending = hook.result.current();
  const refused = expect(pending).rejects.toMatchObject({ name: "AbortError" });
  hook.unmount();
  // # synthetic test value
  const ticket = `FAKE${"0".repeat(39)}`;
  finish({ ok: true, json: async () => ({ ticket }) });
  await refused;
});

it("does not reuse a ticket after mint rejection and rejects an explicitly cancelled attempt", async () => {
  const { mint } = promptConnectionHarness();
  const hook = renderHook(() => usePromptStreamUrl());
  mint.mockRejectedValueOnce(new Error("Ticket service unavailable"));
  await expect(hook.result.current()).rejects.toThrow("Ticket service unavailable");
  expect(await hook.result.current()).toContain("?ticket=FAKE");
  const cancelled = new AbortController();
  cancelled.abort();
  await expect(hook.result.current(cancelled.signal)).rejects.toMatchObject({ name: "AbortError" });
  expect(mint).toHaveBeenCalledTimes(2);
});

it("a real single-run unmount during minting opens no late socket or HTTP fallback", async () => {
  const { mint, sockets } = promptConnectionHarness();
  const mintResponse = mint.getMockImplementation();
  let finish;
  mint.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const view = render(<WorkbenchProvider><span>Workbench</span></WorkbenchProvider>);
  const pending = promptMutation("getRunTemplatePayload(index)")({ index: 0 });
  const cancelled = expect(pending).rejects.toMatchObject({ name: "AbortError" });
  view.unmount();
  finish(await mintResponse());
  await cancelled;
  expect(sockets).toHaveLength(0);
  expect(mocks.axios.post).not.toHaveBeenCalled();
});

it("the real compare caller mints once per version, including a first run after TTL and a rerun", async () => {
  vi.useFakeTimers();
  const { mint, sockets } = promptConnectionHarness();
  render(<WorkbenchProvider><span>Workbench</span></WorkbenchProvider>);
  await flush();
  expect(mint).not.toHaveBeenCalled();
  await act(async () => { vi.advanceTimersByTime(61000); });
  const compare = promptMutation("comparePayload");
  const first = compare();
  await flush();
  expect(sockets).toHaveLength(2);
  expect(mint).toHaveBeenCalledTimes(2);
  await completePromptSocket(sockets[0]);
  await completePromptSocket(sockets[1]);
  expect((await first).successful).toHaveLength(2);
  const second = compare();
  await flush();
  expect(sockets).toHaveLength(4);
  expect(new Set(sockets.map((socket) => socket.url)).size).toBe(4);
  await completePromptSocket(sockets[2]);
  await completePromptSocket(sockets[3]);
  expect((await second).successful).toHaveLength(2);
  expect(mint).toHaveBeenCalledTimes(4);
});

it("the real single-run caller waits for a ticket and does not fall back just because minting is pending", async () => {
  const { mint, sockets } = promptConnectionHarness();
  const mintResponse = mint.getMockImplementation();
  let finish;
  mint.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  render(<WorkbenchProvider><span>Workbench</span></WorkbenchProvider>);
  const run = promptMutation("getRunTemplatePayload(index)");
  const first = run({ index: 0 });
  await flush();
  expect(sockets).toHaveLength(0);
  expect(mocks.axios.post).not.toHaveBeenCalled();
  finish(await mintResponse());
  await flush();
  expect(sockets).toHaveLength(1);
  await completePromptSocket(sockets[0]);
  await first;
  const second = run({ index: 0 });
  await flush();
  expect(sockets).toHaveLength(2);
  expect(sockets[1].url).not.toBe(sockets[0].url);
  await completePromptSocket(sockets[1]);
  await second;
});

it("the real evaluation caller gets fresh tickets for parallel rows and reconnect after close", async () => {
  vi.useFakeTimers();
  const { mint, sockets } = promptConnectionHarness();
  render(<EvaluationData />);
  await flush();
  expect(mint).not.toHaveBeenCalled();
  await act(async () => { vi.advanceTimersByTime(61000); });
  const run = promptMutation("wirePayload");
  const first = run({ _runRowId: "0", version: "V1" });
  const closed = expect(first).rejects.toThrow("Stream connection closed unexpectedly");
  const parallel = run({ _runRowId: "1", version: "V2" });
  await flush();
  expect(sockets).toHaveLength(2);
  expect(mint).toHaveBeenCalledTimes(2);
  sockets[0].onclose({ code: 1006 });
  await closed;
  await completePromptSocket(sockets[1]);
  await parallel;
  const reconnected = run({ _runRowId: "0", version: "V1" });
  await flush();
  expect(sockets).toHaveLength(3);
  expect(new Set(sockets.map((socket) => socket.url)).size).toBe(3);
  await completePromptSocket(sockets[2]);
  await reconnected;
  for (const socket of sockets.slice(1)) {
    expect(JSON.parse(socket.send.mock.calls[0][0])).not.toHaveProperty("_runRowId");
  }
});

function renderEvaluationGrid() {
  mocks.evaluationData = {
    variables: null,
    V1: { output: ["", ""], eval_names: [], eval_output: {}, eval_status: {} },
    V2: { output: ["", ""], eval_names: [], eval_output: {}, eval_status: {} },
  };
  const rows = ["0", "1"].map((id) => {
    const row = { data: { id, "Output-V1": "", "Output-V2": "" } };
    row.setDataValue = vi.fn((field, value) => { row.data[field] = value; });
    return row;
  });
  mocks.gridApi = {
    stopEditing: vi.fn(),
    getDisplayedRowAtIndex: (index) => rows[index],
    getDisplayedRowCount: () => rows.length,
  };
  const view = render(<EvaluationData />);
  const start = (version, index = 0) => {
    act(() => {
      const column = mocks.gridProps.columnDefs.find(({ id }) => id === `Output-${version}`);
      column.handleClick(OriginTypes.RUN_PROMPT, index, version);
    });
    return mocks.runs.at(-1);
  };
  return { view, rows, start };
}

async function evaluationMessage(socket, version, status, chunk = "", index = 0) {
  await act(async () => {
    socket.onmessage({ data: JSON.stringify({
      type: "run_prompt", version, result_index: index,
      streaming_status: status, chunk,
    }) });
  });
}

async function finishEvaluation(socket, version, output, index = 0) {
  socket.onopen();
  await evaluationMessage(socket, version, "started", "", index);
  await evaluationMessage(socket, version, "running", output, index);
  await evaluationMessage(socket, version, "all_completed", "", index);
}

it("EvaluationData completes both compared cells on one row when their ticket mints resolve in reverse order", async () => {
  const { mint, sockets } = promptConnectionHarness();
  const mintResponse = mint.getMockImplementation();
  let finishV1;
  let finishV2;
  mint.mockImplementationOnce(() => new Promise((resolve) => { finishV1 = resolve; }));
  mint.mockImplementationOnce(() => new Promise((resolve) => { finishV2 = resolve; }));
  const { rows, start } = renderEvaluationGrid();
  const first = start("V1");
  const second = start("V2");
  await flush();
  expect(mint).toHaveBeenCalledTimes(2);
  expect(sockets).toHaveLength(0);
  finishV2(await mintResponse());
  await flush();
  finishV1(await mintResponse());
  await flush();
  expect(sockets).toHaveLength(2);
  expect(new Set(sockets.map(({ url }) => url)).size).toBe(2);
  expect(sockets[0].close).not.toHaveBeenCalled();
  await finishEvaluation(sockets[0], "V2", "second output");
  await expect(second).resolves.toMatchObject({ version: "V2" });
  expect(sockets[1].close).not.toHaveBeenCalled();
  await finishEvaluation(sockets[1], "V1", "first output");
  await expect(first).resolves.toMatchObject({ version: "V1" });
  expect(rows[0].data).toMatchObject({ "Output-V1": "first output", "Output-V2": "second output" });
  for (const socket of sockets) {
    expect(socket.close).toHaveBeenCalledTimes(1);
    expect(JSON.parse(socket.send.mock.calls[0][0])).not.toHaveProperty("_runRowId");
  }
});

it("EvaluationData keeps V1 streaming when V2 starts and completes on the same row", async () => {
  const { sockets } = promptConnectionHarness();
  const { rows, start } = renderEvaluationGrid();
  const first = start("V1");
  await flush();
  sockets[0].onopen();
  await evaluationMessage(sockets[0], "V1", "started");
  await evaluationMessage(sockets[0], "V1", "running", "first ");
  const second = start("V2");
  await flush();
  expect(sockets).toHaveLength(2);
  expect(sockets[0].url).not.toBe(sockets[1].url);
  expect(sockets[0].close).not.toHaveBeenCalled();
  await finishEvaluation(sockets[1], "V2", "second output");
  await second;
  expect(sockets[0].close).not.toHaveBeenCalled();
  await evaluationMessage(sockets[0], "V1", "running", "output");
  await evaluationMessage(sockets[0], "V1", "all_completed");
  await first;
  expect(rows[0].data).toMatchObject({ "Output-V1": "first output", "Output-V2": "second output" });
  expect(sockets[0].close).toHaveBeenCalledTimes(1);
});

it("EvaluationData retires only a same-cell retry and ignores that old connection's late messages and cleanup", async () => {
  const { sockets } = promptConnectionHarness();
  const { rows, start } = renderEvaluationGrid();
  const first = start("V1");
  const peer = start("V2");
  await flush();
  const retired = expect(first).rejects.toMatchObject({ name: "AbortError" });
  const retry = start("V1");
  await flush();
  await retired;
  expect(sockets).toHaveLength(3);
  expect(new Set(sockets.map(({ url }) => url)).size).toBe(3);
  expect(sockets[0].close).toHaveBeenCalledTimes(1);
  expect(sockets[1].close).not.toHaveBeenCalled();
  await evaluationMessage(sockets[0], "V1", "running", "stale output");
  await evaluationMessage(sockets[0], "V1", "all_completed");
  sockets[0].onclose({ code: 1006 });
  expect(rows[0].data["Output-V1"]).toBe("");
  expect(sockets[2].close).not.toHaveBeenCalled();
  await finishEvaluation(sockets[2], "V1", "retry output");
  await retry;
  expect(sockets[1].close).not.toHaveBeenCalled();
  await finishEvaluation(sockets[1], "V2", "peer output");
  await peer;
  expect(rows[0].data).toMatchObject({ "Output-V1": "retry output", "Output-V2": "peer output" });
});

it("EvaluationData cancels a superseded same-cell mint, not another version's pending mint", async () => {
  const { mint, sockets } = promptConnectionHarness();
  const mintResponse = mint.getMockImplementation();
  const finish = [];
  mint.mockImplementation(() => new Promise((resolve) => { finish.push(resolve); }));
  const { start } = renderEvaluationGrid();
  const first = start("V1");
  const peer = start("V2");
  const retry = start("V1");
  await flush();
  const cancelled = expect(first).rejects.toMatchObject({ name: "AbortError" });
  for (const resolve of finish) resolve(await mintResponse());
  await cancelled;
  await flush();
  expect(sockets).toHaveLength(2);
  expect(sockets[0].url).not.toBe(sockets[1].url);
  await finishEvaluation(sockets[0], "V2", "peer output");
  await finishEvaluation(sockets[1], "V1", "retry output");
  await Promise.all([peer, retry]);
});

it("EvaluationData errors close only the failed version and leave its peer tracked through unmount", async () => {
  const { sockets } = promptConnectionHarness();
  const { view, start } = renderEvaluationGrid();
  const failed = start("V1");
  const peer = start("V2");
  await flush();
  const rejected = expect(failed).rejects.toBeTruthy();
  sockets[0].onerror(new Error("FAKE stream error"));
  await rejected;
  expect(sockets[0].close).toHaveBeenCalled();
  expect(sockets[1].close).not.toHaveBeenCalled();
  const cancelled = expect(peer).rejects.toMatchObject({ name: "AbortError" });
  view.unmount();
  await cancelled;
  expect(sockets[1].close).toHaveBeenCalledTimes(1);
});

describe.each(["minting", "streaming"])("EvaluationData cleanup while %s", (phase) => {
  it.each(["row deletion", "unmount"])("%s cancels every version, with no late socket or output", async (action) => {
    const { mint, sockets } = promptConnectionHarness();
    const mintResponse = mint.getMockImplementation();
    const finish = [];
    if (phase === "minting") {
      mint.mockImplementation(() => new Promise((resolve) => { finish.push(resolve); }));
    }
    mocks.deletableRows = true;
    const { view, rows, start } = renderEvaluationGrid();
    const first = start("V1");
    const second = start("V2");
    const otherRow = start("V1", 1);
    await flush();
    const cancelledFirst = expect(first).rejects.toMatchObject({ name: "AbortError" });
    const cancelledSecond = expect(second).rejects.toMatchObject({ name: "AbortError" });
    const cancelledOther = action === "unmount"
      ? expect(otherRow).rejects.toMatchObject({ name: "AbortError" }) : null;
    if (action === "unmount") {
      view.unmount();
    } else {
      act(() => mocks.gridProps.columnDefs.find(({ colId }) => colId === "rowActions")
        .cellRendererParams.onDelete("0"));
    }
    for (const resolve of finish) resolve(await mintResponse());
    await Promise.all([cancelledFirst, cancelledSecond, cancelledOther]);
    await flush();
    if (phase === "minting") {
      expect(sockets).toHaveLength(action === "unmount" ? 0 : 1);
    } else {
      expect(sockets[0].close).toHaveBeenCalledTimes(1);
      expect(sockets[1].close).toHaveBeenCalledTimes(1);
      await evaluationMessage(sockets[0], "V1", "running", "stale");
      await evaluationMessage(sockets[1], "V2", "running", "stale");
    }
    expect(rows[0].setDataValue).not.toHaveBeenCalled();
    if (action === "row deletion") {
      const remaining = sockets.at(-1);
      expect(remaining.close).not.toHaveBeenCalled();
      await finishEvaluation(remaining, "V1", "other row output", 1);
      await otherRow;
      expect(rows[1].data["Output-V1"]).toBe("other row output");
    } else if (phase === "streaming") {
      expect(sockets[2].close).toHaveBeenCalledTimes(1);
    }
  });
});

it.each([{}, { ticket: "invalid ticket" }])("fails closed on a malformed socket-ticket body", async (body) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => body }));
  await expect(requestWebSocketUrl(SYNTHETIC_ACCESS, "/ws/connect/")).rejects.toThrow();
});

it.each([
  ["generic", genericSocketSource], ["simulation", simulationSocketSource],
  ["prompt", promptSocketSource], ["falcon", falconSocketSource], ["cluster", clusterSocketSource],
])("%s builder uses ticket intake, never a bearer URL", (_name, source) => {
  expect(source).toContain("requestWebSocketUrl");
  expect(source).not.toMatch(/[?&](?:token|access_token|sso_token)=/);
  expect(source).not.toMatch(/URLSearchParams\(\{\s*token/);
});
