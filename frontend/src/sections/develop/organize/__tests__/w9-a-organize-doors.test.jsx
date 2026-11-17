// @vitest-environment jsdom
/* eslint-disable react/prop-types */
// W9 A1, A6, A7 (W9-SCOPE §5): the organise doors. The selection bar shows
// `Organize ▾` only when the host passes it; the menu opens each dialog on
// the selected datasets; ⌘K registers `Set metadata…`, `Move to folder…`
// and `Attach to agents…` for the selection or the active row; read-only
// people get a disabled button with the reason and no runnable command.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const put = vi.fn();
const get = vi.fn();
vi.mock("src/utils/axios", () => ({
  default: { put: (...a) => put(...a), get: (...a) => get(...a) },
  endpoints: {
    develop: { updateDataset: (id) => `/edit/${id}/` },
    agentPlayground: { listGraphs: "/graphs/" },
  },
}));
const notify = vi.fn(() => ({ undone: Promise.resolve(false) }));
vi.mock("src/sections/common/ux/toast", () => ({
  notify: (...a) => notify(...a),
  default: (...a) => notify(...a),
}));

const {
  default: useDatasetOrganize,
  organizeTargets,
  showOrganizeToast,
} = await import("../useDatasetOrganize");
const { default: ListSelectionBar } = await import("../../ListSelectionBar");
const { useCommandStore } = await import(
  "src/sections/common/CommandPalette/commandRegistry"
);

const GS = {
  id: "gs",
  name: "Golden",
  metadata: {},
  folder: "",
  agent_ids: [],
};
const RP = {
  id: "rp",
  name: "Refund policy QA",
  metadata: {},
  folder: "support",
  agent_ids: [],
};

function Host({ selected, active = null, canEdit = true }) {
  const org = useDatasetOrganize({
    selected,
    active,
    canEdit,
    folders: [{ path: "support", count: 1, total: 1 }],
  });
  return (
    <>
      <ListSelectionBar
        count={selected.length}
        canCreate
        canDelete
        organize={org.menu}
        onClear={() => {}}
      />
      {org.dialogs}
    </>
  );
}
const wrap = (ui) =>
  render(
    <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>,
  );
const commands = () =>
  useCommandStore.getState().sources["datasets.organize"] ?? [];

beforeEach(() => {
  put.mockReset();
  get.mockReset();
  notify.mockClear();
  get.mockResolvedValue({ data: { result: { graphs: [], metadata: {} } } });
});
afterEach(cleanup);

describe("ListSelectionBar organise slot", () => {
  it("renders nothing extra without the prop", () => {
    wrap(<ListSelectionBar count={2} canCreate canDelete onClear={() => {}} />);
    expect(screen.queryByRole("button", { name: /Organize/ })).toBeNull();
    expect(screen.getByText("2 selected")).toBeTruthy();
  });
});

describe("useDatasetOrganize", () => {
  it("opens each dialog on the selected datasets from the menu", () => {
    wrap(<Host selected={[GS, RP]} />);
    const open = (label) => {
      fireEvent.click(screen.getByRole("button", { name: /Organize/ }));
      fireEvent.click(screen.getByRole("menuitem", { name: label }));
    };
    open("Metadata…");
    expect(screen.getByText("Metadata of 2 datasets")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    open("Agents…");
    expect(screen.getByText("Agents of 2 datasets")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    open("Move to folder…");
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.getByRole("dialog").textContent).toMatch(/support/);
  });

  it("registers the three ⌘K commands for the selection or the active row", () => {
    const { unmount } = wrap(<Host selected={[]} active={GS} />);
    const cmds = commands();
    expect(cmds.map((c) => c.label)).toEqual([
      "Set metadata…",
      "Move to folder…",
      "Attach to agents…",
    ]);
    expect(cmds.every((c) => c.group === "Datasets")).toBe(true);
    expect(cmds.every((c) => c.when())).toBe(true);
    act(() => cmds[0].run());
    expect(screen.getByText("Metadata of Golden")).toBeTruthy();
    unmount();
    expect(commands()).toEqual([]);
  });

  it("read-only: a disabled Organize with the reason, commands not runnable", () => {
    wrap(<Host selected={[GS]} canEdit={false} />);
    const btn = screen.getByRole("button", { name: /Organize/ });
    expect(btn.disabled).toBe(true);
    expect(commands().every((c) => c.when() === false)).toBe(true);
    act(() => commands()[0].run());
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("no target: commands are not offered", () => {
    wrap(<Host selected={[]} />);
    expect(commands().every((c) => c.when() === false)).toBe(true);
  });
});

describe("pure parts", () => {
  it("organizeTargets: the selection, else the active row", () => {
    expect(organizeTargets([GS, null, RP], null)).toEqual([GS, RP]);
    expect(organizeTargets([], RP)).toEqual([RP]);
    expect(organizeTargets(undefined, null)).toEqual([]);
  });

  it("showOrganizeToast: Undo runs the dialog's undo and reports it", async () => {
    let resolveUndone;
    notify.mockImplementationOnce(() => ({
      undone: new Promise((r) => {
        resolveUndone = r;
      }),
    }));
    const undo = vi.fn(async () => ({ saved: [GS, RP], failed: [], total: 2 }));
    showOrganizeToast({
      variant: "success",
      message: "Moved 2 datasets to support",
      undo,
    });
    expect(notify.mock.calls[0][0]).toBe("Moved 2 datasets to support");
    expect(notify.mock.calls[0][1].undo).toBeTruthy();
    resolveUndone(true);
    await vi.waitFor(() => expect(undo).toHaveBeenCalled());
    await vi.waitFor(() =>
      expect(notify.mock.calls[1][0]).toBe("Moved back 2 datasets"),
    );
    showOrganizeToast({
      variant: "success",
      message: "Saved metadata on 1 dataset",
    });
    expect(notify.mock.calls[2][1].undo).toBeUndefined();
  });
});
