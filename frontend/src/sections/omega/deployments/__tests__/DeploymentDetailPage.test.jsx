import React from "react";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  peekOmegaConnect,
  resetOmegaConnectForTests,
  takeOmegaConnectToken,
} from "src/sections/omega/embed/connect-intake";
import { failure } from "src/sections/omega/shared/omegaClient";
import {
  OmegaError,
  describeBody,
  stateForStatus,
} from "src/sections/omega/shared/omegaStates";
import { OMEGA_PART } from "src/sections/omega/shell/rollout";

import {
  keepAccessToken,
  resetAccessTokensForTests,
} from "../lib/accessTokens";
import {
  ID,
  detailView,
  fakeApi,
  renderDeployments,
  revisionView,
} from "./helpers";
import { session, workspace } from "./session-mock";

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));
vi.mock("src/sections/omega/shared", async (importOriginal) => ({
  ...(await importOriginal()),
  useOmegaSession: () => session,
}));

const { default: DeploymentDetailPage } = await import(
  "../DeploymentDetailPage"
);
const { errorCopy } = await import("../components/DeploymentErrorState");
const { CreateError } = await import("../new/DeployStep");

const at = (tab) => `/dashboard/deployments/${ID}${tab ? `/${tab}` : ""}`;

// The error a page gets for an EE refusal, built the way the client builds it
// (omegaClient.js: describeBody, then failure), so the copy under test is the
// copy a customer reads.
const refusal = (status, body) => {
  const { code, message, reason, detail } = describeBody(body);
  return new OmegaError(
    failure({
      status,
      code,
      reason,
      detail,
      serverMessage: message,
      fallbackState: stateForStatus(status),
    }),
  );
};

beforeEach(() => {
  Object.assign(session, { state: "ready", code: null, message: null });
  resetAccessTokensForTests();
  resetOmegaConnectForTests();
});
const clipboardDescriptor = Object.getOwnPropertyDescriptor(
  navigator,
  "clipboard",
);
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  if (clipboardDescriptor) {
    Object.defineProperty(navigator, "clipboard", clipboardDescriptor);
  } else {
    Reflect.deleteProperty(navigator, "clipboard");
  }
});

describe("One-time access token copy (FA-DEP-UX-09)", () => {
  const token = "k".repeat(43);
  const copyError =
    "Couldn't copy. If the token is hidden, choose Show. Select the token and copy it by hand.";

  it.each(["missing clipboard", "insecure context", "rejected write"])(
    "shows an inline error and reveals selectable text for %s",
    async (failure) => {
      const writeText = vi.fn(async () => {
        if (failure === "rejected write") throw new Error("Permission denied");
      });
      vi.stubGlobal("isSecureContext", failure !== "insecure context");
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: failure === "missing clipboard" ? undefined : { writeText },
      });
      keepAccessToken({ id: ID, accessToken: token });
      renderDeployments(<DeploymentDetailPage />, { route: at() });
      const field = await screen.findByTestId("access-token");
      expect(field).not.toHaveTextContent(token);
      // fireEvent avoids user-event's clipboard stub hiding the missing API.
      fireEvent.click(
        within(field).getByRole("button", { name: "Copy access token" }),
      );
      expect(await within(field).findByText(copyError)).toBeVisible();
      expect(within(field).getByText(token)).toHaveStyle({ userSelect: "all" });
      expect(field.querySelector('[aria-live="polite"]')).toHaveTextContent(
        copyError,
      );
      if (failure !== "rejected write")
        expect(writeText).not.toHaveBeenCalled();
    },
  );

  it("announces Copied without revealing a masked token on success", async () => {
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("isSecureContext", true);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    keepAccessToken({ id: ID, accessToken: token });
    renderDeployments(<DeploymentDetailPage />, { route: at() });
    const field = await screen.findByTestId("access-token");
    fireEvent.click(
      within(field).getByRole("button", { name: "Copy access token" }),
    );
    await waitFor(() =>
      expect(field.querySelector('[aria-live="polite"]')).toHaveTextContent(
        "Copied",
      ),
    );
    expect(writeText).toHaveBeenCalledWith(token);
    expect(field).not.toHaveTextContent(token);
    expect(within(field).queryByText(copyError)).toBeNull();
  });

  it("clears the inline error when a later copy succeeds", async () => {
    const writeText = vi
      .fn()
      .mockRejectedValueOnce(new Error("Permission denied"))
      .mockResolvedValueOnce(undefined);
    vi.stubGlobal("isSecureContext", true);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    keepAccessToken({ id: ID, accessToken: token });
    renderDeployments(<DeploymentDetailPage />, { route: at() });
    const field = await screen.findByTestId("access-token");
    const copy = within(field).getByRole("button", {
      name: "Copy access token",
    });
    fireEvent.click(copy);
    expect(await within(field).findByText(copyError)).toBeVisible();
    fireEvent.click(copy);
    await waitFor(() =>
      expect(field.querySelector('[aria-live="polite"]')).toHaveTextContent(
        "Copied",
      ),
    );
    expect(within(field).queryByText(copyError)).toBeNull();
  });
});

