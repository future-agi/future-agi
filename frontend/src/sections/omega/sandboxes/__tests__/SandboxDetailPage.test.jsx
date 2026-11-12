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

import { OmegaError } from "src/sections/omega/shared/omegaStates";

import {
  CAPS,
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
// Which Future AGI this is: hosted ("cloud") or a self-hosted install.
const capabilities = vi.hoisted(() => ({ data: null }));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useCapabilities: () => ({
    data: capabilities.data,
    isLoading: false,
    isError: false,
  }),
}));

const { default: SandboxDetailPage } = await import("../SandboxDetailPage");
const ID = "sbx_0123456789abcdef01234567";
const at = (tab) => `/dashboard/sandboxes/${ID}${tab ? `/${tab}` : ""}`;

beforeEach(() => {
  Object.assign(session, { state: "ready", code: null, message: null });
  capabilities.data = { display_mode: "oss", features: {} };
});

describe("Sandbox detail: header and limits", () => {
  const clipboard = Object.getOwnPropertyDescriptor(navigator, "clipboard");
  afterEach(() => {
    vi.unstubAllGlobals();
    if (clipboard) Object.defineProperty(navigator, "clipboard", clipboard);
    else Reflect.deleteProperty(navigator, "clipboard");
  });

  it("heads the page with the full id, a copy button, and when it was created and updated", async () => {
    vi.stubGlobal("isSecureContext", true);
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    const sandbox = sandboxView();
    renderSandboxes(<SandboxDetailPage />, {
      api: fakeApi({ get: vi.fn(async () => sandbox) }),
      route: at(),
    });
    expect((await screen.findByTestId("sandbox-id")).textContent).toBe(ID);
    fireEvent.click(screen.getByRole("button", { name: "Copy sandbox id" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(ID));
    const created = screen.getByTestId("sandbox-created");
    expect(created).toHaveTextContent("Created 1 h ago");
    expect(created).toHaveAttribute("datetime", sandbox.createdAt);
    expect(screen.getByTestId("sandbox-updated")).toHaveTextContent(
      "Updated 1 min ago",
    );
  });

  it("lets the keyboard reach the exact created and updated times, with the time zone", async () => {
    const user = userEvent.setup();
    renderSandboxes(<SandboxDetailPage />, { route: at() });
    const copy = await screen.findByRole("button", {
      name: "Copy sandbox id",
    });
    act(() => copy.focus());
    await user.tab();
    const created = screen.getByTestId("sandbox-created");
    expect(created).toHaveFocus();
    // The copy button's own tooltip may still be closing.
    const tip = await screen.findByRole("tooltip", { name: /\d{4}/ });
    expect(tip.textContent).toMatch(/(UTC|GMT|[A-Z]{2,5})[+\-:\d]*$/);
    expect(created).toHaveAccessibleDescription(tip.textContent);
    await user.tab();
    expect(screen.getByTestId("sandbox-updated")).toHaveFocus();
  });

  it("states the command time limit from the caps, in words", async () => {
    renderSandboxes(<SandboxDetailPage />, { route: at() });
    expect(
      await screen.findByTestId("overview-command-limit"),
    ).toHaveTextContent("Up to 15 minutes");
    expect(document.body.textContent).not.toMatch(/Self-hosted/);
  });

  it("reads the limit from the caps, not a fixed number", async () => {
    const api = fakeApi({
      list: vi.fn(async () => ({
        sandboxes: [sandboxView()],
        caps: {
          ...CAPS,
          exec: { ...CAPS.exec, maxTimeoutSeconds: 5400 },
        },
        provider: PROVIDER,
      })),
    });
    renderSandboxes(<SandboxDetailPage />, { api, route: at() });
    expect(
      await screen.findByTestId("overview-command-limit"),
    ).toHaveTextContent("Up to 1 hour 30 minutes");
  });

  it("adds on hosted Future AGI that self-hosted installs can allow longer commands", async () => {
    capabilities.data = { display_mode: "cloud", features: {} };
    renderSandboxes(<SandboxDetailPage />, { route: at() });
    const limit = await screen.findByTestId("overview-command-limit");
    expect(limit).toHaveTextContent("Up to 15 minutes");
    expect(limit).toHaveTextContent(
      "Self-hosted installs can allow longer commands.",
    );
    expect(limit).not.toHaveTextContent(/coming soon/i);
  });
});

describe("Sandbox detail: Overview", () => {
  it("shows the isolation statement exactly as the provider metadata states it", async () => {
    const statement =
      "OCI container sharing the container host's Linux kernel; network off; limits 1 CPU, 512 MB memory, 256 processes.";
    const api = fakeApi({
      get: vi.fn(async () =>
        sandboxView({
          provider: { ...PROVIDER, isolationStatement: statement },
        }),
      ),
    });
    renderSandboxes(<SandboxDetailPage />, { api, route: at() });
    expect(
      (await screen.findByTestId("sandbox-isolation-statement")).textContent,
    ).toBe(statement);
    expect(api.get).toHaveBeenCalledWith(ID);
    expect(document.body.textContent).not.toMatch(
      /micro ?vm|safe for untrusted/i,
    );
    expect(screen.getByTestId("overview-network").textContent).toBe("Off");
    expect(screen.getByTestId("overview-exec-count").textContent).toBe("3");
  });

  it("links every run that used the sandbox to its Omega Console thread", async () => {
    const api = fakeApi({
      get: vi.fn(async () =>
        sandboxView({
          usedBy: [
            {
              runId: "conv_42",
              taskId: "task_1",
              agentId: "agent_7",
              title: "Nightly import",
            },
          ],
        }),
      ),
    });
    renderSandboxes(<SandboxDetailPage />, { api, route: at() });
    const link = await screen.findByTestId("used-by-run");
    expect(link).toHaveTextContent("Nightly import");
    expect(link).toHaveAttribute("href", "/dashboard/console/chat/conv_42");
  });

  it("asks once more when one assertion was not accepted (401), never for a 403", async () => {
    let calls = 0;
    const api = fakeApi({
      get: vi.fn(async () => {
        calls += 1;
        if (calls === 1)
          throw new OmegaError({
            state: "refused",
            status: 401,
            code: null,
            message: null,
          });
        return sandboxView();
      }),
    });
    renderSandboxes(<SandboxDetailPage />, { api, route: at() });
    expect(
      await screen.findByTestId(
        "sandbox-isolation-statement",
        {},
        { timeout: 4000 },
      ),
    ).toBeInTheDocument();
    expect(api.get).toHaveBeenCalledTimes(2);
  });

  it("says 'not found' for an id outside this workspace, without detail", async () => {
    const api = fakeApi({
      get: vi.fn(async () => {
        throw new OmegaError({
          state: "not-found",
          status: 404,
          code: "not_found",
          message: "Sandbox not found.",
        });
      }),
    });
    renderSandboxes(<SandboxDetailPage />, { api, route: at() });
    expect(await screen.findByText("Sandbox not found")).toBeInTheDocument();
    expect(screen.queryByTestId("sandbox-isolation-statement")).toBeNull();
    expect(screen.queryByTestId("sandbox-hibernate")).toBeNull();
  });

  it("says 'not found' for an id EE could never issue, without asking EE", async () => {
    // Found in the live walk: an id copied with the page's "…" shortening was
    // percent-encoded, EE refused the path before any lookup, and the page said
    // Sandboxes "aren't reachable from Future AGI", which was false.
    for (const bad of ["sbx_a48116bf…0fc9", "sbx 1", ".."]) {
      const api = fakeApi();
      const view = renderSandboxes(<SandboxDetailPage />, {
        api,
        route: `/dashboard/sandboxes/${encodeURIComponent(bad)}`,
      });
      expect(await screen.findByText("Sandbox not found")).toBeInTheDocument();
      expect(screen.queryByText(/aren't reachable/u)).toBeNull();
      expect(api.get).not.toHaveBeenCalled();
      view.unmount();
    }
  });

  it("hibernates an active sandbox and offers Resume once it is hibernated", async () => {
    let state = "active";
    const api = fakeApi({
      get: vi.fn(async () =>
        sandboxView({
          state,
          hibernatesAt:
            state === "active"
              ? new Date(Date.now() + 60_000).toISOString()
              : undefined,
        }),
      ),
      hibernate: vi.fn(async () => {
        state = "hibernated";
        return sandboxView({ state });
      }),
    });
    renderSandboxes(<SandboxDetailPage />, { api, route: at() });
    fireEvent.click(await screen.findByTestId("sandbox-hibernate"));
    await waitFor(() => expect(api.hibernate).toHaveBeenCalledWith(ID));
    expect(await screen.findByTestId("sandbox-resume")).toBeInTheDocument();
    expect(screen.getByTestId("sandbox-state")).toHaveAttribute(
      "data-state",
      "hibernated",
    );
  });

  it("gives an agent run's sandbox no lifecycle actions", async () => {
    const api = fakeApi({
      get: vi.fn(async () =>
        sandboxView({
          readOnly: true,
          origin: "agent_run",
          idleTtlSeconds: null,
        }),
      ),
    });
    renderSandboxes(<SandboxDetailPage />, { api, route: at() });
    expect(
      await screen.findByText("Agent run · read-only"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("sandbox-hibernate")).toBeNull();
    expect(screen.queryByTestId("sandbox-delete")).toBeNull();
  });
});

describe("Sandbox detail: Run", () => {
  it("streams a command's output and cancels it by its exec id", async () => {
    let handlers;
    let finish;
    const api = fakeApi({
      exec: vi.fn((_id, _input, options) => {
        handlers = options;
        return {
          close: vi.fn(),
          done: new Promise((resolve) => {
            finish = resolve;
          }),
        };
      }),
    });
    renderSandboxes(<SandboxDetailPage />, { api, route: at("run") });
    fireEvent.change(await screen.findByTestId("run-command"), {
      target: { value: "echo nonce-81f2" },
    });
    fireEvent.click(screen.getByTestId("run-submit"));
    expect(api.exec).toHaveBeenCalledWith(
      ID,
      expect.objectContaining({ command: "echo nonce-81f2" }),
      expect.any(Object),
    );
    act(() =>
      handlers.onEvent({
        event: "started",
        json: { execId: "exec_0123456789abcdef01234567" },
      }),
    );
    act(() =>
      handlers.onEvent({ event: "stdout", json: { data: "nonce-81f2\n" } }),
    );
    act(() =>
      handlers.onEvent({ event: "stderr", json: { data: "warning\n" } }),
    );
    const output = screen.getByTestId("run-output");
    expect(output).toHaveTextContent("nonce-81f2");
    expect(within(output).getByText("warning")).toHaveAttribute(
      "data-stream",
      "stderr",
    );
    expect(screen.getByTestId("run-status")).toHaveTextContent("Running…");
    fireEvent.click(screen.getByTestId("run-cancel"));
    await waitFor(() =>
      expect(api.cancelExec).toHaveBeenCalledWith(
        ID,
        "exec_0123456789abcdef01234567",
      ),
    );
    await act(async () => {
      handlers.onEvent({
        event: "exit",
        json: {
          exitCode: null,
          cancelled: true,
          timedOut: false,
          truncated: false,
          durationMs: 900,
        },
      });
      finish({ ended: true });
    });
    expect(screen.getByTestId("run-status")).toHaveTextContent(
      "Cancelled · 900 ms",
    );
    expect(screen.getByTestId("run-submit")).toBeInTheDocument();
  });

  it("shows why a command was refused before it started", async () => {
    const api = fakeApi({
      exec: vi.fn(() => ({
        close: vi.fn(),
        done: Promise.resolve({
          ok: false,
          state: "error",
          status: 400,
          message: '"env.OMEGA_X" is reserved.',
        }),
      })),
    });
    renderSandboxes(<SandboxDetailPage />, { api, route: at("run") });
    fireEvent.change(await screen.findByTestId("run-command"), {
      target: { value: "env" },
    });
    fireEvent.keyDown(screen.getByTestId("run-command"), { key: "Enter" });
    expect(
      await screen.findByText('"env.OMEGA_X" is reserved.'),
    ).toBeInTheDocument();
  });

  it("asks to resume a hibernated sandbox instead of offering a command box", async () => {
    const api = fakeApi({
      get: vi.fn(async () =>
        sandboxView({ state: "hibernated", hibernatesAt: undefined }),
      ),
    });
    renderSandboxes(<SandboxDetailPage />, { api, route: at("run") });
    expect(
      await screen.findByText("This sandbox is hibernated"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("run-command")).toBeNull();
  });
});

describe("Sandbox detail: tabs", () => {
  it("routes each tab to /dashboard/sandboxes/:id/:tab", async () => {
    renderSandboxes(<SandboxDetailPage />, { route: at() });
    fireEvent.click(await screen.findByTestId("tab-snapshots"));
    expect(screen.getByTestId("location").textContent).toBe(at("snapshots"));
    expect(await screen.findByTestId("snapshot-statement")).toHaveTextContent(
      PROVIDER.snapshotStatement,
    );
    // No preview-links tab: EE's sandbox API publishes no ports.
    expect(
      screen.getAllByRole("tab").map((tab) => tab.getAttribute("data-testid")),
    ).toEqual([
      "tab-overview",
      "tab-files",
      "tab-run",
      "tab-snapshots",
      "tab-activity",
    ]);
  });

  it("draws no Used by panel when no agent run used the sandbox", async () => {
    renderSandboxes(<SandboxDetailPage />, { route: at() });
    expect(await screen.findByTestId("panel-overview")).toBeInTheDocument();
    expect(
      await screen.findByTestId("sandbox-isolation-statement"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("overview-used-by")).toBeNull();
    expect(
      screen.queryByText("No agent runs have used this sandbox."),
    ).toBeNull();
  });

  it("calls a mistyped sandbox URL not found, not out of reach", async () => {
    for (const refusal of [
      { code: "host_delegation_scope_denied", reason: "route_not_in_manifest" },
      { code: "route_not_in_manifest", reason: null },
    ]) {
      const api = fakeApi({
        get: vi.fn(async () => {
          throw new OmegaError({ state: "refused", status: 403, ...refusal });
        }),
      });
      const { unmount } = renderSandboxes(<SandboxDetailPage />, {
        api,
        route: "/dashboard/sandboxes/not-an-id",
      });
      const state = await screen.findByTestId("sandbox-error-state");
      expect(state).toHaveTextContent(/not found/i);
      expect(state).not.toHaveTextContent(/can't be opened/i);
      unmount();
    }
  });

  it("shows a deleted sandbox's record and activity only, never 'Files are kept'", async () => {
    const api = fakeApi({
      get: vi.fn(async () => sandboxView({ state: "destroyed" })),
      activity: vi.fn(async () => [
        {
          at: "2026-10-19T10:00:00Z",
          verb: "sandbox.hibernate",
          outcome: "ok",
          actor: "system:idle-reaper",
          detail: {},
        },
        {
          at: "2026-10-19T11:00:00Z",
          verb: "sandbox.delete",
          outcome: "ok",
          actor: "acc_23340000000000000114",
          detail: {},
        },
      ]),
    });
    for (const tab of ["files", "run", "snapshots"]) {
      const { unmount } = renderSandboxes(<SandboxDetailPage />, {
        api,
        route: at(tab),
      });
      await waitFor(() =>
        expect(screen.getByTestId("location").textContent).toBe(at("activity")),
      );
      expect(await screen.findByTestId("panel-activity")).toBeInTheDocument();
      expect(
        screen.getAllByRole("tab").map((t) => t.getAttribute("data-testid")),
      ).toEqual(["tab-overview", "tab-activity"]);
      expect(document.body.textContent).not.toMatch(/Files are kept/);
      unmount();
    }
    renderSandboxes(<SandboxDetailPage />, { api, route: at("activity") });
    const panel = await screen.findByTestId("panel-activity");
    await waitFor(() => expect(panel).toHaveTextContent("Automatic (idle)"));
    expect(panel).not.toHaveTextContent(/system:|reaper/);
  });

  it("names where it runs without the internal binding id", async () => {
    renderSandboxes(<SandboxDetailPage />, { route: at() });
    const caption = await screen.findByTestId("overview-isolation");
    expect(caption).toHaveTextContent("Container");
    fireEvent.mouseOver(screen.getByTestId("sandbox-runtime"));
    await act(() => new Promise((resolve) => setTimeout(resolve, 50)));
    expect(document.body.textContent).not.toMatch(/binding|colima/i);
  });

  it("tells a member on a teammate's sandbox link that it is private to its creator", async () => {
    const api = fakeApi({
      get: vi.fn(async () => {
        throw new OmegaError({
          state: "refused",
          status: 403,
          code: "role_forbidden",
          reason: "not_creator",
          title: "This sandbox is private to its creator",
          message:
            "Only the member who created this sandbox, and workspace owners and admins, can open it.",
        });
      }),
    });
    renderSandboxes(<SandboxDetailPage />, { api, route: at() });
    const state = await screen.findByTestId("sandbox-error-state");
    expect(state).toHaveTextContent("This sandbox is private to its creator");
    expect(state).not.toHaveTextContent("You don't have access");
  });

  // The sign-in copy says to try again in a moment: its button asks again.
  async function expectTryAgainAsksAgain(state, api) {
    expect(within(state).queryByRole("button", { name: "Reload" })).toBeNull();
    const before = api.get.mock.calls.length;
    fireEvent.click(within(state).getByRole("button", { name: "Try again" }));
    await waitFor(() =>
      expect(api.get.mock.calls.length).toBeGreaterThan(before),
    );
  }

  it("calls a lasting 401 a sign-in problem with Try again, not the role", async () => {
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
    renderSandboxes(<SandboxDetailPage />, { api, route: at() });
    const state = await screen.findByTestId(
      "sandbox-error-state",
      {},
      { timeout: 5000 },
    );
    expect(state).toHaveTextContent("Omega couldn't confirm your sign-in");
    expect(state).not.toHaveTextContent(/role/);
    await expectTryAgainAsksAgain(state, api);
  });

  // Staging walk (head-72): the gateway check failed, so the page blamed Omega
  // without asking EE. The sandbox's own call says sandboxes are off.
  it("asks EE when only the gateway check failed, and says sandboxes are off on this deployment", async () => {
    Object.assign(session, {
      state: "unavailable",
      code: "gateway_unavailable",
      message: "Omega isn't responding right now. Try again in a moment.",
    });
    const off = async () => {
      throw new OmegaError({
        state: "unavailable",
        status: 503,
        code: "sandboxes_unavailable",
        message: "Sandboxes are not enabled on this deployment.",
      });
    };
    const api = fakeApi({ get: vi.fn(off), list: vi.fn(off) });
    renderSandboxes(<SandboxDetailPage />, { api, route: at() });
    const state = await screen.findByTestId("sandbox-error-state");
    expect(state).toHaveTextContent(
      "Sandboxes are not available on this deployment.",
    );
    expect(state).toHaveTextContent(
      "An administrator enables a sandbox host for this deployment.",
    );
    expect(state).not.toHaveTextContent(/Omega/);
    expect(within(state).queryByRole("button")).toBeNull();
    expect(api.get).toHaveBeenCalledWith(ID);
  });

  it("offers Try again, not Reload, when EE's unauthenticated refusal comes as a 403", async () => {
    const api = fakeApi({
      get: vi.fn(async () => {
        throw new OmegaError({
          state: "refused",
          status: 403,
          code: "unauthenticated",
          title: "Omega couldn't confirm your sign-in",
          message:
            "Future AGI couldn't confirm your sign-in with Omega. Try again in a moment. If it keeps happening, contact Future AGI support.",
        });
      }),
    });
    renderSandboxes(<SandboxDetailPage />, { api, route: at() });
    const state = await screen.findByTestId("sandbox-error-state");
    expect(state).toHaveTextContent("Omega couldn't confirm your sign-in");
    expect(state).toHaveTextContent("Try again in a moment.");
    await expectTryAgainAsksAgain(state, api);
  });

  it("sends an old /services link, or any unknown tab, to the sandbox itself", async () => {
    for (const tab of ["services", "preview", "SERVICES"]) {
      const { unmount } = renderSandboxes(<SandboxDetailPage />, {
        route: at(tab),
      });
      await waitFor(() =>
        expect(screen.getByTestId("location").textContent).toBe(at()),
      );
      expect(await screen.findByTestId("panel-overview")).toBeInTheDocument();
      expect(screen.queryByTestId("services-unavailable")).toBeNull();
      unmount();
    }
  });
});
