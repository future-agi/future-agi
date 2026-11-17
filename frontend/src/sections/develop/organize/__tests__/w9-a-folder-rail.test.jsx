// @vitest-environment jsdom
// W9 A6 (W9-SCOPE §5): the folder rail and breadcrumb are filters (one
// mechanism); rename, move and remove rewrite each dataset's path in the
// subtree, one PUT each, refuse a move into the folder's own subfolder,
// and Undo puts each dataset back.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const put = vi.fn();
vi.mock("src/utils/axios", () => ({
  default: { put: (...a) => put(...a) },
  endpoints: { develop: { updateDataset: (id) => `/edit/${id}/` } },
}));

const { folderCondition, railPick, railRows, withRailPick, STARRED_CONDITION } =
  await import("../folderRailModel");
const { doneMessage, folderPlan, folderTarget } = await import(
  "../folderActions"
);
const { CYCLE_SENTENCE } = await import("../folderTree");
const { default: FolderRail } = await import("../FolderRail");
const { default: FolderBreadcrumb } = await import("../FolderBreadcrumb");
const { default: FolderActionDialog } = await import("../FolderActionDialog");

const wrap = (ui) =>
  render(
    <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>,
  );
const TAG = {
  column_id: "tag",
  filter_config: {
    filter_type: "categorical",
    filter_op: "in",
    filter_value: ["golden"],
  },
};
const DATASETS = [
  { id: "a", name: "A", folder: "support/billing" },
  { id: "b", name: "B", folder: "support" },
  { id: "c", name: "C", folder: "" },
];

beforeEach(() => {
  put.mockReset();
  put.mockResolvedValue({ data: { result: "ok" } });
});
afterEach(cleanup);

describe("folderRailModel", () => {
  it("a rail pick replaces only the rail's own condition", () => {
    const f1 = withRailPick([TAG], { kind: "folder", path: "/support/" });
    expect(f1).toEqual([TAG, folderCondition("support")]);
    expect(f1[1].filter_config).toEqual({
      filter_type: "text",
      filter_op: "starts_with",
      filter_value: "support",
    });
    expect(railPick(f1)).toEqual({ kind: "folder", path: "support" });
    const f2 = withRailPick(f1, { kind: "starred" });
    expect(f2).toEqual([TAG, STARRED_CONDITION]);
    expect(railPick(f2)).toEqual({ kind: "starred" });
    const f3 = withRailPick(f2, { kind: "none" });
    expect(f3[1].filter_config.filter_op).toBe("is_null");
    expect(railPick(f3)).toEqual({ kind: "none" });
    expect(withRailPick(f3, { kind: "all" })).toEqual([TAG]);
    expect(railPick([TAG])).toEqual({ kind: "all" });
  });

  it("a folder condition the rail can't show selects no row", () => {
    const eq = {
      column_id: "folder",
      filter_config: {
        filter_type: "text",
        filter_op: "equals",
        filter_value: "support",
      },
    };
    expect(railPick([eq])).toEqual({ kind: "custom" });
  });

  it("railRows: leaf names, depth, path order, own and subtree counts", () => {
    expect(
      railRows([
        { path: "support/billing", count: 1, total: 1 },
        { path: "support", count: 1, total: 2 },
      ]),
    ).toEqual([
      { path: "support", name: "support", depth: 1, count: 1, total: 2 },
      {
        path: "support/billing",
        name: "billing",
        depth: 2,
        count: 1,
        total: 1,
      },
    ]);
  });
});

describe("folderActions", () => {
  it("targets: rename keeps the parent, move keeps the leaf, remove goes up", () => {
    expect(folderTarget("rename", "support/billing", "invoices")).toBe(
      "support/invoices",
    );
    expect(folderTarget("rename", "support", "a/b")).toBeNull();
    expect(folderTarget("move", "support/billing", "help")).toBe(
      "help/billing",
    );
    expect(folderTarget("move", "support/billing", "")).toBe("billing");
    expect(folderTarget("remove", "support/billing")).toBe("support");
  });

  it("plans a prefix rewrite of the subtree and refuses a cycle", () => {
    expect(folderPlan("rename", DATASETS, "support", "help").changes).toEqual([
      { id: "a", from: "support/billing", to: "help/billing" },
      { id: "b", from: "support", to: "help" },
    ]);
    expect(folderPlan("move", DATASETS, "support", "support/billing")).toEqual({
      changes: [],
      problem: CYCLE_SENTENCE,
    });
    expect(folderPlan("rename", DATASETS, "support", "").problem).toBe(
      "A folder name can't be empty or contain “/”.",
    );
    expect(folderPlan("remove", DATASETS, "support/billing").changes).toEqual([
      { id: "a", from: "support/billing", to: "support" },
    ]);
  });

  it("says what happened", () => {
    expect(doneMessage("rename", "support", "help", 2)).toBe(
      "Renamed support to help · 2 datasets",
    );
    expect(doneMessage("move", "support/billing", "help", 1)).toBe(
      "Moved support/billing to help/billing · 1 dataset",
    );
    expect(doneMessage("remove", "support", "", 2)).toBe(
      "Removed support · 2 datasets moved out of any folder",
    );
  });
});

