// P0: the Row view's Activity tab hosts two claims that lanes
// make real separately. Comments (dataset.collab.comments) own the threads,
// the Comments chip and the composer; the history (dataset.rowExtras.activity)
// owns the feed, its filter chips and the rows/{row}/activity read. Each
// claim alone gives a working tab; both together keep today's tab.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "src/utils/test-utils";
import axios from "src/utils/axios";
import {
  rowTabShownIn,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";

const calls = vi.hoisted(() => ({ history: [], comments: [] }));
let events = [];
let threads = [];

vi.mock("src/api/develop/row-view", async (importOriginal) => ({
  ...(await importOriginal()),
  useRowActivity: (datasetId, rowId, options) => {
    calls.history.push(options);
    const on = options?.enabled !== false;
    return {
      data: on ? events : undefined,
      isLoading: false,
      isError: false,
    };
  },
}));
vi.mock("src/api/develop/row-collab", async (importOriginal) => ({
  ...(await importOriginal()),
  useRowComments: (datasetId, rowId, options) => {
    calls.comments.push(options);
    return { data: { threads } };
  },
  usePostComment: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock("src/sections/common/Collab/CommentThread", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ status }) =>
      createElement("div", { "data-testid": "threads", "data-status": status }),
  };
});
vi.mock("src/sections/common/Collab/CommentComposer", async () => {
  const { createElement } = await import("react");
  return {
    default: () => createElement("div", { "data-testid": "pinned-composer" }),
  };
});
vi.mock("src/sections/common/RowPanel/ActivityList", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ events: list, onOpenComment }) =>
      createElement(
        "ul",
        {
          "data-testid": "history",
          "data-comment-door": onOpenComment ? "yes" : "no",
        },
        list.map((e) => createElement("li", { key: e.id }, e.type)),
      ),
  };
});

const { default: RowActivityTab } = await import("../RowActivityTab");

const AT = new Date().toISOString();
const claims = (...ids) => ({
  launch: true,
  experimentsBasic: false,
  madeReal: ids,
});
// The tab's own comments read asks for every run's threads.
const tabCommentsRead = () => calls.comments.filter((o) => o?.runId === "all");

const mount = (search = "") => {
  window.history.pushState({}, "", `/dashboard/develop/d1${search}`);
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <RowActivityTab datasetId="d1" rowId="r1" />
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  calls.history.length = 0;
  calls.comments.length = 0;
  events = [
    { id: "e1", type: "run", at: AT },
    { id: "e2", type: "comment", at: AT, resolved: false },
  ];
  threads = [
    { root: { id: "t1", resolved: false, deleted: false }, replies: [] },
    { root: { id: "t2", resolved: true, deleted: false }, replies: [] },
    { root: { id: "t3", resolved: false, deleted: true }, replies: [] },
  ];
  vi.spyOn(axios, "get").mockResolvedValue({
    data: { result: { column_config: [] } },
  });
});
afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
  vi.restoreAllMocks();
});

describe("P0 705: the Activity tab is shown by either claim", () => {
  it("shows the tab for comments, the history, or both; never for neither", () => {
    const tab = (...ids) =>
      rowTabShownIn("activity", { ...claims(), madeReal: new Set(ids) });
    expect(tab()).toBe(false);
    expect(tab("dataset.collab.comments")).toBe(true);
    expect(tab("dataset.rowExtras.activity")).toBe(true);
    expect(tab("dataset.collab.comments", "dataset.rowExtras.activity")).toBe(
      true,
    );
    // The shared host follows its parts; a claim on it alone shows nothing.
    expect(tab("dataset.rowTab.activity")).toBe(false);
  });
});

describe("P0 705: RowActivityTab per claim", () => {
  it("comments alone: the row's threads with their counts, no history read", () => {
    setLaunchOverrideForTest(claims("dataset.collab.comments"));
    // The comment door's ract=comments is dropped without the history
    // claim; the tab opens on the threads anyway.
    mount("?row=r1&rowTab=activity&rcs=open");
    expect(calls.history.at(-1)).toEqual({ enabled: false });
    expect(tabCommentsRead().at(-1)?.enabled).not.toBe(false);
    expect(screen.getByTestId("threads").dataset.status).toBe("open");
    expect(screen.queryByTestId("activity-filters")).toBeNull();
    expect(screen.queryByTestId("history")).toBeNull();
    expect(screen.queryByTestId("pinned-composer")).toBeNull();
    // Open 1 · Resolved 1 · All 3, counted from the threads.
    expect(screen.getByTestId("comment-status-open").textContent).toBe(
      "Open 1",
    );
    expect(screen.getByTestId("comment-status-resolved").textContent).toBe(
      "Resolved 1",
    );
    expect(screen.getByTestId("comment-status-all").textContent).toBe("All 3");
  });

  it("the history alone: its feed and chips, and no comment part", () => {
    setLaunchOverrideForTest(claims("dataset.rowExtras.activity"));
    mount("?row=r1&rowTab=activity&ract=comments");
    expect(calls.history.at(-1)?.enabled).not.toBe(false);
    expect(tabCommentsRead().every((o) => o.enabled === false)).toBe(true);
    expect(screen.getByTestId("activity-chip-runs")).toBeTruthy();
    expect(screen.queryByTestId("activity-chip-comments")).toBeNull();
    // ract=comments falls back to All; comment events never list.
    const history = screen.getByTestId("history");
    expect(history.textContent).toBe("run");
    expect(history.dataset.commentDoor).toBe("no");
    expect(screen.queryByTestId("threads")).toBeNull();
    expect(screen.queryByTestId("activity-comments")).toBeNull();
    expect(screen.queryByTestId("pinned-composer")).toBeNull();
  });

  it("both claims: today's tab (Comments chip, feed with comments, composer)", () => {
    setLaunchOverrideForTest(
      claims("dataset.collab.comments", "dataset.rowExtras.activity"),
    );
    mount("?row=r1&rowTab=activity");
    expect(calls.history.at(-1)?.enabled).not.toBe(false);
    // The counts come from the history; the tab adds no comments read.
    expect(tabCommentsRead().every((o) => o.enabled === false)).toBe(true);
    expect(screen.getByTestId("activity-chip-comments")).toBeTruthy();
    expect(screen.getByTestId("activity-chip-runs")).toBeTruthy();
    const history = screen.getByTestId("history");
    expect(history.textContent).toBe("runcomment");
    expect(history.dataset.commentDoor).toBe("yes");
    expect(screen.getByTestId("pinned-composer")).toBeTruthy();
  });

  it("a prototype or unit-test build keeps the full tab", () => {
    mount("?row=r1&rowTab=activity&ract=comments&rcs=resolved");
    expect(calls.history.at(-1)?.enabled).not.toBe(false);
    expect(screen.getByTestId("activity-chip-comments")).toBeTruthy();
    expect(screen.getByTestId("threads").dataset.status).toBe("resolved");
    // Status counts read the history's comment events (one open).
    expect(screen.getByTestId("comment-status-open").textContent).toBe(
      "Open 1",
    );
  });
});