describe("Deployment overview", () => {
  it("shows the URL, health, live revision and the latest operation", async () => {
    renderDeployments(<DeploymentDetailPage />, { route: at() });
    // Host-only (EE DEP-02): its revision is running, never "live" (DEMO-FIX-2 item 1).
    expect(await screen.findByTestId("live-revision")).toHaveTextContent(
      "Revision 2 running",
    );
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "greeter",
    );
    expect(screen.getByTestId("overview-url")).toHaveTextContent(
      "http://127.0.0.1:55408",
    );
    expect(await screen.findByTestId("deployment-health")).toHaveTextContent(
      "Healthy",
    );
    expect(
      within(screen.getByTestId("live-revision-card")).getByText("2"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("operation-card")).toHaveTextContent(
      "Deploy · revision 2",
    );
  });

  it("hides Open in Studio until a deployed server opens in Studio, keeping the Studio page link of a deployment with a network address", async () => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
    keepAccessToken({ id: ID, accessToken: "k".repeat(43) });
    const api = fakeApi({
      get: vi.fn(async () =>
        detailView({
          url: "https://greeter.agents.example.com",
          studioUrl: "https://greeter.agents.example.com/",
          urlReach: "network",
          urlNote: null,
        }),
      ),
    });
    renderDeployments(<DeploymentDetailPage />, { api, route: at() });
    expect(await screen.findByTestId("open-studio-page")).toHaveAttribute(
      "href",
      "https://greeter.agents.example.com/",
    );
    expect(screen.getByTestId("deployment-status")).toHaveTextContent("Live");
    expect(screen.getByTestId("live-revision")).toHaveTextContent(
      "Revision 2 live",
    );
    expect(screen.queryByTestId("open-in-studio")).toBeNull();
    expect(screen.getByTestId("studio-page-gate")).toHaveAttribute(
      "data-token-held",
      "true",
    );
    vi.unstubAllEnvs();
  });

  it("says Running on this host for a host-only deployment, shows its address as text and links nothing on that host, not even its Studio page (DEMO-FIX-2 item 1, DEMO-POLISH-3)", async () => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
    renderDeployments(<DeploymentDetailPage />, { route: at() });
    await screen.findByTestId("overview-url");
    const chip = screen.getByTestId("deployment-status");
    expect(chip).toHaveTextContent("Running on this host");
    expect(chip).toHaveAttribute("data-reach", "deployment-host");
    expect(screen.getByTestId("overview-url")).toHaveTextContent(
      "http://127.0.0.1:55408",
    );
    expect(screen.getByTestId("endpoint-reach")).toHaveTextContent(
      "Running on this host: Reachable from the deployment host only.",
    );
    // Its Studio page asks for an access token the person may never have
    // seen, so a host-only deployment links nothing on that host (round-5c
    // demo check: the link opened "Open Studio from the link omega dev
    // printed", a dead end). The address stays as text.
    expect(screen.queryByTestId("open-studio-page")).toBeNull();
    expect(screen.queryByTestId("studio-page-gate")).toBeNull();
    const local = [...document.querySelectorAll("[href]")]
      .map((a) => a.getAttribute("href"))
      .filter((href) => /127\.0\.0\.1|localhost/.test(href));
    expect(local).toEqual([]);
    vi.unstubAllEnvs();
  });

  it("opens Studio on the deployment only with the token this tab holds, handing it over in memory, never in a URL (OD-21)", async () => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
    const assign = vi.fn();
    vi.spyOn(window, "location", "get").mockReturnValue({
      ...window.location,
      assign,
    });
    renderDeployments(<DeploymentDetailPage />, {
      route: at(),
      parts: [OMEGA_PART.DEPLOYMENT_STUDIO],
    });
    const button = await screen.findByTestId("open-in-studio");
    expect(button).toBeDisabled();
    // One line says why. A host-only deployment links no Studio page
    // (DEMO-POLISH-3), so the line speaks for the button alone.
    expect(screen.getByTestId("studio-page-gate")).toHaveTextContent(
      "Open in Studio needs the access token shown when this deployment was created. Rotate it under Settings to get a new one.",
    );
    expect(screen.queryByTestId("open-studio-page")).toBeNull();

    const token = "k".repeat(43);
    keepAccessToken({ id: ID, accessToken: token });
    await waitFor(() =>
      expect(screen.getByTestId("open-in-studio")).toBeEnabled(),
    );
    // Holding the token, the button needs no word, and still no link appears.
    expect(screen.queryByTestId("open-studio-page")).toBeNull();
    expect(screen.queryByTestId("studio-page-gate")).toBeNull();
    await userEvent.click(screen.getByTestId("open-in-studio"));
    // A router push to the Studio tab, with no token in the path, query or fragment.
    expect(screen.getByTestId("location")).toHaveTextContent(
      /^\/dashboard\/studio$/,
    );
    expect(assign).not.toHaveBeenCalled();
    expect(window.location.href).not.toContain(token);
    // The Studio tab gets the server and the token once, from memory.
    expect(peekOmegaConnect()).toEqual({
      url: "http://127.0.0.1:55408",
      origin: "http://127.0.0.1:55408",
    });
    expect(takeOmegaConnectToken()).toBe(token);
    expect(takeOmegaConnectToken()).toBeNull();
    vi.unstubAllEnvs();
  });

  it("hides Studio for a deployment that does not serve the Studio page", async () => {
    const api = fakeApi({
      get: vi.fn(async () => detailView({ studioUrl: null })),
    });
    renderDeployments(<DeploymentDetailPage />, { api, route: at() });
    await screen.findByTestId("overview-url");
    expect(screen.queryByTestId("open-in-studio")).toBeNull();
  });

  it("is a calm not-found for another workspace's id and for a malformed id", async () => {
    const api = fakeApi({
      get: vi.fn(async () => {
        throw new OmegaError({ state: "not-found", status: 404 });
      }),
    });
    renderDeployments(<DeploymentDetailPage />, { api, route: at() });
    expect(await screen.findByText("Deployment not found")).toBeInTheDocument();
    renderDeployments(<DeploymentDetailPage />, {
      route: "/dashboard/deployments/not-an-id",
    });
    await waitFor(() =>
      expect(screen.getAllByText("Deployment not found")).toHaveLength(2),
    );
  });

  // The sign-in copy says to try again in a moment, so the action asks
  // Omega again; it does not reload the page.
  async function expectTryAgainAsksAgain(state, api, reload) {
    expect(
      within(state).queryByRole("button", { name: "Reload" }),
    ).not.toBeInTheDocument();
    const before = api.get.mock.calls.length;
    await userEvent.click(
      within(state).getByRole("button", { name: "Try again" }),
    );
    await waitFor(() =>
      expect(api.get.mock.calls.length).toBeGreaterThan(before),
    );
    expect(reload).not.toHaveBeenCalled();
  }

  it("ends on a sign-in state, not on skeletons or the role, when Omega answers 401, and Try again asks again", async () => {
    const api = fakeApi({
      get: vi.fn(async () => {
        throw new OmegaError({
          state: "refused",
          status: 401,
          title: "Omega couldn't confirm your sign-in",
          message:
            "Future AGI couldn't confirm your sign-in with Omega. Try again in a moment. If it keeps happening, contact Future AGI support.",
        });
      }),
    });
    const reload = vi.fn();
    vi.spyOn(window, "location", "get").mockReturnValue({
      ...window.location,
      reload,
    });
    renderDeployments(<DeploymentDetailPage />, { api, route: at() });
    const state = await screen.findByTestId(
      "deployment-error-state",
      {},
      { timeout: 5000 },
    );
    expect(state).toHaveTextContent("Omega couldn't confirm your sign-in");
    expect(state).not.toHaveTextContent("your role");
    expect(screen.queryByTestId("deployment-loading")).toBeNull();
    await expectTryAgainAsksAgain(state, api, reload);
  });

  // EE's deployment routes answer a request with no current signed-in
  // workspace with a 403 `unauthenticated` (project-deployments/routes), and
  // its Future AGI copy names the sign-in, not the role, and says to try
  // again in a moment and to contact support if it keeps happening.
  it("offers Try again, not Reload or the role, when EE refuses the sign-in with a 403", async () => {
    const api = fakeApi({
      get: vi.fn(async () => {
        throw refusal(403, {
          schemaVersion: "omega-workspace-deployments/v1",
          error: {
            code: "unauthenticated",
            message: "A current signed-in workspace is required.",
          },
        });
      }),
    });
    const reload = vi.fn();
    vi.spyOn(window, "location", "get").mockReturnValue({
      ...window.location,
      reload,
    });
    renderDeployments(<DeploymentDetailPage />, { api, route: at() });
    const state = await screen.findByTestId(
      "deployment-error-state",
      {},
      { timeout: 5000 },
    );
    expect(state).toHaveTextContent("Omega couldn't confirm your sign-in");
    expect(state).toHaveTextContent(
      "Try again in a moment. If it keeps happening, contact Future AGI support.",
    );
    expect(state).not.toHaveTextContent("Reload the page");
    expect(state).not.toHaveTextContent("your role");
    await expectTryAgainAsksAgain(state, api, reload);
  });

  it("keeps Reload for a 401 whose own copy says to reload the page", async () => {
    const api = fakeApi({
      get: vi.fn(async () => {
        throw refusal(401, { error: { code: "unauthorized" } });
      }),
    });
    const reload = vi.fn();
    vi.spyOn(window, "location", "get").mockReturnValue({
      ...window.location,
      reload,
    });
    renderDeployments(<DeploymentDetailPage />, { api, route: at() });
    const state = await screen.findByTestId(
      "deployment-error-state",
      {},
      { timeout: 5000 },
    );
    expect(state).toHaveTextContent("Reload the page to try again.");
    expect(
      within(state).queryByRole("button", { name: "Try again" }),
    ).not.toBeInTheDocument();
    await userEvent.click(
      within(state).getByRole("button", { name: "Reload" }),
    );
    expect(reload).toHaveBeenCalled();
  });

  it("still says it is the role, with no Reload, when EE denies the permission", async () => {
    const api = fakeApi({
      get: vi.fn(async () => {
        throw refusal(403, {
          schemaVersion: "omega-workspace-deployments/v1",
          error: {
            code: "forbidden",
            message: "Only administrators manage credentials.",
          },
        });
      }),
    });
    renderDeployments(<DeploymentDetailPage />, { api, route: at() });
    const state = await screen.findByTestId(
      "deployment-error-state",
      {},
      { timeout: 5000 },
    );
    expect(state).toHaveTextContent(
      "Deployments aren't available to your role",
    );
    expect(
      within(state).queryByRole("button", { name: "Reload" }),
    ).not.toBeInTheDocument();
  });

  // Each refusal's action is the one its own copy names: Reload where the copy
  // says to reload the page, Try again where it says to try again in a moment
  // (the sign-in), and none for a real permission denial, which keeps its
  // role heading.
  it("offers the action a 403 refusal's own copy names", () => {
    const cases = [
      ["unauthenticated", null, "retry"],
      ["role_out_of_date", null, "reload"],
      ["omega_scope_mismatch", null, "reload"],
      ["session_exchange_refused", null, "reload"],
      ["unauthorized", null, "reload"],
      ["omega_scope_mismatch", "role_below_floor", null],
      ["omega_scope_mismatch", "scope_pivot", null],
      ["role_forbidden", null, null],
      ["forbidden", null, null],
      ["omega_viewer_not_admitted", null, null],
    ];
    for (const [code, reason, action] of cases) {
      const error = refusal(403, { error: code, reason });
      const copy = errorCopy(error);
      const label = `${code} (${reason})`;
      expect(Boolean(copy.reload), label).toBe(action === "reload");
      expect(Boolean(copy.retry), label).toBe(action === "retry");
      expect(copy.description, label).toBe(error.message);
      expect(/Reload the page/.test(copy.description), label).toBe(
        action === "reload",
      );
      expect(/Try again in a moment/.test(copy.description), label).toBe(
        action === "retry",
      );
      if (action) expect(copy.title, label).toBe(error.title);
    }
  });

  // The Future AGI session itself ended: the session exchange answered 401
  // (shared/sessionAssertion.js), so a reload cannot help; signing in again can.
  it("ends a signed-out detail request with a sign-in action instead of a skeleton", async () => {
    const api = fakeApi({
      get: vi.fn(async () => {
        throw new OmegaError({
          state: "signed-out",
          status: 401,
          code: "session_ended",
        });
      }),
    });
    renderDeployments(<DeploymentDetailPage />, { api, route: at() });
    expect(
      await screen.findByText("Your session has ended"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("deployment-sign-in")).toHaveAttribute(
      "href",
      `/auth/jwt/login?returnTo=${encodeURIComponent(at())}`,
    );
    expect(screen.queryByTestId("deployment-loading")).toBeNull();
  });

  it("gives rate-limited requests a retry heading and wait guidance", () => {
    expect(
      errorCopy({ status: 429, code: "omega_rate_limited", retryAfter: 12 }),
    ).toMatchObject({
      title: "Too many requests",
      description: expect.stringContaining("12 seconds"),
      retry: true,
    });
    expect(errorCopy({ status: 429 })).toMatchObject({
      title: "Too many requests",
      description: expect.stringContaining("Try again in a few seconds"),
      retry: true,
    });
  });

  // The last resort said "Something went wrong" with nothing under it.
  it("names what failed and what to do for an answer it has no words for", () => {
    const copy = errorCopy({
      state: "error",
      status: 500,
      code: null,
      message: null,
    });
    expect(copy).toMatchObject({
      title: "Deployments couldn't complete this request",
      description:
        "Try again. If it keeps happening, contact Future AGI support.",
      retry: true,
    });
    expect(copy.title).not.toMatch(/something went wrong/i);
    const worded = errorCopy({
      state: "error",
      status: 500,
      message: "Build queue is full.",
    });
    expect(worded.description).toBe("Build queue is full.");
  });

  it("heads a deploy failure with what failed only when EE sent no words of its own", () => {
    const { rerender } = render(
      <CreateError error={{ state: "error", status: 500, message: null }} />,
    );
    expect(screen.getByTestId("deploy-error").textContent).toBe(
      "Deployments couldn't complete this request. Try again. If it keeps happening, contact Future AGI support.",
    );
    rerender(
      <CreateError
        error={{
          state: "error",
          status: 400,
          message: "No active credential named X.",
        }}
      />,
    );
    expect(screen.getByTestId("deploy-error").textContent).toBe(
      "No active credential named X.",
    );
  });
});

