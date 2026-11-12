import React from "react";
import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createOmegaClient,
  probeOmegaSession,
} from "src/sections/omega/shared";
import { OmegaError } from "src/sections/omega/shared/omegaStates";

import { createSandboxesApi } from "../api/sandboxesApi";
import { errorCopy } from "../components/SandboxErrorState";
import {
  CAPS,
  CAPS_V2,
  PROVIDER,
  fakeApi,
  renderSandboxes,
  sandboxView,
} from "./helpers";
import { session, workspace } from "./session-mock";

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));
vi.mock("src/sections/omega/shared", async (importOriginal) => ({
  ...(await importOriginal()),
  useOmegaSession: () => session,
}));

const { default: SandboxesListPage } = await import("../SandboxesListPage");

beforeEach(() => {
  Object.assign(session, {
    state: "ready",
    code: null,
    title: null,
    message: null,
    retryAfter: null,
  });
});

describe("Sandboxes list page", () => {
  it("lists the workspace's sandboxes with state, runtime, image, limits and hibernation", async () => {
    const api = fakeApi({
      list: vi.fn(async () => ({
        sandboxes: [
          sandboxView(),
          sandboxView({
            id: "sbx_ffffffffffffffffffffffff",
            name: "notebook",
            state: "hibernated",
            hibernatesAt: undefined,
            image: "python:3.12-slim",
            limits: { cpus: 0.5, memoryMb: 256, pidsLimit: 64 },
          }),
        ],
        caps: CAPS,
        provider: PROVIDER,
      })),
    });
    renderSandboxes(<SandboxesListPage />, { api });
    const rows = await screen.findAllByTestId("sandbox-row");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("data-cleanup")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Running")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Container")).toBeInTheDocument();
    expect(within(rows[0]).getByText("1 CPU · 512 MiB")).toBeInTheDocument();
    expect(
      within(rows[0]).getByTestId("sandbox-hibernates").textContent,
    ).toMatch(/^in 2\d min$/);
    expect(within(rows[1]).getByText("Hibernated")).toBeInTheDocument();
    expect(within(rows[1]).getByText("python:3.12-slim")).toBeInTheDocument();
    expect(within(rows[1]).getByTestId("sandbox-hibernates").textContent).toBe(
      "—",
    );
    expect(screen.getByTestId("sandboxes-active-count").textContent).toBe(
      "You can see 1 running sandbox. This workspace can run 10 at once.",
    );
    fireEvent.click(rows[1]);
    expect(screen.getByTestId("location").textContent).toBe(
      "/dashboard/sandboxes/sbx_ffffffffffffffffffffffff",
    );
  });

  // DEMO-POLISH-3 (round-5c demo check): Customer B's list said "1 of 10 running" beside two Running rows.
  // The second was an agent run's computer, which the workspace limit does not count (EE counts the
  // workspace's own sandboxes). The line now names both, so it agrees with the rows.
  it.each([
    [
      1,
      "You can see 1 running sandbox. This workspace can run 10 at once. Agent runs (1) don't count.",
    ],
    [
      2,
      "You can see 1 running sandbox. This workspace can run 10 at once. Agent runs (2) don't count.",
    ],
  ])(
    "counts %i running agent run(s) beside the sandboxes, as the rows show them",
    async (runs, line) => {
      const agentRuns = Array.from({ length: runs }, (_, index) =>
        sandboxView({
          id: `agent-task-task_${index}`,
          origin: "agent_run",
          state: "active",
          readOnly: true,
          name: `Run: weekly summary ${index}`,
        }),
      );
      const api = fakeApi({
        list: vi.fn(async () => ({
          sandboxes: [
            sandboxView(),
            ...agentRuns,
            sandboxView({
              id: "agent-task-task_done",
              origin: "agent_run",
              state: "hibernated",
              readOnly: true,
              name: "Run: parked",
            }),
          ],
          caps: CAPS,
          provider: PROVIDER,
        })),
      });
      renderSandboxes(<SandboxesListPage />, { api });
      const rows = await screen.findAllByTestId("sandbox-row");
      const runningRows = rows.filter((row) =>
        within(row).queryByText("Running"),
      );
      expect(runningRows).toHaveLength(1 + runs);
      expect(screen.getByTestId("sandboxes-active-count").textContent).toBe(
        line,
      );
    },
  );

  // The header said "You can see 4 running sandboxes … Agent runs (1) don't
  // count." while the toolbar said "8 sandboxes · 5 running".
  it("counts running agent runs apart in the toolbar, as the header does", async () => {
    const api = fakeApi({
      list: vi.fn(async () => ({
        sandboxes: [
          sandboxView(),
          sandboxView({
            id: "agent-task-task_0",
            origin: "agent_run",
            state: "active",
            readOnly: true,
            name: "Run: weekly summary",
          }),
          sandboxView({
            id: "agent-task-task_done",
            origin: "agent_run",
            state: "hibernated",
            readOnly: true,
            name: "Run: parked",
          }),
        ],
        caps: CAPS,
        provider: PROVIDER,
      })),
    });
    renderSandboxes(<SandboxesListPage />, { api });
    await screen.findAllByTestId("sandbox-row");
    expect(screen.getByTestId("sandboxes-active-count")).toHaveTextContent(
      "You can see 1 running sandbox. This workspace can run 10 at once. Agent runs (1) don't count.",
    );
    expect(screen.getByTestId("sandboxes-count").textContent).toBe(
      "3 sandboxes · 1 running + 1 agent run",
    );
    expect(screen.getByTestId("sandboxes-filter-active")).toHaveTextContent(
      "Running 2",
    );
  });

  it("leaves out run sandboxes whose run has finished", async () => {
    const api = fakeApi({
      list: vi.fn(async () => ({
        sandboxes: [
          sandboxView({
            id: "agent-task-task_1",
            origin: "agent_run",
            state: "destroyed",
            readOnly: true,
            name: "Run: old",
          }),
        ],
        caps: CAPS,
        provider: PROVIDER,
      })),
    });
    renderSandboxes(<SandboxesListPage />, { api });
    expect(await screen.findByTestId("sandboxes-empty")).toBeInTheDocument();
    expect(screen.queryByText("Run: old")).toBeNull();
  });

  it("shows one calm empty state with a single New sandbox action", async () => {
    const api = fakeApi({
      list: vi.fn(async () => ({
        sandboxes: [],
        caps: CAPS,
        provider: PROVIDER,
      })),
    });
    renderSandboxes(<SandboxesListPage />, { api });
    const empty = await screen.findByTestId("sandboxes-empty");
    expect(within(empty).getByText("No sandboxes yet")).toBeInTheDocument();
    expect(screen.getAllByTestId("new-sandbox")).toHaveLength(1);
    fireEvent.click(screen.getByTestId("new-sandbox"));
    expect(
      await screen.findByTestId("create-sandbox-drawer"),
    ).toBeInTheDocument();
  });

  describe("when the workspace refuses every create", () => {
    const REFUSAL = {
      field: "size",
      reason: "This workspace offers only XL, and it cannot run here.",
    };
    const listing = (sandboxes, caps) =>
      fakeApi({
        list: vi.fn(async () => ({
          sandboxes,
          caps: { ...CAPS_V2, createRefusal: REFUSAL, ...caps },
          provider: PROVIDER,
        })),
      });

    it("keeps New sandbox on an empty list, off and tied to the refusal, and points an admin at the settings", async () => {
      renderSandboxes(<SandboxesListPage />, { api: listing([]) });
      const empty = await screen.findByTestId("sandboxes-empty");
      expect(empty).toHaveTextContent(
        "Nothing can be created here until the issue above is resolved.",
      );
      // Production shows the button here; it stays, off, with its reason.
      expect(screen.getAllByTestId("new-sandbox")).toHaveLength(1);
      const button = within(empty).getByTestId("new-sandbox");
      expect(button).toBeDisabled();
      expect(button).toHaveAttribute(
        "aria-describedby",
        "sandboxes-create-refusal",
      );
      expect(button.parentElement).toHaveAttribute(
        "aria-label",
        "New sandboxes can't be created in this workspace right now.",
      );
      fireEvent.click(button);
      expect(screen.queryByTestId("create-sandbox-drawer")).toBeNull();
      expect(screen.getByTestId("sandboxes-create-refusal")).toHaveTextContent(
        REFUSAL.reason,
      );
      fireEvent.click(screen.getByTestId("sandboxes-create-refusal-settings"));
      expect(screen.getByTestId("location").textContent).toBe(
        "/dashboard/settings/sandboxes",
      );
    });

    it("turns the header's New sandbox off and ties it to the refusal, with no settings for a member", async () => {
      renderSandboxes(<SandboxesListPage />, {
        api: listing([sandboxView()], { settings: { canEdit: false } }),
      });
      const button = await screen.findByTestId("new-sandbox");
      expect(button).toBeDisabled();
      expect(button).toHaveAttribute(
        "aria-describedby",
        "sandboxes-create-refusal",
      );
      expect(screen.getByTestId("sandboxes-create-refusal")).toHaveAttribute(
        "id",
        "sandboxes-create-refusal",
      );
      expect(
        screen.queryByTestId("sandboxes-create-refusal-settings"),
      ).toBeNull();
      expect(screen.queryByTestId("sandboxes-at-cap")).toBeNull();
    });
  });

  // An older EE's 503. Today's EE answers 200 with a coded create refusal, and
  // both render the same unavailable state (Unavailable.test.jsx).
  describe("when sandboxes are off on this deployment", () => {
    const offList = (message) =>
      fakeApi({
        list: vi.fn(async () => {
          throw new OmegaError({
            state: "unavailable",
            status: 503,
            code: "sandboxes_unavailable",
            message,
          });
        }),
      });

    // EE's default, Future AGI's copy for the code, and no message at all
    // (an OmegaError then carries the stock "Omega isn't responding" line).
    it.each([
      ["Sandboxes are not enabled on this deployment."],
      ["Sandboxes aren't available right now."],
      [null],
    ])(
      "says so and what it needs for %s, with no Try again or New sandbox",
      async (message) => {
        renderSandboxes(<SandboxesListPage />, { api: offList(message) });
        const state = await screen.findByTestId("sandboxes-unavailable");
        expect(state).toHaveTextContent(
          "Sandboxes are not available on this deployment.",
        );
        expect(state).toHaveTextContent(
          "An administrator enables a sandbox host for this deployment.",
        );
        expect(state).not.toHaveTextContent(/Omega/);
        expect(within(state).queryByRole("button")).toBeNull();
        expect(screen.queryByTestId("new-sandbox")).toBeNull();
        expect(screen.queryByTestId("sandbox-row")).toBeNull();
      },
    );

    it("names EE's own reason when it gives a more specific one", async () => {
      const reason =
        "Sandboxes need the artifact store and the accounts plane; this deployment has neither wired.";
      renderSandboxes(<SandboxesListPage />, { api: offList(reason) });
      const state = await screen.findByTestId("sandboxes-unavailable");
      expect(state).toHaveTextContent(
        "Sandboxes are not available on this deployment.",
      );
      expect(state).toHaveTextContent(reason);
      expect(state).not.toHaveTextContent(
        "An administrator enables a sandbox host",
      );
      expect(within(state).queryByRole("button")).toBeNull();
    });

    it("never shows an operator's reason; it says what the deployment needs instead", async () => {
      renderSandboxes(<SandboxesListPage />, {
        api: offList(
          "Sandboxes are off on this deployment. An operator enables them with OMEGA_WORKSPACE_SANDBOXES=1.",
        ),
      });
      const state = await screen.findByTestId("sandboxes-unavailable");
      expect(state).toHaveTextContent(
        "An administrator enables a sandbox host for this deployment.",
      );
      expect(state).not.toHaveTextContent(/operator|OMEGA_WORKSPACE/i);
    });
  });

  it("does not blame the user's role when EE refuses delegated sessions for sandboxes", async () => {
    const api = fakeApi({
      list: vi.fn(async () => {
        throw new OmegaError({
          state: "refused",
          status: 403,
          code: "host_delegation_scope_denied",
          message: null,
        });
      }),
    });
    renderSandboxes(<SandboxesListPage />, { api });
    expect(
      await screen.findByText("Sandboxes can't be opened in this workspace"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/role/i)).toBeNull();
  });

  it("never calls the API when the session cannot use Omega", async () => {
    Object.assign(session, {
      state: "refused",
      code: "role_forbidden",
      message: "Viewers cannot use Omega.",
    });
    const api = fakeApi();
    renderSandboxes(<SandboxesListPage />, { api });
    expect(
      await screen.findByText("You don't have access to Sandboxes here"),
    ).toBeInTheDocument();
    expect(screen.getByText("Viewers cannot use Omega.")).toBeInTheDocument();
    await waitFor(() => expect(api.list).not.toHaveBeenCalled());
  });

  it("heads a viewer's refusal with the session's own Future AGI title", async () => {
    Object.assign(session, {
      state: "refused",
      code: "omega_viewer_not_admitted",
      title: "Viewers can't use Omega",
      message:
        "Viewers can't use Omega in this workspace. Ask a workspace owner or admin for member access.",
    });
    renderSandboxes(<SandboxesListPage />);
    const state = await screen.findByTestId("sandbox-error-state");
    expect(state).toHaveTextContent("Viewers can't use Omega");
    expect(state).not.toHaveTextContent("You don't have access");
    expect(within(state).queryByRole("button", { name: "Reload" })).toBeNull();
  });

  it.each([
    ["session_exchange_refused", "Omega isn't available to you here"],
    ["role_out_of_date", "Your role just changed"],
    ["omega_scope_mismatch", "This belongs to another workspace"],
  ])(
    "offers Reload when the %s refusal says to reload",
    async (code, title) => {
      Object.assign(session, {
        state: "refused",
        code,
        title,
        message: "Reload the page.",
      });
      renderSandboxes(<SandboxesListPage />);
      const state = await screen.findByTestId("sandbox-error-state");
      expect(state).toHaveTextContent(title);
      expect(
        within(state).getByRole("button", { name: "Reload" }),
      ).toBeInTheDocument();
    },
  );

  it("still stops at the gate when Future AGI can't open a session", async () => {
    Object.assign(session, {
      state: "unavailable",
      code: "session_exchange_unavailable",
      title: "Future AGI couldn't open Omega",
      message:
        "Future AGI couldn't start an Omega session just now. Try again in a moment.",
    });
    const api = fakeApi();
    renderSandboxes(<SandboxesListPage />, { api });
    expect(
      await screen.findByTestId("sandbox-error-state"),
    ).toBeInTheDocument();
    await waitFor(() => expect(api.list).not.toHaveBeenCalled());
  });
});

