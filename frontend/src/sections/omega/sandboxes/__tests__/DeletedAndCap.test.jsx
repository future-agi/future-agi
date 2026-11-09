import React from "react";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  CAPS,
  CAPS_V2,
  fakeApi,
  renderSandboxes,
  sandboxView,
  settingsView,
} from "./helpers";
import { session, workspace } from "./session-mock";

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));
vi.mock("src/sections/omega/shared", async (importOriginal) => ({
  ...(await importOriginal()),
  useOmegaSession: () => session,
}));

const { default: SandboxDetailPage } = await import("../SandboxDetailPage");
const { default: SandboxesListPage } = await import("../SandboxesListPage");
const { default: FilesTab } = await import("../detail/FilesTab");
const { default: RunTab } = await import("../detail/RunTab");
const ID = "sbx_0123456789abcdef01234567";
const at = (tab) => `/dashboard/sandboxes/${ID}${tab ? `/${tab}` : ""}`;

beforeEach(() => {
  Object.assign(session, { state: "ready", code: null, message: null });
});

const deletedAt = new Date(Date.now() - 5 * 60_000).toISOString();
const deleted = () =>
  fakeApi({
    get: vi.fn(async () =>
      sandboxView({ state: "destroyed", template: "base", size: "small" }),
    ),
    activity: vi.fn(async () => [
      {
        at: deletedAt,
        sandboxId: ID,
        verb: "sandbox.destroy",
        actor: "acc_23340000000000000114",
        outcome: "ok",
      },
    ]),
  });

describe("A deleted sandbox", () => {
  it("shows what it was and when it went, and nothing that says it still runs", async () => {
    renderSandboxes(<SandboxDetailPage />, { api: deleted(), route: at() });
    expect(
      await screen.findByTestId("sandbox-deleted-overview"),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId("sandbox-deleted-title")).toHaveTextContent(
        "This sandbox was deleted 5 min ago",
      ),
    );
    const text = document.body.textContent;
    expect(text).toMatch(/Its container and files were removed/);
    expect(text).not.toMatch(/Files are kept|Hibernates|Idle timeout|Change/);
    expect(screen.queryByTestId("sandbox-isolation-statement")).toBeNull();
    expect(screen.queryByTestId("sandbox-hibernate")).toBeNull();
    expect(screen.queryByTestId("sandbox-delete")).toBeNull();
    fireEvent.click(screen.getByTestId("sandbox-deleted-activity"));
    expect(screen.getByTestId("location").textContent).toBe(at("activity"));
  });

  it("never says its files are kept, on Files or on Run", async () => {
    // The tabs themselves, whatever route reaches them (HIDE-UNFINISHED-FA
    // sends a deleted sandbox's Files and Run URLs to Activity).
    const sandbox = sandboxView({ state: "destroyed" });
    for (const Tab of [FilesTab, RunTab]) {
      const { unmount } = renderSandboxes(<Tab sandbox={sandbox} />, {
        route: at(),
      });
      expect(
        await screen.findByTestId("sandbox-deleted-panel"),
      ).toHaveTextContent("This sandbox was deleted");
      expect(document.body.textContent).not.toMatch(/Files are kept|Resume/);
      expect(screen.queryByTestId("run-command")).toBeNull();
      fireEvent.click(screen.getByTestId("sandbox-deleted-activity"));
      expect(screen.getByTestId("location").textContent).toBe(at("activity"));
      unmount();
    }
  });
});

const active = (n) =>
  Array.from({ length: n }, (_, i) =>
    sandboxView({ id: `sbx_${String(i).padStart(24, "0")}`, name: `s${i}` }),
  );

