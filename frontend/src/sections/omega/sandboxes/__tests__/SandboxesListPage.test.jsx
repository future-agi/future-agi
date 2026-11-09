import React from "react";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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

const { default: SandboxesListPage } = await import("../SandboxesListPage");

beforeEach(() => {
  Object.assign(session, { state: "ready", code: null, message: null });
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
      "1 visible sandboxes running; workspace limit 10",
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
      "1 visible sandboxes running; workspace limit 10, plus 1 visible agent run",
    ],
    [
      2,
      "1 visible sandboxes running; workspace limit 10, plus 2 visible agent runs",
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

  it("says why sandboxes are off, in the server's own words", async () => {
    const reason =
      "Sandboxes are off on this deployment. An operator enables them with OMEGA_WORKSPACE_SANDBOXES=1.";
    const api = fakeApi({
      list: vi.fn(async () => {
        throw new OmegaError({
          state: "unavailable",
          status: 503,
          code: "sandboxes_unavailable",
          message: reason,
        });
      }),
    });
    renderSandboxes(<SandboxesListPage />, { api });
    expect(
      await screen.findByText("Sandboxes aren't available right now."),
    ).toBeInTheDocument();
    expect(screen.getByText(reason)).toBeInTheDocument();
    expect(screen.queryByTestId("sandbox-row")).toBeNull();
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
      await screen.findByText("You don't have access to this sandbox action."),
    ).toBeInTheDocument();
    expect(screen.getByText("Viewers cannot use Omega.")).toBeInTheDocument();
    await waitFor(() => expect(api.list).not.toHaveBeenCalled());
  });
});
