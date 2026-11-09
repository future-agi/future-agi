// @vitest-environment jsdom
// W9 A4 (W9-SCOPE §5): the old per-browser pins are POSTed as stars once
// (oldest first, so the newest pin is the newest star), then cleared; a
// deleted dataset's pin is dropped; a pin that failed otherwise stays.
/* eslint-disable react/prop-types */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, waitFor } from "@testing-library/react";

const post = vi.fn();
vi.mock("src/utils/axios", () => ({
  default: { post: (...a) => post(...a), delete: vi.fn() },
  endpoints: {
    develop: {
      w9: { datasetStar: (id) => `/star/${id}/` },
      updateDataset: (id) => `/edit/${id}/`,
    },
    agentPlayground: { listGraphs: "/graphs/" },
  },
}));

const {
  default: useDatasetStars,
  migratePins,
  pinnedIdsOf,
  PINS_SURFACE,
} = await import("../useDatasetStars");
const { LAYOUT_VERSION, getLayout, layoutKey, setLayout } = await import(
  "src/sections/common/ux/oneview/layoutPrefs"
);

const KEY = layoutKey(null, PINS_SURFACE);
function Host({ onDone }) {
  useDatasetStars({ onDone });
  return null;
}

beforeEach(() => {
  post.mockReset();
  window.localStorage.clear();
});

describe("migratePins", () => {
  it("stars oldest first; 404 drops the pin; other failures keep it", async () => {
    const star = vi.fn(async (id) => {
      if (id === "gone") throw { response: { status: 404 } };
      if (id === "flaky") throw { response: { status: 500 } };
      return { data: { result: { dataset_id: id, starred: true } } };
    });
    const res = await migratePins(["newest", "flaky", "gone", "oldest"], star);
    expect(star.mock.calls.map((c) => c[0])).toEqual([
      "oldest",
      "gone",
      "flaky",
      "newest",
    ]);
    expect(res).toEqual({
      starred: ["oldest", "gone", "newest"],
      kept: ["flaky"],
    });
  });

  it("pinnedIdsOf reads extras.pinned only", () => {
    expect(pinnedIdsOf({ extras: { pinned: ["a", "", 3, "b"] } })).toEqual([
      "a",
      "b",
    ]);
    expect(pinnedIdsOf({})).toEqual([]);
  });
});

describe("useDatasetStars", () => {
  it("runs once: stars each pin, clears the key, reports the stars", async () => {
    setLayout(KEY, { v: LAYOUT_VERSION, extras: { pinned: ["b", "a"] } });
    post.mockResolvedValue({ data: { result: { starred: true } } });
    const onDone = vi.fn();
    const { rerender } = render(<Host onDone={onDone} />);
    await waitFor(() => expect(onDone).toHaveBeenCalledWith(["a", "b"]));
    expect(post.mock.calls.map((c) => c[0])).toEqual(["/star/a/", "/star/b/"]);
    expect(getLayout(KEY).extras.pinned).toEqual([]);
    rerender(<Host onDone={onDone} />);
    expect(post).toHaveBeenCalledTimes(2);
  });

  it("does nothing without pins", () => {
    render(<Host />);
    expect(post).not.toHaveBeenCalled();
  });
});
