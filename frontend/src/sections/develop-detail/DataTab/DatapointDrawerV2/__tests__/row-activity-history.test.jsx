// Row activity (dataset.rowExtras.activity) in the Row view's Activity tab,
// through the real history read: GET …/rows/{row_id}/activity/
// ?page_size=&cursor= → {events, next_cursor, has_more}, newest first.
// Older pages append in server order (one entry per event id). A failed
// read stays inside the history region, so the comment threads and the
// composer keep working (G2A-collab-04).
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import RowActivityTab from "../RowActivityTab";

const h = vi.hoisted(() => ({ answers: [], calls: [] }));

vi.mock("src/utils/axios", async (importOriginal) => {
  const real = await importOriginal();
  return {
    ...real,
    default: {
      ...real.default,
      get: (url, config) => {
        // The tab's column read (comment anchors) answers with no columns.
        if (!String(url).endsWith("/activity/"))
          return Promise.resolve({ data: { result: { column_config: [] } } });
        h.calls.push({ url, params: config?.params });
        const answer = h.answers.shift();
        if (!answer) return Promise.reject(new Error("unexpected read"));
        // A read that never answers keeps the query loading.
        if (answer.pending) return new Promise(() => {});
        return answer.ok
          ? Promise.resolve({ data: { status: true, result: answer.ok } })
          : Promise.reject(answer.fail);
      },
    },
  };
});
vi.mock("src/api/develop/row-collab", async (importOriginal) => ({
  ...(await importOriginal()),
  useRowComments: () => ({ data: { threads: [] } }),
  usePostComment: () => ({
    mutateAsync: () => Promise.resolve({}),
    isPending: false,
  }),
}));
vi.mock("src/sections/common/Collab/CommentThread", async () => {
  const { createElement } = await import("react");
  return {
    default: () => createElement("div", { "data-testid": "threads" }),
  };
});
vi.mock("src/sections/common/Collab/CommentComposer", async () => {
  const { createElement } = await import("react");
  return {
    default: () =>
      createElement("input", {
        "data-testid": "pinned-composer",
        "aria-label": "Write a comment",
      }),
  };
});

const BOTH = ["dataset.rowExtras.activity", "dataset.collab.comments"];
const VERB = {
  create: "created",
  edit: "changed",
  delete: "deleted",
  restore: "restored",
};
// Higher ids are newer, as the server orders them.
const at = (id) =>
  new Date(Date.now() - (100 - Number(id)) * 60 * 1000).toISOString();
const common = (id) => ({
  id,
  at: at(id),
  actor_id: "7",
  actor: "Asha Rao",
  origin: "write",
  before: null,
  after: null,
  before_truncated: false,
  after_truncated: false,
  source: null,
});
const cell = (id, action, column, extra = {}) => ({
  ...common(id),
  type: "edit",
  subject: "cell",
  action,
  cell_id: `00000000-0000-4000-8000-00000000000${id}`,
  column_id: "00000000-0000-4000-8000-0000000000c1",
  column,
  text: `Asha Rao ${VERB[action]} ${column}`,
  ...extra,
});
const rowEvent = (id, action, extra = {}) => ({
  ...common(id),
  type: { create: "created", delete: "deleted", restore: "restored" }[action],
  subject: "row",
  action,
  cell_id: null,
  column_id: null,
  column: null,
  text: `Asha Rao ${VERB[action]} this row`,
  ...extra,
});
const page = (events, next = null) => ({
  ok: { events, next_cursor: next, has_more: next !== null },
});
const PENDING = { pending: true };
const feedText = () => screen.getByTestId("activity-feed").textContent;
const OLDER = { name: "Show older activity" };
// Lets the query settle after an action whose result is a read not sent.
const settle = () =>
  act(async () => {
    await new Promise((resolve) => {
      setTimeout(resolve, 0);
    });
  });

let client = null;
const mount = (search = "?row=r1&rowTab=activity") => {
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: false,
    madeReal: BOTH,
  });
  window.history.pushState({}, "", `/dashboard/develop/d1${search}`);
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RowActivityTab datasetId="d1" rowId="r1" />
    </QueryClientProvider>,
  );
};

afterEach(() => {
  cleanup();
  client?.clear();
  client = null;
  setLaunchOverrideForTest(null);
  h.answers.length = 0;
  h.calls.length = 0;
});