describe("At the running cap", () => {
  it("says why New sandbox is off and what to do, with the limit's setting for an admin", async () => {
    const api = fakeApi({
      list: vi.fn(async () => ({
        sandboxes: active(4),
        caps: { ...CAPS_V2, maxActiveSandboxes: 4 },
      })),
    });
    renderSandboxes(<SandboxesListPage />, { api });
    const notice = await screen.findByTestId("sandboxes-at-cap");
    expect(notice).toHaveTextContent(
      "4 visible sandboxes are running, at the workspace limit of 4. Hibernate or delete one before trying to start another.",
    );
    const button = screen.getByTestId("new-sandbox");
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-describedby", "sandboxes-at-cap");
    fireEvent.click(screen.getByTestId("sandboxes-at-cap-settings"));
    expect(screen.getByTestId("location").textContent).toBe(
      "/dashboard/settings/sandboxes",
    );
  });

  it("tells a member who can review the limit", async () => {
    const api = fakeApi({
      list: vi.fn(async () => ({
        sandboxes: active(2),
        caps: { ...CAPS, maxActiveSandboxes: 2, settings: { canEdit: false } },
      })),
    });
    renderSandboxes(<SandboxesListPage />, { api });
    const notice = await screen.findByTestId("sandboxes-at-cap");
    expect(notice).toHaveTextContent(
      "A workspace admin can review the limit in Settings.",
    );
    expect(screen.queryByTestId("sandboxes-at-cap-settings")).toBeNull();
  });

  it("counts hibernated sandboxes out, as EE does", async () => {
    const api = fakeApi({
      list: vi.fn(async () => ({
        sandboxes: [
          ...active(1),
          sandboxView({
            id: "sbx_h00000000000000000000000",
            state: "hibernated",
          }),
        ],
        caps: { ...CAPS, maxActiveSandboxes: 2 },
      })),
    });
    renderSandboxes(<SandboxesListPage />, { api });
    expect(await screen.findByTestId("new-sandbox")).not.toBeDisabled();
    expect(screen.queryByTestId("sandboxes-at-cap")).toBeNull();
  });
});

describe("Custom limits below a template's minimum", () => {
  it("are refused as they are typed, before a create EE would refuse with 400", async () => {
    const api = fakeApi({
      list: vi.fn(async () => ({ sandboxes: active(1), caps: CAPS_V2 })),
    });
    renderSandboxes(<SandboxesListPage />, { api });
    fireEvent.click(await screen.findByTestId("new-sandbox"));
    fireEvent.click(screen.getByTestId("create-size-custom"));
    fireEvent.change(screen.getByTestId("create-cpus"), {
      target: { value: "0.3" },
    });
    fireEvent.change(screen.getByTestId("create-memoryMb"), {
      target: { value: "512" },
    });
    // Live, without a submit: Base needs at least Small (0.5 CPU, 1024 MB).
    expect(screen.getByText("Base needs at least 0.5")).toBeInTheDocument();
    expect(screen.getByText("Base needs at least 1024")).toBeInTheDocument();
    expect(screen.getByTestId("create-summary")).toHaveTextContent(
      "Fix the highlighted fields to create this sandbox.",
    );
    fireEvent.change(screen.getByTestId("create-cpus"), {
      target: { value: "5" },
    });
    expect(screen.getByText("Between 0.1 and 2")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("create-submit"));
    expect(api.create).not.toHaveBeenCalled();
    fireEvent.change(screen.getByTestId("create-cpus"), {
      target: { value: "1" },
    });
    fireEvent.change(screen.getByTestId("create-memoryMb"), {
      target: { value: "1024" },
    });
    expect(screen.getByTestId("create-summary")).toHaveTextContent(
      "1 vCPU · 1024 MiB",
    );
    fireEvent.click(screen.getByTestId("create-submit"));
    await waitFor(() => expect(api.create).toHaveBeenCalledTimes(1));
    expect(api.create.mock.calls[0][0]).toMatchObject({
      template: "base",
      limits: { cpus: 1, memoryMb: 1024 },
    });
  });
});

describe("EE's reasons on Settings → Sandboxes", () => {
  it("never show the operator's build script or a refusal meant for the operator", async () => {
    const { default: SandboxSettingsPage } = await import(
      "../settings/SandboxSettingsPage"
    );
    const api = fakeApi({
      settings: vi.fn(async () =>
        settingsView({
          caps: {
            ...CAPS_V2,
            createRefusal: {
              field: "template",
              reason:
                "None of the templates this deployment lists is offered here, and no other image is. An operator can build the template images or offer another image.",
            },
          },
        }),
      ),
    });
    renderSandboxes(<SandboxSettingsPage />, { api });
    await screen.findByTestId("sandbox-settings");
    expect(screen.getByTestId("sandbox-settings-refusal")).toHaveTextContent(
      "Nobody in this workspace can create a sandbox right now. None of the templates this deployment lists is offered here, and no other image is.",
    );
    expect(document.body).toHaveTextContent(
      "Not available on this deployment right now.",
    );
    // EE's own operator sentences; the page's header wording is HIDE-UNFINISHED-FA's (F16).
    expect(document.body.textContent).not.toMatch(
      /An operator (can|builds)|build\.mjs|\byet\b/i,
    );
  });
});

