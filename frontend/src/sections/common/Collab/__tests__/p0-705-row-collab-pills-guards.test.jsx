// P0: RowCollabPills' launch gates only switch its reads off.
// useRowComments and useAssignments keep their own guards (a pending new
// row or a missing id is never read), so a shown surface never runs a
// query the hooks would have refused. A query that runs builds its path
// first (endpoints.develop.w4), so the path builders count every run, even
// one that fails before the network.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, waitFor } from "src/utils/test-utils";
import axios, { endpoints } from "src/utils/axios";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { PENDING_ROW_PREFIX } from "src/sections/develop-detail/DataTab/Versions/pendingRows";
import RowCollabPills from "../RowCollabPills";

let get;
let commentPath;
let assignmentPath;
const urls = () => get.mock.calls.map(([url]) => String(url));
const commentReads = () => commentPath.mock.calls;
const assignmentReads = () => assignmentPath.mock.calls;

const mount = (props) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <RowCollabPills canEdit {...props} />
    </QueryClientProvider>,
  );
// Let react-query start (and settle) any fetch it is going to start.
const settle = () =>
  act(async () => {
    await new Promise((r) => setTimeout(r, 30));
  });

beforeEach(() => {
  get = vi.spyOn(axios, "get").mockResolvedValue({
    data: { result: { threads: [], open: 0, assignments: [], me: {} } },
  });
  commentPath = vi.spyOn(endpoints.develop.w4, "rowComments");
  assignmentPath = vi.spyOn(endpoints.develop.w4, "assignments");
});
afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
  vi.restoreAllMocks();
});

describe("P0 705: RowCollabPills keeps the hooks' own guards", () => {
  it("a pending new row reads no comments", async () => {
    mount({ datasetId: "d1", rowId: `${PENDING_ROW_PREFIX}1` });
    await settle();
    expect(commentReads()).toEqual([]);
  });

  it("an undefined row id reads no comments", async () => {
    mount({ datasetId: "d1", rowId: undefined });
    await settle();
    expect(commentReads()).toEqual([]);
  });

  it("an undefined dataset id reads nothing", async () => {
    mount({ datasetId: undefined, rowId: "r1" });
    await settle();
    expect(commentReads()).toEqual([]);
    expect(assignmentReads()).toEqual([]);
    expect(urls()).toEqual([]);
  });

  it("a launch build with neither claim reads nothing for a saved row", async () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      madeReal: [],
    });
    mount({ datasetId: "d1", rowId: "r1" });
    await settle();
    expect(commentReads()).toEqual([]);
    expect(assignmentReads()).toEqual([]);
    expect(urls()).toEqual([]);
  });

  it("each claim turns on only its own read (positive controls)", async () => {
    mount({ datasetId: "d1", rowId: "r1" });
    await waitFor(() => expect(commentReads()).toEqual([["d1", "r1"]]));
    await waitFor(() => expect(assignmentReads()).toEqual([["d1"]]));
    await waitFor(() => expect(urls()).toHaveLength(2));
    cleanup();
    commentPath.mockClear();
    assignmentPath.mockClear();
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      madeReal: ["dataset.collab.comments"],
    });
    mount({ datasetId: "d1", rowId: "r1" });
    await waitFor(() => expect(commentReads()).toEqual([["d1", "r1"]]));
    await settle();
    expect(assignmentReads()).toEqual([]);
  });
});