// Staging walk (head-72, 2026-11-10 16:20Z): /dashboard/sandboxes showed only
// "Omega can't complete this request right now." with a Try again that
// repeated it. Something in front of EE answered the gateway check (GET
// /omega/api/readyz, sent without credentials) 401, so the session probe said
// gateway_unavailable and the page never made its own sandbox call, which
// carries a minted assertion and gets EE's real answer.
describe("Sandboxes list page when only the gateway check fails", () => {
  const BASE = "/omega/api";
  const TOKEN = "omega_delegate_test.a.b";
  const mintAssertion = vi.fn(async () => ({
    token: TOKEN,
    expiresAt: Date.now() + 45_000,
  }));
  const json = (status, body) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    });

  function stagingFetch(sandboxAnswer) {
    return vi.fn(async (url) => {
      if (url === `${BASE}/readyz`) return new Response("", { status: 401 });
      if (url === `${BASE}/workspace/sandboxes`) return sandboxAnswer();
      throw new TypeError(`Unexpected request to ${url}`);
    });
  }

  async function probeFails(fetchImpl) {
    Object.assign(
      session,
      await probeOmegaSession({ mintAssertion, fetchImpl, basePath: BASE }),
    );
    expect(session).toMatchObject({
      state: "unavailable",
      code: "gateway_unavailable",
    });
  }

  function realApi(fetchImpl) {
    const { omegaJson } = createOmegaClient({
      basePath: BASE,
      fetchImpl,
      mintAssertion,
    });
    return createSandboxesApi({ omegaJson });
  }

  it("shows EE's sandboxes-off answer in plain words, with no Try again", async () => {
    const fetchImpl = stagingFetch(() =>
      json(503, {
        schemaVersion: "omega-workspace-sandboxes/v1",
        error: {
          code: "sandboxes_unavailable",
          message: "Sandboxes are not enabled on this deployment.",
          cause: "product_withheld",
        },
      }),
    );
    await probeFails(fetchImpl);
    renderSandboxes(<SandboxesListPage />, { api: realApi(fetchImpl) });
    const state = await screen.findByTestId("sandboxes-unavailable");
    expect(state).toHaveTextContent(
      "Sandboxes are not available on this deployment.",
    );
    expect(state).toHaveTextContent(
      "An administrator enables a sandbox host for this deployment.",
    );
    expect(state).not.toHaveTextContent(/Omega/);
    expect(within(state).queryByRole("button")).toBeNull();
    expect(screen.queryByTestId("new-sandbox")).toBeNull();
    expect(fetchImpl).toHaveBeenCalledWith(
      `${BASE}/workspace/sandboxes`,
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: `Bearer ${TOKEN}`,
        }),
      }),
    );
  });

  it("shows the sign-in refusal with Try again, which asks again, when the sandbox route is refused too", async () => {
    const fetchImpl = stagingFetch(() => new Response("", { status: 401 }));
    await probeFails(fetchImpl);
    renderSandboxes(<SandboxesListPage />, { api: realApi(fetchImpl) });
    const state = await screen.findByTestId(
      "sandbox-error-state",
      {},
      { timeout: 5000 },
    );
    expect(state).toHaveTextContent("Omega couldn't confirm your sign-in");
    expect(state).not.toHaveTextContent(/isn't responding|can't complete/);
    expect(within(state).queryByRole("button", { name: "Reload" })).toBeNull();
    const route = `${BASE}/workspace/sandboxes`;
    const listCalls = () =>
      fetchImpl.mock.calls.filter(([url]) => url === route).length;
    const before = listCalls();
    fireEvent.click(within(state).getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(listCalls()).toBeGreaterThan(before));
  });

  it("lists the sandboxes when the sandbox route answers", async () => {
    Object.assign(session, {
      state: "unavailable",
      code: "gateway_unavailable",
      title: "Omega isn't responding",
      message: "Omega isn't responding right now. Try again in a moment.",
    });
    const api = fakeApi();
    renderSandboxes(<SandboxesListPage />, { api });
    expect(await screen.findAllByTestId("sandbox-row")).toHaveLength(1);
    expect(screen.queryByTestId("sandbox-error-state")).toBeNull();
    expect(screen.queryByText(/Omega isn't responding/)).toBeNull();
  });

  it("says Sandboxes can't be reached when the sandbox call gets no answer either, and Try again asks again", async () => {
    Object.assign(session, {
      state: "unavailable",
      code: "session_probe_failed",
      title: "Omega isn't responding",
      message: "Omega isn't responding right now. Try again in a moment.",
    });
    let reachable = false;
    const api = fakeApi({
      list: vi.fn(async () => {
        if (reachable)
          return { sandboxes: [sandboxView()], caps: CAPS, provider: PROVIDER };
        throw new OmegaError({
          state: "unavailable",
          status: null,
          code: "network_error",
          title: "Omega can't be reached",
          message:
            "Your browser couldn't reach Omega. Check your connection and try again.",
        });
      }),
    });
    renderSandboxes(<SandboxesListPage />, { api });
    const state = await screen.findByTestId(
      "sandbox-error-state",
      {},
      { timeout: 8000 },
    );
    expect(state).toHaveTextContent("Sandboxes can't be reached right now.");
    expect(state).not.toHaveTextContent(/Omega/);
    reachable = true;
    fireEvent.click(within(state).getByRole("button", { name: "Try again" }));
    expect(await screen.findAllByTestId("sandbox-row")).toHaveLength(1);
  });
});