describe("Row activity: the history read and its pages", () => {
  it("reads the newest page first, then older pages with the returned cursor, one entry per event", async () => {
    h.answers.push(
      page(
        [cell("9", "edit", "answer"), cell("8", "edit", "question")],
        "older-1",
      ),
      page([cell("8", "edit", "question"), cell("7", "create", "context")]),
    );
    mount();
    await screen.findByText(/changed question/);
    expect(h.calls).toEqual([
      {
        url: "/model-hub/datasets/d1/rows/r1/activity/",
        params: { page_size: 50 },
      },
    ]);
    // Counts cover the loaded pages only, so they say there is more.
    expect(screen.getByTestId("activity-chip-all").textContent).toBe("All 2+");

    fireEvent.click(screen.getByRole("button", OLDER));
    await screen.findByText(/set context/);
    expect(h.calls[1].params).toEqual({ page_size: 50, cursor: "older-1" });
    const text = feedText();
    expect(text.indexOf("changed answer")).toBeLessThan(
      text.indexOf("changed question"),
    );
    expect(text.indexOf("changed question")).toBeLessThan(
      text.indexOf("set context"),
    );
    expect(text.split("changed question")).toHaveLength(2);
    expect(screen.getByTestId("activity-chip-all").textContent).toBe("All 3");
    expect(screen.queryByRole("button", OLDER)).toBeNull();
  });

  it("keeps Show older activity focusable while the older page loads, and asks once", async () => {
    h.answers.push(page([cell("9", "edit", "answer")], "older-1"), PENDING);
    mount();
    await screen.findByText(/changed answer/);
    const button = screen.getByRole("button", OLDER);
    button.focus();
    fireEvent.click(button);
    await waitFor(() =>
      expect(button.textContent).toMatch(/Loading older activity/),
    );
    // A disabled button would drop keyboard focus to the page.
    expect(button.disabled).toBe(false);
    expect(button.getAttribute("aria-disabled")).toBe("true");
    expect(button.getAttribute("aria-busy")).toBe("true");
    expect(document.activeElement).toBe(button);

    fireEvent.click(button);
    await settle();
    expect(h.calls).toHaveLength(2);
    expect(screen.getByTestId("activity-load-more")).toBe(button);
  });

  it("names each change and keeps restores under Edits; no chip claims a row has no runs", async () => {
    h.answers.push(
      page([
        rowEvent("6", "restore"),
        rowEvent("5", "delete"),
        cell("4", "delete", "answer", { before: "Paris" }),
        cell("3", "restore", "context", { after: "Europe" }),
        cell("2", "create", "question", { after: "Capital of France?" }),
        rowEvent("1", "create", {
          origin: "import",
          source: { type: "upload" },
        }),
      ]),
    );
    mount();
    await screen.findByText(/restored this row/);
    expect(screen.getByText(/deleted this row/)).toBeTruthy();
    expect(screen.getByText(/removed answer/)).toBeTruthy();
    expect(screen.getByText(/restored context/)).toBeTruthy();
    expect(screen.getByText(/set question/)).toBeTruthy();
    expect(screen.getByText(/Imported from a file/)).toBeTruthy();
    // The history holds row and cell changes only: no Runs, Annotations or
    // Splits chip says "none" about things it never lists.
    for (const key of ["runs", "annotations", "splits"])
      expect(screen.queryByTestId(`activity-chip-${key}`)).toBeNull();

    fireEvent.click(screen.getByTestId("activity-chip-edits"));
    expect(screen.getByTestId("activity-chip-edits").textContent).toBe(
      "Edits 6",
    );
    expect(feedText()).toMatch(/restored this row/);
  });

  it("says when a long value's preview hides the change", async () => {
    const long = "A".repeat(40);
    h.answers.push(
      page([
        cell("2", "edit", "answer", {
          before: long,
          after: long,
          before_truncated: true,
          after_truncated: true,
        }),
        cell("1", "edit", "question", {
          before: "short",
          after: `${long}B`,
          after_truncated: true,
        }),
      ]),
    );
    mount();
    await screen.findByText(/changed answer/);
    screen
      .getAllByRole("button", { name: /Show diff/ })
      .forEach((button) => fireEvent.click(button));
    expect(
      await screen.findByText(
        "The change is past the part of the value shown here.",
      ),
    ).toBeTruthy();
    expect(screen.getByText("Long values are shortened here.")).toBeTruthy();
  });
});