describe("Revisions and rollback", () => {
  it("uses the error color for the rollback confirmation (FA-DEP-UX-02)", async () => {
    renderDeployments(<DeploymentDetailPage />, { route: at("revisions") });
    const rows = await screen.findAllByTestId("revision-row");
    await userEvent.click(within(rows[1]).getByTestId("rollback"));
    expect(screen.getByTestId("confirm-rollback")).toHaveClass(
      "MuiButton-containedError",
    );
  });

  it("gives a failed revision reason its own wrapping Typography (FA-DEP-UX-04)", async () => {
    const message =
      "Health check timed out because the agent could not read its required configuration; check the deployment log and try again.";
    const api = fakeApi({
      get: vi.fn(async () =>
        detailView({
          revisions: [
            revisionView(2, { status: "live" }),
            revisionView(1, {
              status: "failed",
              liveAt: null,
              message,
              secretRefs: ["SERVICE_KEY", "DATABASE_KEY"],
            }),
          ],
        }),
      ),
    });
    renderDeployments(<DeploymentDetailPage />, {
      api,
      route: at("revisions"),
    });
    const rows = await screen.findAllByTestId("revision-row");
    const reason = within(rows[1]).getByText(message, { exact: false });
    expect(reason).toHaveClass("MuiTypography-root");
    expect(reason).not.toHaveClass("MuiTypography-noWrap");
    expect(reason.textContent).toBe(message);
    expect(reason).toHaveStyle({ overflowWrap: "anywhere" });
  });

  it("offers Roll back only on revisions that served before, fenced by the live revision", async () => {
    const detail = detailView({
      revision: 3,
      revisionCount: 3,
      revisions: [
        revisionView(3, {
          status: "failed",
          liveAt: undefined,
          message: "Health check timed out.",
        }),
        revisionView(2, { status: "live" }),
        revisionView(1),
      ],
    });
    const api = fakeApi({ get: vi.fn(async () => detail) });
    renderDeployments(<DeploymentDetailPage />, {
      api,
      route: at("revisions"),
    });
    const rows = await screen.findAllByTestId("revision-row");
    expect(rows.map((r) => r.dataset.revision)).toEqual(["3", "2", "1"]);
    expect(within(rows[0]).queryByTestId("rollback")).toBeNull();
    expect(within(rows[0])).toBeTruthy();
    expect(rows[0]).toHaveTextContent("Health check timed out.");
    expect(within(rows[1]).queryByTestId("rollback")).toBeNull();
    expect(within(rows[1]).getByTestId("revision-status")).toHaveAttribute(
      "data-status",
      "live",
    );

    await userEvent.click(within(rows[2]).getByTestId("rollback"));
    await userEvent.click(screen.getByTestId("confirm-rollback"));
    await waitFor(() => expect(api.rollback).toHaveBeenCalledTimes(1));
    const [id, target, body, { idempotencyKey }] = api.rollback.mock.calls[0];
    expect([id, target, body]).toEqual([ID, 1, { expectedRevision: 2 }]);
    expect(idempotencyKey).toMatch(/^fa-dep-rollback-/);
  });

  it("shows EE's conflict when the live revision moved", async () => {
    const api = fakeApi({
      rollback: vi.fn(async () => {
        throw new OmegaError({
          state: "error",
          status: 409,
          code: "revision_conflict",
          message: "Revision 3 is live, not 2; refresh and decide again.",
        });
      }),
    });
    renderDeployments(<DeploymentDetailPage />, {
      api,
      route: at("revisions"),
    });
    const rows = await screen.findAllByTestId("revision-row");
    await userEvent.click(within(rows[1]).getByTestId("rollback"));
    await userEvent.click(screen.getByTestId("confirm-rollback"));
    expect(await screen.findByTestId("rollback-error")).toHaveTextContent(
      "Revision 3 is live, not 2",
    );
    expect(api.rollback).toHaveBeenCalledTimes(1);
  });

  it("lists the history newest first", async () => {
    renderDeployments(<DeploymentDetailPage />, { route: at("revisions") });
    const entries = await screen.findAllByTestId("history-entry");
    expect(entries[0]).toHaveTextContent("Deploy: revision 2 (from 1) live");
  });
});