describe("Activity actors", () => {
  it("never show EE's account ids or system actor names", async () => {
    const api = fakeApi({
      activity: vi.fn(async () => [
        {
          at: new Date().toISOString(),
          sandboxId: ID,
          verb: "sandbox.exec",
          actor: "acc_23340000000000000114",
          outcome: "ok",
          detail: { command: "ls", exitCode: 0, durationMs: 12 },
        },
        {
          at: new Date().toISOString(),
          sandboxId: ID,
          verb: "sandbox.exec",
          actor: "acc_23340000000000000114",
          outcome: "timed_out",
          detail: { command: "sleep 99", exitCode: 143, durationMs: 60_000 },
        },
        {
          at: new Date().toISOString(),
          sandboxId: ID,
          verb: "sandbox.hibernate",
          actor: "system:idle-reaper",
          outcome: "ok",
        },
      ]),
    });
    renderSandboxes(<SandboxDetailPage />, { api, route: at("activity") });
    const list = await screen.findByTestId("activity-list");
    await waitFor(() => expect(list).toHaveTextContent("Workspace member"));
    expect(list).toHaveTextContent("timed out · Workspace member");
    expect(list).toHaveTextContent("Automatic (idle)");
    expect(list.textContent).not.toMatch(/acc_|system:|reaper/);
  });
});

describe("A network-on start EE refuses", () => {
  it("says network-on cannot start here, not that sandboxes are off", async () => {
    const { OmegaError, OMEGA_STATE } = await import(
      "src/sections/omega/shared/omegaStates"
    );
    const refusal = new OmegaError({
      state: OMEGA_STATE.UNAVAILABLE,
      status: 503,
      code: "sandboxes_unavailable",
      reason: "egress_firewall_missing",
      message: "Sandboxes aren't turned on for this workspace.",
    });
    const api = fakeApi({
      list: vi.fn(async () => ({ sandboxes: active(1), caps: CAPS_V2 })),
      create: vi.fn(async () => {
        throw refusal;
      }),
    });
    renderSandboxes(<SandboxesListPage />, { api });
    fireEvent.click(await screen.findByTestId("new-sandbox"));
    fireEvent.click(screen.getByTestId("create-network-toggle"));
    fireEvent.click(screen.getByTestId("create-submit"));
    expect(await screen.findByTestId("create-error")).toHaveTextContent(
      "Sandboxes with the network on can't start on this deployment right now, so nothing was started. Sandboxes with the network off are not affected.",
    );
    expect(document.body.textContent).not.toMatch(/aren't turned on/);
  });

  it("words EE's gVisor refusal (runsc_required) the same way, without its operator sentence", async () => {
    const { OmegaError, OMEGA_STATE } = await import(
      "src/sections/omega/shared/omegaStates"
    );
    const refusal = new OmegaError({
      state: OMEGA_STATE.UNAVAILABLE,
      status: 503,
      code: "sandboxes_unavailable",
      reason: "runsc_required",
      message:
        "This sandbox has network on, and network-on sandboxes run only under the gVisor runtime (runsc), which this sandbox host does not have, so nothing was started. Sandboxes with network off are not affected; an operator can install gVisor on the sandbox host.",
    });
    const api = fakeApi({
      list: vi.fn(async () => ({ sandboxes: active(1), caps: CAPS_V2 })),
      create: vi.fn(async () => {
        throw refusal;
      }),
    });
    renderSandboxes(<SandboxesListPage />, { api });
    fireEvent.click(await screen.findByTestId("new-sandbox"));
    fireEvent.click(screen.getByTestId("create-network-toggle"));
    fireEvent.click(screen.getByTestId("create-submit"));
    expect(await screen.findByTestId("create-error")).toHaveTextContent(
      "Sandboxes with the network on can't start on this deployment right now, so nothing was started. Sandboxes with the network off are not affected.",
    );
    expect(document.body.textContent).not.toMatch(/operator|runsc/);
  });
});

describe("A deleted sandbox's snapshots", () => {
  it("can still be restored (EE keeps them), and taking one is off with the reason", async () => {
    const { default: SnapshotsTab } = await import("../detail/SnapshotsTab");
    const api = fakeApi({
      snapshots: vi.fn(async () => [
        {
          id: "snap_0001",
          sandboxId: ID,
          createdAt: new Date().toISOString(),
          kind: "files",
          statement: "Files only.",
          contentAddress: { artifactId: "art_1", sha256: "a".repeat(64) },
        },
      ]),
    });
    renderSandboxes(
      <SnapshotsTab sandbox={sandboxView({ state: "destroyed" })} />,
      { api, route: at("snapshots") },
    );
    expect(await screen.findByTestId("snapshot-restore")).not.toBeDisabled();
    expect(screen.getByTestId("snapshot-take")).toBeDisabled();
    fireEvent.mouseOver(screen.getByTestId("snapshot-take").parentElement);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "A deleted sandbox can't take snapshots.",
    );
  });
});
