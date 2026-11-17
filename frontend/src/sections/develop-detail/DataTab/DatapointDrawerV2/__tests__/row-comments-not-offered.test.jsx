// Where the deployment does not offer row comments (the launch registry
// neither ships them nor has them made real), the Row view's Activity tab
// says so in one sentence and reads nothing from the comments routes.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "src/utils/test-utils";
import axios from "src/utils/axios";
import { setCollabOfferForTest } from "src/sections/common/Collab/collabOffer";

vi.mock("src/api/develop/row-view", async (importOriginal) => ({
  ...(await importOriginal()),
  useRowActivity: () => ({
    data: [],
    isLoading: false,
    isError: false,
    hasNextPage: false,
  }),
}));

const { default: RowActivityTab } = await import("../RowActivityTab");

const SENTENCE = "Row comments are not part of this release.";

const commentsCalls = (spy) =>
  spy.mock.calls.filter(([url]) => String(url).includes("/comments/"));

const mount = (search) => {
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

let get;
let post;
beforeEach(() => {
  // As a deployment reads it: the registry decides.
  setCollabOfferForTest({ all: false });
  get = vi.spyOn(axios, "get").mockResolvedValue({
    data: { result: { column_config: [] } },
  });
  post = vi.spyOn(axios, "post").mockResolvedValue({ data: {} });
});
afterEach(() => {
  cleanup();
  setCollabOfferForTest(null);
  vi.restoreAllMocks();
});

describe("Activity tab where row comments are not offered", () => {
  it("Comment on row's link shows the sentence, with no Retry, no comment box and no comments read", async () => {
    // The cell menu's Comment on row opens this link (commentDeepLink).
    mount("?row=r1&rowTab=activity&ract=comments&rcs=open&rcompose=1");
    expect(await screen.findByText(SENTENCE)).toBeTruthy();
    expect(screen.queryByText(/Couldn't load the comments/)).toBeNull();
    expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByText(/coming soon/i)).toBeNull();
    await new Promise((r) => setTimeout(r, 0));
    expect(commentsCalls(get)).toEqual([]);
    expect(commentsCalls(post)).toEqual([]);
  });

  it("the sentence is plain text, not an alert", async () => {
    mount("?row=r1&rowTab=activity&ract=comments");
    const line = await screen.findByText(SENTENCE);
    expect(line.closest("[role='alert']")).toBeNull();
    expect(line.textContent).toBe(SENTENCE);
  });

  it("the history view shows no comment box either", async () => {
    mount("?row=r1&rowTab=activity&ract=all");
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(commentsCalls(get)).toEqual([]);
  });
});