describe("Logs and settings", () => {
  it("uses the error color for the rotate confirmation (FA-DEP-UX-02)", async () => {
    renderDeployments(<DeploymentDetailPage />, { route: at("settings") });
    await userEvent.click(await screen.findByTestId("rotate-token"));
    expect(screen.getByTestId("confirm-rotate")).toHaveClass(
      "MuiButton-containedError",
    );
  });

  it("follows the runtime log of a live deployment and a revision's build log on request", async () => {
    const api = fakeApi();
    renderDeployments(<DeploymentDetailPage />, { api, route: at("logs") });
    await waitFor(() =>
      expect(api.logs).toHaveBeenCalledWith(
        ID,
        expect.objectContaining({ source: "runtime", tail: 200 }),
      ),
    );
    await userEvent.click(screen.getByTestId("logs-build"));
    await waitFor(() =>
      expect(api.logs).toHaveBeenLastCalledWith(
        ID,
        expect.objectContaining({ source: "build", revision: 2 }),
      ),
    );
  });

  it("shows the revision's variables and secret names, and deletes only after the name is typed", async () => {
    const api = fakeApi({
      get: vi.fn(async () =>
        detailView({
          revisions: [
            revisionView(2, {
              status: "live",
              secretRefs: ["SERVE_FIXTURE_SECRET"],
            }),
            revisionView(1),
          ],
        }),
      ),
    });
    renderDeployments(<DeploymentDetailPage />, { api, route: at("settings") });
    // A variable's value is masked until asked for (audit F4).
    const entry = await screen.findByTestId("env-entry");
    expect(entry).toHaveTextContent("SERVE_FIXTURE_GREETING");
    expect(entry).toHaveTextContent("••••••••");
    expect(entry).not.toHaveTextContent("hello-v2");
    expect(within(entry).getByTestId("env-value")).toHaveAttribute(
      "data-masked",
      "true",
    );
    await userEvent.click(
      within(entry).getByRole("button", {
        name: "Show the value of SERVE_FIXTURE_GREETING",
      }),
    );
    expect(entry).toHaveTextContent("SERVE_FIXTURE_GREETINGhello-v2");
    await userEvent.click(
      within(entry).getByRole("button", {
        name: "Hide the value of SERVE_FIXTURE_GREETING",
      }),
    );
    expect(entry).not.toHaveTextContent("hello-v2");
    expect(screen.getByTestId("secret-refs")).toHaveTextContent(
      "SERVE_FIXTURE_SECRET",
    );
    await userEvent.click(screen.getByTestId("change-settings"));
    expect(screen.getByTestId("location")).toHaveTextContent(
      `/dashboard/deployments/new?deployment=${ID}`,
    );
  });

  it("deletes only after the name is typed, then returns to the list", async () => {
    const api = fakeApi();
    renderDeployments(<DeploymentDetailPage />, { api, route: at("settings") });
    await userEvent.click(await screen.findByTestId("delete-deployment"));
    expect(screen.getByTestId("confirm-delete")).toBeDisabled();
    await userEvent.type(screen.getByTestId("delete-confirm-name"), "greeter");
    const reads = api.get.mock.calls.length;
    await userEvent.click(screen.getByTestId("confirm-delete"));
    await waitFor(() => expect(api.remove).toHaveBeenCalledWith(ID));
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        /^\/dashboard\/deployments$/,
      ),
    );
    // The deleted deployment is forgotten, never read again (that would 404).
    expect(api.get).toHaveBeenCalledTimes(reads);
  });

  it("rotates the token with a key and shows the new one once", async () => {
    const api = fakeApi({
      rotateToken: vi.fn(async () => ({
        ...detailView(),
        accessToken: "n".repeat(43),
      })),
    });
    renderDeployments(<DeploymentDetailPage />, { api, route: at("settings") });
    await userEvent.click(await screen.findByTestId("rotate-token"));
    await userEvent.click(screen.getByTestId("confirm-rotate"));
    await waitFor(() =>
      expect(api.rotateToken).toHaveBeenCalledWith(ID, {
        idempotencyKey: expect.stringMatching(/^fa-dep-token-/),
      }),
    );
    expect(
      await screen.findAllByTestId("access-token-notice"),
    ).not.toHaveLength(0);
  });
});