describe("Row activity: failed reads", () => {
  it("shows the server's reason when the read is refused", async () => {
    h.answers.push({
      fail: {
        status: false,
        type: "permission_denied",
        code: "permission_denied",
        detail: "You do not have permission to perform this action.",
        statusCode: 403,
      },
    });
    mount();
    expect(
      await screen.findByText("Couldn't load this row's activity."),
    ).toBeTruthy();
    expect(
      screen.getByText("You do not have permission to perform this action."),
    ).toBeTruthy();
    expect(screen.queryByText(/No activity on this row yet/)).toBeNull();
  });

  it("says the server didn't answer, and Retry reads again", async () => {
    h.answers.push(
      { fail: { message: "Network Error", transportCode: "ERR_NETWORK" } },
      page([cell("1", "edit", "answer")]),
    );
    mount();
    expect(
      await screen.findByText(
        "The server didn't answer. Check your connection and try again.",
      ),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
    await screen.findByText(/changed answer/);
    expect(screen.queryByTestId("error-state")).toBeNull();
    expect(h.calls.map((c) => c.params)).toEqual([
      { page_size: 50 },
      { page_size: 50 },
    ]);
  });

  it("says something went wrong on our side for a server failure", async () => {
    h.answers.push({
      fail: { statusCode: 500, transportCode: "ERR_BAD_RESPONSE" },
    });
    mount();
    expect(
      await screen.findByText("Something went wrong on our side. Try again."),
    ).toBeTruthy();
  });

  it("keeps loaded activity when an older page fails, and retries that page", async () => {
    h.answers.push(
      page([cell("9", "edit", "answer")], "older-1"),
      { fail: { statusCode: 503 } },
      page([cell("8", "edit", "question")]),
    );
    mount();
    await screen.findByText(/changed answer/);
    fireEvent.click(screen.getByRole("button", OLDER));
    expect(
      await screen.findByText("Couldn't load older activity."),
    ).toBeTruthy();
    expect(screen.getByText(/Something went wrong on our side/)).toBeTruthy();
    expect(screen.getByText(/changed answer/)).toBeTruthy();
    expect(screen.queryByTestId("error-state")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByText(/changed question/);
    expect(h.calls.map((c) => c.params.cursor ?? null)).toEqual([
      null,
      "older-1",
      "older-1",
    ]);
  });

  it("reloads from the newest page when an older page's link has expired", async () => {
    h.answers.push(
      page([cell("9", "edit", "answer")], "older-1"),
      {
        fail: {
          status: false,
          type: "validation_error",
          code: "invalid",
          attr: "cursor",
          detail:
            "This activity cursor is invalid or expired. Reload the activity.",
          statusCode: 400,
        },
      },
      page([cell("9", "edit", "answer")], "older-2"),
    );
    mount();
    await screen.findByText(/changed answer/);
    fireEvent.click(screen.getByRole("button", OLDER));
    expect(await screen.findByText(/This list has expired/)).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/cursor/i);

    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    await waitFor(() => expect(h.calls).toHaveLength(3));
    expect(h.calls[2].params).toEqual({ page_size: 50 });
    expect(await screen.findByRole("button", OLDER)).toBeTruthy();
  });

  it("keeps loaded activity when a refresh fails, and Retry reads again", async () => {
    h.answers.push(
      page([cell("1", "edit", "answer")]),
      { fail: { statusCode: 503 } },
      page([cell("2", "edit", "question"), cell("1", "edit", "answer")]),
    );
    mount();
    await screen.findByText(/changed answer/);
    // A cell save invalidates the row's reads, so the history reads again.
    await act(() =>
      client.invalidateQueries({ queryKey: ["develop", "row-view", "d1"] }),
    );
    const notice = await screen.findByTestId("activity-refresh-error");
    expect(notice.textContent).toContain(
      "Couldn't refresh this row's activity.",
    );
    expect(notice.textContent).toContain(
      "Something went wrong on our side. Try again.",
    );
    expect(screen.getByText(/changed answer/)).toBeTruthy();
    expect(screen.queryByTestId("error-state")).toBeNull();
    expect(screen.queryByTestId("activity-older-error")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText(/changed question/);
    expect(screen.queryByTestId("activity-refresh-error")).toBeNull();
    expect(h.calls.map((c) => c.params)).toEqual([
      { page_size: 50 },
      { page_size: 50 },
      { page_size: 50 },
    ]);
  });
});

describe("G2A-collab-04: a failed history read stays in the history region", () => {
  it("keeps the composer mounted while the first page loads", async () => {
    h.answers.push(PENDING);
    mount();
    const loading = await screen.findByTestId("activity-loading");
    expect(loading.getAttribute("role")).toBe("status");
    expect(loading.getAttribute("aria-busy")).toBe("true");
    expect(screen.getByTestId("pinned-composer")).toBeTruthy();
    // The chips wait for the history before they show counts.
    expect(screen.getByTestId("activity-chip-all").textContent).toBe("All");
    await waitFor(() => expect(h.calls).toHaveLength(1));
    expect(screen.queryByTestId("error-state")).toBeNull();
  });

  it("keeps the composer and its draft while the history fails and is retried", async () => {
    h.answers.push(
      { fail: { statusCode: 503 } },
      page([cell("1", "edit", "answer")]),
    );
    mount();
    expect(await screen.findByTestId("error-state")).toBeTruthy();
    const composer = screen.getByTestId("pinned-composer");
    fireEvent.change(composer, { target: { value: "Looks wrong" } });

    fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
    await screen.findByText(/changed answer/);
    expect(screen.getByTestId("pinned-composer")).toBe(composer);
    expect(composer.value).toBe("Looks wrong");
    fireEvent.click(screen.getByTestId("activity-chip-comments"));
    expect(screen.getByTestId("threads")).toBeTruthy();
  });

  it("opens the threads from a comment link while the history fails", async () => {
    h.answers.push({ fail: { statusCode: 503 } });
    mount("?row=r1&rowTab=activity&ract=comments&rcompose=1");
    expect(await screen.findByTestId("error-state")).toBeTruthy();
    expect(screen.getByTestId("threads")).toBeTruthy();
    expect(screen.getByTestId("activity-chip-comments")).toBeTruthy();
  });
});
