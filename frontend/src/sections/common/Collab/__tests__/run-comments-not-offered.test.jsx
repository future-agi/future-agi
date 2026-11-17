// Where the deployment does not offer row comments (collabOffer.js), the
// comments under a run's answer (the results drawer's Review tab, and a
// dataset row's run detail) are one calm sentence: no composer, no empty
// list that invites a comment, and nothing read from or sent to the
// comments routes.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import axios from "src/utils/axios";
import { setCollabOfferForTest } from "src/sections/common/Collab/collabOffer";

vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: () => ({ data: null }),
}));
// The composer stands in as the text box a person would type into.
vi.mock("../CommentComposer", async () => {
  const { createElement } = await import("react");
  return {
    default: () =>
      createElement("textarea", {
        "aria-label": "Comment on",
        "data-testid": "composer",
      }),
  };
});

const { default: RunCommentsSection } = await import("../RunCommentsSection");
const { default: CommentThreads } = await import("../CommentThread");

const SENTENCE = "Row comments are not part of this release.";

const commentsCalls = (spy) =>
  spy.mock.calls.filter(([url]) => String(url).includes("/comments/"));

const mount = (node) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter>{node}</MemoryRouter>
    </QueryClientProvider>,
  );

let get;
let post;
beforeEach(() => {
  setCollabOfferForTest({ all: false });
  get = vi.spyOn(axios, "get").mockResolvedValue({ data: { result: {} } });
  post = vi.spyOn(axios, "post").mockResolvedValue({ data: { result: {} } });
});
afterEach(() => {
  cleanup();
  setCollabOfferForTest(null);
  vi.restoreAllMocks();
});

describe("comments on a run's answer where row comments are not offered", () => {
  it("the answer's comments section shows the sentence, with no composer and no count", async () => {
    mount(
      <RunCommentsSection
        datasetId="ds-1"
        rowId="row-12"
        runId="run-4a"
        runLabel="#4A"
      />,
    );
    expect(await screen.findByText(SENTENCE)).toBeTruthy();
    const section = screen.getByTestId("run-comments");
    expect(section.textContent).not.toContain("(0)");
    expect(section.textContent).not.toContain(
      "No comments on this answer yet.",
    );
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
    expect(screen.queryByText(/coming soon/i)).toBeNull();
    await new Promise((r) => setTimeout(r, 0));
    expect(commentsCalls(get)).toEqual([]);
    expect(commentsCalls(post)).toEqual([]);
  });

  it("any host of the threads gets the sentence as plain text, not an alert", async () => {
    mount(<CommentThreads datasetId="ds-1" rowId="row-12" runId="run-4a" />);
    const line = await screen.findByText(SENTENCE);
    expect(line.textContent).toBe(SENTENCE);
    expect(line.closest("[role='alert']")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByTestId("comment-threads")).toBeNull();
    await new Promise((r) => setTimeout(r, 0));
    expect(commentsCalls(get)).toEqual([]);
  });

  it("where row comments are offered, the section keeps its count and threads", async () => {
    setCollabOfferForTest({
      all: false,
      madeReal: ["dataset.collab.comments"],
    });
    get.mockResolvedValue({ data: { result: { threads: [] } } });
    mount(
      <RunCommentsSection
        datasetId="ds-1"
        rowId="row-12"
        runId="run-4a"
        runLabel="#4A"
      />,
    );
    expect(
      await screen.findByText("No comments on this answer yet."),
    ).toBeTruthy();
    expect(screen.queryByText(SENTENCE)).toBeNull();
    expect(screen.getByTestId("run-comments").textContent).toContain("(0)");
  });
});
