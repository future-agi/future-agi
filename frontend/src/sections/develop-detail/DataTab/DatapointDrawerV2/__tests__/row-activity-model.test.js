// Row activity (dataset.rowExtras.activity): the pure parts of the history
// read. GET …/rows/{row_id}/activity/?page_size=&cursor= answers
// {events, next_cursor, has_more}, newest first; ids are strings.
import { describe, expect, it } from "vitest";
import {
  ROW_ACTIVITY_PAGE_SIZE,
  activityEvents,
  nextActivityCursor,
} from "src/api/develop/row-view";
import {
  activityCounts,
  activityErrorReason,
  activityListExpired,
  filterActivity,
} from "../activityModel";

const ev = (id, type = "edit", extra = {}) => ({
  id,
  type,
  at: "2026-11-09T10:00:00Z",
  ...extra,
});

describe("row activity pages", () => {
  it("joins pages in server order with one entry per event id", () => {
    const data = {
      pages: [
        { events: [ev("9"), ev("8")], next_cursor: "a", has_more: true },
        // A page can repeat an event the previous page already listed.
        { events: [ev("8"), ev("7")], next_cursor: null, has_more: false },
      ],
      pageParams: [null, "a"],
    };
    expect(activityEvents(data).map((e) => e.id)).toEqual(["9", "8", "7"]);
    // Numeric and string ids name the same event.
    expect(
      activityEvents({ pages: [{ events: [ev(5)] }, { events: [ev("5")] }] }),
    ).toHaveLength(1);
    // Events without an id are kept as they come.
    const runs = [{ type: "run" }, { type: "run" }];
    expect(activityEvents({ pages: [{ events: runs }] })).toHaveLength(2);
    expect(activityEvents(undefined)).toEqual([]);
    expect(activityEvents({ pages: [{}] })).toEqual([]);
  });

  it("asks for an older page only while the server says there is one", () => {
    expect(ROW_ACTIVITY_PAGE_SIZE).toBe(50);
    expect(nextActivityCursor({ has_more: true, next_cursor: "c1" })).toBe(
      "c1",
    );
    expect(
      nextActivityCursor({ has_more: false, next_cursor: null }),
    ).toBeUndefined();
    // Never send back a null or empty cursor.
    expect(
      nextActivityCursor({ has_more: true, next_cursor: null }),
    ).toBeUndefined();
    expect(
      nextActivityCursor({ has_more: true, next_cursor: "" }),
    ).toBeUndefined();
    // An answer without paging fields is a single page.
    expect(nextActivityCursor({ events: [] })).toBeUndefined();
    expect(nextActivityCursor(undefined)).toBeUndefined();
  });
});

describe("row activity filters", () => {
  it("counts a restored row under Edits", () => {
    const list = [
      ev("4", "restored"),
      ev("3", "deleted"),
      ev("2", "edit"),
      ev("1", "created"),
    ];
    expect(filterActivity(list, "edits").map((e) => e.id)).toEqual([
      "4",
      "3",
      "2",
      "1",
    ]);
    expect(activityCounts(list)).toMatchObject({ all: 4, edits: 4 });
  });
});

describe("row activity read errors", () => {
  it("uses the server's reason for a refused read", () => {
    expect(
      activityErrorReason({
        status: false,
        detail: "You do not have permission to perform this action.",
        statusCode: 403,
      }),
    ).toBe("You do not have permission to perform this action.");
    expect(activityErrorReason({ statusCode: 403 })).toBe(
      "You don't have access to this row's activity.",
    );
    expect(
      activityErrorReason({
        status: false,
        detail: "Dataset row not found.",
        statusCode: 404,
      }),
    ).toBe("Dataset row not found.");
  });

  it("says plainly when the server failed or never answered", () => {
    expect(activityErrorReason({ statusCode: 500 })).toBe(
      "Something went wrong on our side. Try again.",
    );
    expect(
      activityErrorReason({
        message: "Network Error",
        transportCode: "ERR_NETWORK",
      }),
    ).toBe("The server didn't answer. Check your connection and try again.");
    expect(
      activityErrorReason({
        message: "timeout of 30000ms exceeded",
        transportCode: "ECONNABORTED",
      }),
    ).toBe("The server took too long to answer. Try again.");
    expect(activityErrorReason(new Error("boom"))).toBe(
      "Something went wrong. Try again.",
    );
    expect(activityErrorReason(null)).toBeNull();
  });

  it("treats a refused older page (400) as an expired list", () => {
    expect(
      activityListExpired({
        statusCode: 400,
        type: "validation_error",
        code: "invalid",
        attr: "cursor",
      }),
    ).toBe(true);
    expect(activityListExpired({ statusCode: 503 })).toBe(false);
    expect(activityListExpired({ message: "Network Error" })).toBe(false);
  });
});