describe("Sandboxes error copy", () => {
  it.each([
    [{ state: "unavailable", status: 502, code: null }],
    [{ state: "unavailable", status: null, code: "network_error" }],
    [{ state: "unavailable", status: 502, code: "omega_remote_unreachable" }],
    [{ state: "unavailable", status: 504, code: "omega_remote_timeout" }],
    [{ state: "unavailable", status: 502, code: "omega_gateway_failure" }],
  ])("calls %o a failure to reach Sandboxes, with Try again", (error) => {
    const copy = errorCopy({
      ...error,
      message: "Omega isn't responding right now. Try again in a moment.",
    });
    expect(copy).toMatchObject({
      title: "Sandboxes can't be reached right now.",
      description: "This page didn't get an answer. Try again in a moment.",
      retry: true,
    });
  });

  it("names what failed and what to do for an answer it has no words for", () => {
    const copy = errorCopy({
      state: "error",
      status: 500,
      code: null,
      message: null,
    });
    expect(copy).toMatchObject({
      title: "Sandboxes couldn't complete this request",
      description:
        "Try again. If it keeps happening, contact Future AGI support.",
      retry: true,
    });
    expect(copy.title).not.toMatch(/something went wrong/i);
  });

  // The action is the one the refusal's own words name: the sign-in copy says
  // to try again in a moment, the others to reload the page.
  it.each([
    [{ status: 401, code: null }, "retry"],
    [{ status: 401, code: "assertion_invalid" }, "retry"],
    [{ status: 403, code: "unauthenticated" }, "retry"],
    [{ status: 401, code: "unauthorized" }, "reload"],
    [{ status: 403, code: "role_out_of_date" }, "reload"],
    [{ status: 403, code: "session_exchange_refused" }, "reload"],
  ])("offers %o the %s action", (refusal, action) => {
    const copy = errorCopy({ state: "refused", message: "words", ...refusal });
    expect(Boolean(copy.retry)).toBe(action === "retry");
    expect(Boolean(copy.reload)).toBe(action === "reload");
  });

  it("keeps a coded unavailable answer's own words", () => {
    const message = "Omega is restarting. Try again in a moment.";
    expect(
      errorCopy({
        state: "unavailable",
        status: 503,
        code: "omega_gateway_draining",
        message,
      }),
    ).toMatchObject({
      title: "Omega can't complete this request right now.",
      description: message,
      retry: true,
    });
  });
});

