import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import RunRowMenu from "../RunRowMenu";
import RunRowMenuHost from "../RunRowMenuHost";

const { simulateUpload } = vi.hoisted(() => ({ simulateUpload: vi.fn() }));
vi.mock("../cellContext", () => ({ useCell: (props) => props }));
vi.mock("src/sections/common/Runs/useRunActions", () => ({
  default: () => ({ simulateUpload }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
const run = {
  run_id: "waiting",
  label: "#6A",
  experiment_id: "e6",
  target_type: "external",
  status: "WaitingForResults",
};
const context = {
  datasetId: "ds",
  runsPerExperiment: { e6: 1 },
  canEdit: true,
  canRun: true,
};
const menu = {
  group: false,
  runIds: [run.run_id],
  position: { top: 20, left: 20 },
};

describe("leaderboard row menu across disposable grid cells", () => {
  it("sends IDs and a stable position to the grid host, not a disposable anchor", () => {
    const openRowMenu = vi.fn();
    render(
      <RunRowMenu
        node={{ group: false }}
        data={run}
        context={{ ...context, openRowMenu }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "#6A actions" }));
    expect(openRowMenu).toHaveBeenCalledWith({
      group: false,
      experimentId: "e6",
      runIds: ["waiting"],
      position: { top: 0, left: 0 },
    });
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("keeps the same menu DOM across polls and invokes the real shared action", () => {
    const onClose = vi.fn();
    const { rerender } = render(
      <RunRowMenuHost
        menu={menu}
        rows={[run]}
        context={context}
        onClose={onClose}
      />,
    );
    const item = screen.getByRole("menuitem", { name: /Simulate the upload/ });
    rerender(
      <RunRowMenuHost
        menu={{ ...menu }}
        rows={[{ ...run, updated_at: "2026-10-25T08:00:00Z" }]}
        context={{ ...context }}
        onClose={onClose}
      />,
    );
    expect(screen.getByRole("menuitem", { name: /Simulate the upload/ })).toBe(
      item,
    );
    fireEvent.click(item);
    expect(simulateUpload).toHaveBeenCalledOnce();
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("updates permission and run-status gates while the menu is open", () => {
    const onClose = vi.fn();
    const { rerender } = render(
      <RunRowMenuHost
        menu={menu}
        rows={[run]}
        context={context}
        onClose={onClose}
      />,
    );
    rerender(
      <RunRowMenuHost
        menu={menu}
        rows={[run]}
        context={{ ...context, canEdit: false, canRun: false }}
        onClose={onClose}
      />,
    );
    expect(
      screen.getByRole("menuitem", { name: /Simulate the upload/ }),
    ).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("menuitem", { name: "Edit…" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    rerender(
      <RunRowMenuHost
        menu={menu}
        rows={[{ ...run, status: "Completed" }]}
        context={context}
        onClose={onClose}
      />,
    );
    expect(
      screen.queryByRole("menuitem", { name: /Simulate the upload/ }),
    ).toBeNull();
  });

  it("refreshes experiment-group membership and closes if grouping changes", () => {
    const onClose = vi.fn();
    const groupMenu = { ...menu, group: true, experimentId: "e6" };
    const { rerender } = render(
      <RunRowMenuHost
        menu={groupMenu}
        rows={[run]}
        context={{ ...context, group: "experiment" }}
        onClose={onClose}
      />,
    );
    rerender(
      <RunRowMenuHost
        menu={groupMenu}
        rows={[run, { ...run, run_id: "second" }]}
        context={{ ...context, group: "experiment" }}
        onClose={onClose}
      />,
    );
    expect(
      screen.getByRole("menuitem", { name: "Compare 2 runs" }),
    ).toBeInTheDocument();
    rerender(
      <RunRowMenuHost
        menu={groupMenu}
        rows={[run]}
        context={{ ...context, group: "none" }}
        onClose={onClose}
      />,
    );
    expect(screen.queryByRole("menu")).toBeNull();
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("closes when its run disappears instead of retaining a stale action", () => {
    const onClose = vi.fn();
    const { rerender } = render(
      <RunRowMenuHost
        menu={menu}
        rows={[run]}
        context={context}
        onClose={onClose}
      />,
    );
    rerender(
      <RunRowMenuHost
        menu={menu}
        rows={[]}
        context={context}
        onClose={onClose}
      />,
    );
    expect(screen.queryByRole("menu")).toBeNull();
    expect(onClose).toHaveBeenCalledOnce();
  });
});
