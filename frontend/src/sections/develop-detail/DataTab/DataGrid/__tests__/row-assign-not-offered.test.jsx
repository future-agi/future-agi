// Where the deployment does not offer row comments or row assignments (the
// launch registry neither ships them nor has them made real), the cell
// menu keeps both entries, Assign row to… says so in one sentence instead
// of the dialog, and the page reads nothing from those routes.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import axios from "src/utils/axios";
import { buildCellContextMenu } from "../gridContextMenu";
import {
  assignRowOrSay,
  rowAssigningOffered,
  rowCommentsOffered,
  setCollabOfferForTest,
} from "src/sections/common/Collab/collabOffer";
import {
  fetchAssignments,
  fetchDatasetComments,
  useAssignments,
  useCreateAssignments,
  useDatasetComments,
  useRowComments,
} from "src/api/develop/row-collab";

const ASSIGN_SENTENCE = "Assigning rows is not part of this release.";

const params = () => ({
  column: {
    getColDef: () => ({ col: { id: "c1" }, headerName: "Input" }),
    getColId: () => "c1",
  },
  node: { data: { row_id: "r1", row_meta: { split_ids: [] } } },
  value: "hello",
});
const handlers = () => ({
  filterTo: vi.fn(),
  exclude: vi.fn(),
  showAllIn: vi.fn(),
  openRow: vi.fn(),
  copyValue: vi.fn(),
  copyRowLink: vi.fn(),
  addToQueue: vi.fn(),
  toggleSplit: vi.fn(),
  manageSplits: vi.fn(),
  commentOnRow: vi.fn(),
  assignRow: vi.fn(),
  translateCell: vi.fn(),
});
const wrapper = ({ children }) => (
  <QueryClientProvider
    client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
  >
    {children}
  </QueryClientProvider>
);
const routeCalls = (spy) =>
  spy.mock.calls.filter(([url]) =>
    /\/(comments|assignments)\//.test(String(url)),
  );

let get;
let post;
beforeEach(() => {
  setCollabOfferForTest({ all: false });
  get = vi.spyOn(axios, "get").mockResolvedValue({ data: { result: {} } });
  post = vi.spyOn(axios, "post").mockResolvedValue({ data: { result: {} } });
});
afterEach(() => {
  setCollabOfferForTest(null);
  vi.restoreAllMocks();
});

describe("cell menu where row comments and assignments are not offered", () => {
  it("the registry offers neither in this build", () => {
    expect(rowCommentsOffered()).toBe(false);
    expect(rowAssigningOffered()).toBe(false);
  });

  it("keeps Comment on row and Assign row to… in the menu, both enabled", () => {
    const items = buildCellContextMenu(params(), {
      handlers: handlers(),
      collab: { canAssign: true },
    });
    const comment = items.find((i) => i?.name === "Comment on row");
    const assign = items.find((i) => i?.name === "Assign row to…");
    expect(comment).toBeTruthy();
    expect(assign).toBeTruthy();
    expect(Boolean(comment.disabled)).toBe(false);
    expect(Boolean(assign.disabled)).toBe(false);
  });

  it("Assign row to… shows the sentence instead of the dialog", () => {
    const open = vi.fn();
    const say = vi.fn();
    assignRowOrSay("r1", { open, say });
    expect(open).not.toHaveBeenCalled();
    expect(say).toHaveBeenCalledTimes(1);
    expect(say).toHaveBeenCalledWith(ASSIGN_SENTENCE, { variant: "info" });
  });

  it("opens the dialog where assigning is offered", () => {
    setCollabOfferForTest({
      all: false,
      madeReal: ["dataset.collab.assignments"],
    });
    const open = vi.fn();
    const say = vi.fn();
    assignRowOrSay("r1", { open, say });
    expect(open).toHaveBeenCalledWith("r1");
    expect(say).not.toHaveBeenCalled();
  });

  it("reads nothing from the comments and assignments routes", async () => {
    renderHook(() => useAssignments("d1", { status: "open" }), { wrapper });
    renderHook(() => useAssignments("d1", {}, { enabled: true }), { wrapper });
    renderHook(() => useDatasetComments("d1", { status: "all" }), { wrapper });
    renderHook(() => useRowComments("d1", "r1", { enabled: true }), {
      wrapper,
    });
    expect(await fetchAssignments("d1", { assignee: "me" })).toBeNull();
    expect(await fetchDatasetComments("d1", { status: "open" })).toBeNull();
    await new Promise((r) => setTimeout(r, 0));
    expect(routeCalls(get)).toEqual([]);
  });

  it("an assign request is refused with the sentence and nothing is sent", async () => {
    const { result } = renderHook(() => useCreateAssignments("d1"), {
      wrapper,
    });
    await expect(
      result.current.mutateAsync({ row_ids: ["r1"], assignee_id: "u1" }),
    ).rejects.toThrow(ASSIGN_SENTENCE);
    expect(routeCalls(post)).toEqual([]);
  });
});