describe("Sandboxes list: search, filters, sorting, ids and times", () => {
  const MIN = 60_000;
  const HOUR = 60 * MIN;
  const ago = (ms) => new Date(Date.now() - ms).toISOString();
  // Listed in this order by the API: neither by name nor by any time.
  const fleet = () => [
    sandboxView({
      id: "sbx_aaaaaaaaaaaaaaaaaaaaaaaa",
      name: "beta-api",
      image: "python:3.12-slim",
      createdAt: ago(3 * HOUR),
      lastActivityAt: ago(10 * MIN),
    }),
    sandboxView({
      id: "sbx_bbbbbbbbbbbbbbbbbbbbbbbb",
      name: "alpha-worker",
      state: "hibernated",
      hibernatesAt: undefined,
      createdAt: ago(HOUR),
      lastActivityAt: ago(2 * HOUR),
    }),
    sandboxView({
      id: "sbx_cccccccccccccccccccccccc",
      name: "gamma-browser",
      state: "recovery_required",
      hibernatesAt: undefined,
      createdAt: ago(48 * HOUR),
      lastActivityAt: ago(MIN),
    }),
  ];
  const renderFleet = (list = fleet()) =>
    renderSandboxes(<SandboxesListPage />, {
      api: fakeApi({
        list: vi.fn(async () => ({
          sandboxes: list,
          caps: CAPS,
          provider: PROVIDER,
        })),
      }),
    });
  // Each row as the letter its id repeats: "a" for sbx_aaaa….
  const idLetter = (row) => row.getAttribute("data-sandbox-id").slice(4, 5);
  const names = () => screen.getAllByTestId("sandbox-row").map(idLetter);
  const search = (value) =>
    fireEvent.change(screen.getByTestId("sandboxes-search"), {
      target: { value },
    });
  const clipboard = Object.getOwnPropertyDescriptor(navigator, "clipboard");
  beforeEach(() => {
    vi.stubGlobal("isSecureContext", true);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    if (clipboard) Object.defineProperty(navigator, "clipboard", clipboard);
    else Reflect.deleteProperty(navigator, "clipboard");
  });

  it("searches by name, id or image and says how many rows it shows", async () => {
    renderFleet();
    await screen.findAllByTestId("sandbox-row");
    expect(screen.getByTestId("sandboxes-count")).toHaveTextContent(
      "3 sandboxes · 1 running",
    );
    search("ALPHA");
    expect(names()).toEqual(["b"]);
    expect(screen.getByTestId("sandboxes-count")).toHaveTextContent(
      "Showing 1 of 3 sandboxes",
    );
    search("cccccccc");
    expect(names()).toEqual(["c"]);
    search("python:3.12");
    expect(names()).toEqual(["a"]);
  });

  it("filters by state with chips, and Clear all brings every row back", async () => {
    renderFleet();
    await screen.findAllByTestId("sandbox-row");
    const running = screen.getByTestId("sandboxes-filter-active");
    expect(running).toHaveTextContent("Running 1");
    expect(
      screen.getByTestId("sandboxes-filter-recovery_required"),
    ).toHaveTextContent("Needs recovery 1");
    expect(running).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(running);
    expect(running).toHaveAttribute("aria-pressed", "true");
    expect(names()).toEqual(["a"]);
    fireEvent.click(screen.getByTestId("sandboxes-filter-hibernated"));
    expect(names()).toEqual(["a", "b"]);
    expect(screen.getByTestId("sandboxes-count")).toHaveTextContent(
      "Showing 2 of 3 sandboxes",
    );
    fireEvent.click(screen.getByTestId("sandboxes-clear-filters"));
    expect(names()).toEqual(["a", "b", "c"]);
    expect(screen.queryByTestId("sandboxes-clear-filters")).toBeNull();
  });

  it("says when nothing matches and offers Clear filters", async () => {
    renderFleet();
    await screen.findAllByTestId("sandbox-row");
    search("no-such-sandbox");
    const empty = screen.getByTestId("sandboxes-no-match");
    expect(empty).toHaveTextContent("No sandboxes match these filters");
    expect(empty).toHaveTextContent(
      "Clear the search and state filters to see all 3 sandboxes again.",
    );
    fireEvent.click(
      within(empty).getByRole("button", { name: "Clear filters" }),
    );
    expect(names()).toEqual(["a", "b", "c"]);
    expect(screen.getByTestId("sandboxes-search")).toHaveValue("");
  });

  it("sorts by name, last activity and created from the column headers", async () => {
    renderFleet();
    await screen.findAllByTestId("sandbox-row");
    // The API's order until a column is chosen.
    expect(names()).toEqual(["a", "b", "c"]);
    const header = (name) => screen.getByRole("columnheader", { name });
    fireEvent.click(screen.getByTestId("sort-name"));
    expect(names()).toEqual(["b", "a", "c"]);
    expect(header("Name")).toHaveAttribute("aria-sort", "ascending");
    fireEvent.click(screen.getByTestId("sort-name"));
    expect(names()).toEqual(["c", "a", "b"]);
    expect(header("Name")).toHaveAttribute("aria-sort", "descending");
    fireEvent.click(screen.getByTestId("sort-lastActivityAt"));
    expect(names()).toEqual(["c", "a", "b"]);
    expect(header("Last activity")).toHaveAttribute("aria-sort", "descending");
    expect(header("Name")).not.toHaveAttribute("aria-sort");
    fireEvent.click(screen.getByTestId("sort-createdAt"));
    expect(names()).toEqual(["b", "a", "c"]);
    fireEvent.click(screen.getByTestId("sort-createdAt"));
    expect(names()).toEqual(["c", "a", "b"]);
    expect(header("Created")).toHaveAttribute("aria-sort", "ascending");
  });

  it("keeps the chosen sort through a search that matches nothing and Clear filters", async () => {
    renderFleet();
    await screen.findAllByTestId("sandbox-row");
    fireEvent.click(screen.getByTestId("sort-name"));
    expect(names()).toEqual(["b", "a", "c"]);
    search("no-such-sandbox");
    const empty = screen.getByTestId("sandboxes-no-match");
    fireEvent.click(
      within(empty).getByRole("button", { name: "Clear filters" }),
    );
    expect(names()).toEqual(["b", "a", "c"]);
    expect(screen.getByRole("columnheader", { name: "Name" })).toHaveAttribute(
      "aria-sort",
      "ascending",
    );
  });

  it("lets the keyboard reach each row's exact times, with the time zone", async () => {
    const user = userEvent.setup();
    renderFleet();
    const [row] = await screen.findAllByTestId("sandbox-row");
    const copy = within(row).getByRole("button", { name: "Copy sandbox id" });
    act(() => copy.focus());
    await user.tab();
    const last = within(row).getByTestId("sandbox-last-activity");
    expect(last).toHaveFocus();
    // The copy button's own tooltip may still be closing.
    const tip = await screen.findByRole("tooltip", { name: /\d{4}/ });
    expect(tip.textContent).toMatch(/(UTC|GMT|[A-Z]{2,5})[+\-:\d]*$/);
    expect(last).toHaveAccessibleDescription(tip.textContent);
    await user.tab();
    expect(within(row).getByTestId("sandbox-created")).toHaveFocus();
    await user.tab();
    expect(
      within(within(row).getByTestId("sandbox-hibernates")).getByText(/^in /),
    ).toHaveFocus();
  });

  it("copies a sandbox's full id from its row without opening the sandbox", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    renderFleet();
    const [row] = await screen.findAllByTestId("sandbox-row");
    const copy = within(row).getByRole("button", { name: "Copy sandbox id" });
    fireEvent.keyDown(copy, { key: "Enter" });
    fireEvent.click(copy);
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith("sbx_aaaaaaaaaaaaaaaaaaaaaaaa"),
    );
    expect(await within(row).findByRole("status")).toHaveTextContent("Copied");
    expect(screen.getByTestId("location").textContent).toBe(
      "/dashboard/sandboxes",
    );
  });

  it("says so when the browser won't copy", async () => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn(async () => Promise.reject(new Error("no"))) },
    });
    renderFleet();
    const [row] = await screen.findAllByTestId("sandbox-row");
    fireEvent.click(
      within(row).getByRole("button", { name: "Copy sandbox id" }),
    );
    expect(await within(row).findByRole("status")).toHaveTextContent(
      "Couldn't copy. This browser blocked the clipboard.",
    );
  });

  it("shows when each sandbox was created, relative, with the exact time and time zone on hover", async () => {
    const list = fleet();
    renderFleet(list);
    const [row] = await screen.findAllByTestId("sandbox-row");
    const created = within(row).getByTestId("sandbox-created");
    expect(created.tagName).toBe("TIME");
    expect(created).toHaveTextContent("3 h ago");
    expect(created).toHaveAttribute("datetime", list[0].createdAt);
    fireEvent.mouseOver(created);
    const tip = await screen.findByRole("tooltip");
    expect(tip.textContent).toMatch(/\d{4}/);
    expect(tip.textContent).toMatch(/(UTC|GMT|[A-Z]{2,5})[+\-:\d]*$/);
  });

  it("shows row-shaped placeholders while the list loads, not a spinner", async () => {
    renderSandboxes(<SandboxesListPage />, {
      api: fakeApi({ list: vi.fn(() => new Promise(() => {})) }),
    });
    const loading = await screen.findByRole("progressbar", {
      name: "Loading sandboxes",
    });
    expect(loading).toHaveAttribute("data-testid", "sandbox-loading");
    expect(loading).toHaveAttribute("aria-busy", "true");
  });

  it("names the exact next action on an empty list", async () => {
    renderFleet([]);
    expect(await screen.findByTestId("sandboxes-empty")).toHaveTextContent(
      "Create one here, or start one from your code with the requests under Code.",
    );
  });
});
