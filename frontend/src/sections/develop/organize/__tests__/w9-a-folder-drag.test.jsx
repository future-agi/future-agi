// @vitest-environment jsdom
// W9 A6 (W9-SCOPE §5): drag to move. Dragging a selected row carries the
// selection; a drop on a folder moves only the datasets not already there,
// one PUT each, with `Move 2 datasets to support` while over it and an
// Undo that puts each dataset back; rail folders, No folder and breadcrumb
// segments are drop targets.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/utils/axios", () => ({
  default: { put: vi.fn() },
  endpoints: { develop: { updateDataset: (id) => `/edit/${id}/` } },
}));

const { draggedDatasets, dropId, dropPlan, finishDrag, pathOfDropId } =
  await import("../folderDrag");
const { DatasetDragHandle, FolderDndProvider } = await import("../FolderDnd");
const { default: FolderRail } = await import("../FolderRail");
const { default: FolderBreadcrumb } = await import("../FolderBreadcrumb");

afterEach(cleanup);
const A = { id: "a", name: "A", folder: "" };
const B = { id: "b", name: "B", folder: "support" };
const C = { id: "c", name: "C", folder: "qa" };

describe("folderDrag", () => {
  it("drop ids round-trip folder paths; other ids are not folders", () => {
    expect(pathOfDropId(dropId("/support/billing/"))).toBe("support/billing");
    expect(pathOfDropId(dropId(""))).toBe("");
    expect(pathOfDropId("dataset-drag:a")).toBeNull();
    expect(pathOfDropId(undefined)).toBeNull();
  });

  it("a selected row drags the selection; an unselected one drags itself", () => {
    expect(draggedDatasets(A, [A, B])).toEqual([A, B]);
    expect(draggedDatasets(C, [A, B])).toEqual([C]);
    expect(draggedDatasets(null, [A])).toEqual([]);
  });

  it("the plan moves only those not there, with the label", () => {
    expect(dropPlan([A, B], "support")).toMatchObject({
      target: "support",
      moving: [A],
      label: "Move 1 dataset to support",
    });
    expect(dropPlan([A, B, C], "")).toMatchObject({
      label: "Move 2 datasets out of any folder",
    });
    expect(dropPlan([B], "support")).toBeNull();
  });

  it("finishDrag: one write per moving dataset, toast, Undo restores each", async () => {
    const save = vi.fn(async (list, patchFor) => ({
      saved: list,
      failed: [],
      total: list.length,
      patches: list.map((d) => patchFor(d)),
    }));
    const toast = await finishDrag({
      row: A,
      overId: dropId("qa"),
      selected: [A, B, C],
      save,
    });
    expect(save.mock.calls[0][0].map((d) => d.id)).toEqual(["a", "b"]);
    expect(save.mock.calls[0][0].map((d) => save.mock.calls[0][1](d))).toEqual([
      { folder: "qa" },
      { folder: "qa" },
    ]);
    expect(toast.message).toBe("Moved 2 datasets to qa");
    await toast.undo();
    const [back, patchBack] = save.mock.calls[1];
    expect(back.map((d) => [d.id, patchBack(d)])).toEqual([
      ["a", { folder: "" }],
      ["b", { folder: "support" }],
    ]);
  });

  it("finishDrag does nothing off a folder or when nothing changes", async () => {
    const save = vi.fn();
    expect(
      await finishDrag({ row: A, overId: "elsewhere", selected: [], save }),
    ).toBeNull();
    expect(
      await finishDrag({
        row: B,
        overId: dropId("support"),
        selected: [],
        save,
      }),
    ).toBeNull();
    expect(save).not.toHaveBeenCalled();
  });
});

describe("drag UI", () => {
  const wrap = (ui) =>
    render(
      <QueryClientProvider client={new QueryClient()}>
        {ui}
      </QueryClientProvider>,
    );

  it("the handle is named; folders, No folder and breadcrumb segments take drops", () => {
    wrap(
      <FolderDndProvider selected={[]} canEdit>
        <DatasetDragHandle row={A} />
        <FolderRail
          folders={[{ path: "support", count: 1, total: 1 }]}
          allCount={3}
          starredCount={0}
          noFolderCount={1}
          pick={{ kind: "all" }}
          onPick={() => {}}
        />
        <FolderBreadcrumb path="support/billing" onPick={() => {}} />
      </FolderDndProvider>,
    );
    expect(
      screen.getByRole("button", { name: "Drag A to a folder" }),
    ).toBeTruthy();
    const paths = [...document.querySelectorAll("[data-drop-path]")].map((e) =>
      e.getAttribute("data-drop-path"),
    );
    expect(paths).toEqual(["support", "", "support"]);
  });

  it("read-only: no drag context, children as they are", () => {
    wrap(
      <FolderDndProvider selected={[]} canEdit={false}>
        <span>list</span>
      </FolderDndProvider>,
    );
    expect(screen.getByText("list")).toBeTruthy();
  });
});
