import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SourceReviewPanel from "../SourceReviewPanel";
import { sourceReviewKey } from "src/api/simulate-environments/sourceImports";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", () => ({ default: api }));

const path = "/simulate/api/harness-source-reviews/review-1/";
const preview = {
  draft_digest: "sha256:before",
  sources: [
    {
      pin: { source_id: "orders", role: "seed_records" },
      filename: "orders.csv",
      fields: [{ source_field: "order_id" }],
      row_count: 1,
    },
  ],
};
const repair = {
  id: "review-1",
  scope: { kind: "design", id: "job-1", schema_digest: "sha256:schema" },
  state: "needs_repair",
  revision: 2,
  draft_digest: preview.draft_digest,
  preview,
};
let serverReview;

beforeEach(() => {
  serverReview = repair;
  api.get.mockReset().mockImplementation(async () => ({ data: serverReview }));
  api.post.mockReset();
});

function mount(onAccepted, cachedReview) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  if (cachedReview)
    client.setQueryData(sourceReviewKey("review-1"), cachedReview);
  return render(
    <QueryClientProvider client={client}>
      <SourceReviewPanel
        reviewId="review-1"
        ownerId="job-1"
        canEdit
        acceptLabel="Approve sources and start build"
        onAccepted={onAccepted}
      />
    </QueryClientProvider>,
  );
}

describe("Prebuild source review", () => {
  it("reauthorizes the server preview before displaying cached source details", async () => {
    api.get.mockRejectedValueOnce(new Error("This review is not available."));
    mount(undefined, repair);
    expect(screen.queryByText("orders.csv")).toBeNull();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This review is not available.",
    );
    expect(screen.queryByText("orders.csv")).toBeNull();
  });

  it("keeps inferred-schema edits out of a review pinned to a template table", async () => {
    serverReview = {
      ...repair,
      scope: { ...repair.scope, target_table: "orders" },
    };
    mount();
    expect(await screen.findByText("orders.csv")).toBeInTheDocument();
    expect(
      screen.queryByRole("combobox", { name: "Identify records by" }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: "Prepare again" })).toBeNull();
    expect(screen.getByRole("button", { name: "Cancel review" })).toBeEnabled();
  });

  it("repairs inferred fields and launches only after approving the new revision", async () => {
    const onAccepted = vi.fn();
    api.post
      .mockImplementationOnce(async () => {
        serverReview = {
          ...repair,
          scope: { ...repair.scope, schema_digest: "sha256:new-schema" },
          state: "ready",
          revision: 3,
          draft_digest: "sha256:repaired",
          preview: { ...preview, draft_digest: "sha256:repaired" },
        };
        return { data: serverReview };
      })
      .mockImplementationOnce(async () => {
        serverReview = { ...serverReview, state: "accepted", revision: 4 };
        return { data: serverReview };
      });
    mount(onAccepted);
    expect(await screen.findByText("orders.csv")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Approve sources and start build" }),
    ).toBeNull();
    fireEvent.mouseDown(
      screen.getByRole("combobox", { name: "Identify records by" }),
    );
    fireEvent.click(screen.getByRole("option", { name: "order_id" }));
    fireEvent.keyDown(screen.getByRole("option", { name: "order_id" }), {
      key: "Escape",
    });
    fireEvent.click(screen.getByRole("button", { name: "Prepare again" }));
    const approve = await screen.findByRole("button", {
      name: "Approve sources and start build",
    });
    expect(onAccepted).not.toHaveBeenCalled();
    expect(api.post).toHaveBeenNthCalledWith(1, `${path}reprepare/`, {
      expected_revision: 2,
      idempotency_key: expect.any(String),
      schema_repairs: [{ source_id: "orders", identity_fields: ["order_id"] }],
    });

    fireEvent.click(approve);
    expect(
      await screen.findByText("Source review accepted."),
    ).toBeInTheDocument();
    expect(api.post).toHaveBeenNthCalledWith(
      2,
      `${path}accept/`,
      expect.objectContaining({
        decisions: expect.objectContaining({
          record_reviews: [
            expect.objectContaining({
              audience: { tested_agent: true, participant_ids: [] },
            }),
          ],
        }),
        expected_revision: 3,
        expected_schema_digest: "sha256:new-schema",
        expected_draft_digest: "sha256:repaired",
      }),
    );
    expect(onAccepted).toHaveBeenCalledExactlyOnceWith(serverReview);
  });
});
