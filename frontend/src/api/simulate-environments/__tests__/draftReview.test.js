import { describe, it, expect } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { DraftReviewSchema } from "../draftReview.contract";
import { draftReviewKey, freshSampleReview } from "../draftReview";
import {
  appendAcceptedEdits,
  sourceSummaryKey,
  upsertPersonalDataEdit,
} from "../sources";
import { sampleDraftReview } from "../_fixtures/draftReview";

describe("draft review contract", () => {
  it("the sample review keeps to the backend's contract", () => {
    expect(DraftReviewSchema.safeParse(sampleDraftReview()).error?.issues).toBe(
      undefined,
    );
  });

  it("refuses what the backend's rules can't produce", () => {
    const review = sampleDraftReview();
    const rule = review.changes.find((one) => one.kind === "rule");
    const broken = {
      ...review,
      changes: review.changes.map((one) =>
        one === rule ? { ...one, depends_on: ["tool:issue_refund"] } : one,
      ),
    };
    expect(DraftReviewSchema.safeParse(broken).success).toBe(false);
  });
});

describe("edits added from files", () => {
  const envId = "env-1";
  const clientWith = (sources) => {
    const queryClient = new QueryClient();
    queryClient.setQueryData(sourceSummaryKey(envId), { sources });
    return queryClient;
  };
  const reviewIn = (queryClient) =>
    queryClient.getQueryData(draftReviewKey(envId));

  it("adds accepted items as accepted rows and blocked ones as failed", () => {
    const queryClient = clientWith([
      { source_id: "src_1", name: "policy.pdf" },
      { source_id: "src_2", name: "orders.csv" },
    ]);
    appendAcceptedEdits(queryClient, envId, {
      accepted: [
        {
          id: "rule-src_1-p2",
          kind: "rule",
          title: "Rule: refunds within 30 days",
          rule: "refunds within 30 days",
          edited: "refunds within 14 days",
          cite: { sourceId: "src_1", ref: "policy.pdf · p.2", at: "p2" },
        },
        {
          id: "conflict-window",
          kind: "conflict",
          title: "Two policies set the return window differently",
          pick: { sourceId: "src_1", rule: "returns within 30 days" },
        },
        {
          id: "table-src_2",
          kind: "table",
          title: "New table orders: 500 rows",
          cite: { sourceId: "src_2", ref: "orders.csv" },
        },
      ],
      failed: [
        {
          id: "missing-src_2-customer_id",
          kind: "missing",
          title: "Missing table: customers",
          detail: "No customers table for orders.customer_id.",
          sourceId: "src_2",
          blocking: true,
        },
        {
          id: "unreadable-src_3",
          kind: "unreadable",
          title: "scan.pdf couldn't be read",
          detail: null,
          cite: { sourceId: "src_3", ref: "scan.pdf" },
          blocking: true,
        },
      ],
    });
    const review = reviewIn(queryClient);
    expect(DraftReviewSchema.safeParse(review).error?.issues).toBe(undefined);
    expect(review.counts).toEqual({
      total: 3,
      accepted: 3,
      rejected: 0,
      undecided: 0,
    });
    expect(review.changes.map((one) => one.kind)).toEqual([
      "rule",
      "rule",
      "world_state",
    ]);
    expect(review.failed.map((one) => one.fix)).toEqual(["supply", "attach"]);
    // A refetch (chat nudge, save) keeps what files added.
    const refetched = freshSampleReview(review);
    expect(refetched.counts.total).toBe(sampleDraftReview().counts.total + 3);
    expect(refetched.failed).toHaveLength(
      sampleDraftReview().failed.length + 2,
    );
    expect(review.changes[0].sources[0].used[0]).toMatchObject({
      source_id: "src_1",
      name: "policy.pdf",
      locator: "p2",
    });
  });

  it("keeps one personal data row per file as the choice changes", () => {
    const orders = {
      source_id: "src_2",
      name: "orders.csv",
      status: "ready",
      ui: { personal_choice: "replace" },
    };
    const queryClient = clientWith([orders]);
    upsertPersonalDataEdit(queryClient, envId, orders);
    const first = reviewIn(queryClient).changes[0].summary;
    upsertPersonalDataEdit(queryClient, envId, {
      ...orders,
      ui: { personal_choice: "keep" },
    });
    const { changes } = reviewIn(queryClient);
    expect(changes).toHaveLength(1);
    expect(changes[0].summary).not.toBe(first);
    expect(changes[0].decision).toBe("accepted");
  });
});