describe("FolderRail", () => {
  const props = {
    folders: [
      { path: "support", count: 1, total: 2 },
      { path: "support/billing", count: 1, total: 1 },
    ],
    allCount: 9,
    starredCount: 2,
    noFolderCount: 7,
    pick: { kind: "folder", path: "support/billing" },
  };

  it("shows every row with its count; a click picks; ⋯ offers the actions", () => {
    const onPick = vi.fn();
    const onAction = vi.fn();
    wrap(<FolderRail {...props} onPick={onPick} onAction={onAction} canEdit />);
    const nav = screen.getByRole("navigation", { name: "Folders" });
    expect(nav.textContent).toBe(
      "All datasets9Starred2support2billing1No folder7",
    );
    expect(
      screen
        .getByRole("button", { name: /^billing/ })
        .getAttribute("aria-current"),
    ).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: /^support/ }));
    expect(onPick).toHaveBeenLastCalledWith({
      kind: "folder",
      path: "support",
    });
    fireEvent.click(screen.getByRole("button", { name: /^Starred/ }));
    expect(onPick).toHaveBeenLastCalledWith({ kind: "starred" });
    fireEvent.click(screen.getByRole("button", { name: /^No folder/ }));
    expect(onPick).toHaveBeenLastCalledWith({ kind: "none" });
    fireEvent.click(
      screen.getByRole("button", { name: "Folder actions for support" }),
    );
    fireEvent.click(screen.getByRole("menuitem", { name: "Move folder to…" }));
    expect(onAction).toHaveBeenCalledWith("move", "support");
  });

  it("no ⋯ for read-only people", () => {
    wrap(<FolderRail {...props} onPick={() => {}} onAction={() => {}} />);
    expect(screen.queryByRole("button", { name: /Folder actions/ })).toBeNull();
  });
});

describe("FolderBreadcrumb", () => {
  it("All datasets / support / billing; a segment picks that folder", () => {
    const onPick = vi.fn();
    wrap(<FolderBreadcrumb path="support/billing" onPick={onPick} />);
    expect(screen.getByRole("navigation", { name: "Folder" }).textContent).toBe(
      "All datasets/support/billing",
    );
    fireEvent.click(screen.getByRole("button", { name: "support" }));
    expect(onPick).toHaveBeenCalledWith({ kind: "folder", path: "support" });
    fireEvent.click(screen.getByRole("button", { name: "All datasets" }));
    expect(onPick).toHaveBeenCalledWith({ kind: "all" });
  });
});

describe("FolderActionDialog", () => {
  it("rename: one PUT per dataset in the subtree, then Undo puts them back", async () => {
    const onDone = vi.fn();
    const onClose = vi.fn();
    wrap(
      <FolderActionDialog
        open
        action="rename"
        path="support"
        datasets={DATASETS}
        onClose={onClose}
        onDone={onDone}
      />,
    );
    const input = screen.getByLabelText("New name");
    expect(input.value).toBe("support");
    fireEvent.change(input, { target: { value: "help" } });
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(put.mock.calls).toEqual([
      ["/edit/a/", { folder: "help/billing" }],
      ["/edit/b/", { folder: "help" }],
    ]);
    const toast = onDone.mock.calls[0][0];
    expect(toast.message).toBe("Renamed support to help · 2 datasets");
    put.mockClear();
    const res = await toast.undo();
    expect(res.saved.length).toBe(2);
    expect(put.mock.calls).toEqual([
      ["/edit/a/", { folder: "support/billing" }],
      ["/edit/b/", { folder: "support" }],
    ]);
    expect(onClose).toHaveBeenCalled();
  });

  it("move into its own subfolder is refused with the sentence; nothing sent", async () => {
    wrap(
      <FolderActionDialog
        open
        action="move"
        path="support"
        datasets={DATASETS}
        onClose={() => {}}
      />,
    );
    fireEvent.change(screen.getByLabelText("Move into (empty = top level)"), {
      target: { value: "support/billing" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Move" }));
    expect((await screen.findByRole("alert")).textContent).toBe(CYCLE_SENTENCE);
    expect(put).not.toHaveBeenCalled();
  });

  it("remove: says where the datasets go and moves them up", async () => {
    const onDone = vi.fn();
    wrap(
      <FolderActionDialog
        open
        action="remove"
        path="support/billing"
        datasets={DATASETS}
        onClose={() => {}}
        onDone={onDone}
      />,
    );
    expect(screen.getByText("Its dataset moves to support.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Remove folder" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(put.mock.calls).toEqual([["/edit/a/", { folder: "support" }]]);
    expect(onDone.mock.calls[0][0].message).toBe(
      "Removed support/billing · 1 dataset moved to support",
    );
  });

  it("a partial failure names the dataset and the server's sentence", async () => {
    put
      .mockResolvedValueOnce({ data: { result: "ok" } })
      .mockRejectedValueOnce({
        response: { data: { result: "You need edit access to this dataset." } },
      });
    const onDone = vi.fn();
    wrap(
      <FolderActionDialog
        open
        action="rename"
        path="support"
        datasets={DATASETS}
        onClose={() => {}}
        onDone={onDone}
      />,
    );
    fireEvent.change(screen.getByLabelText("New name"), {
      target: { value: "help" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(onDone.mock.calls[0][0].message).toBe(
      "Saved on 1 of 2 · B: You need edit access to this dataset.",
    );
  });
});
