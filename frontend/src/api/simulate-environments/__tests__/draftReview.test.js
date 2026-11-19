import { describe, it, expect } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { DraftReviewSchema } from "../draftReview.contract";
import {
  draftReviewKey,
  freshSampleReview,
  sampleFileEdits,
} from "../draftReview";
import {
  appendAcceptedEdits,
  sourceSummaryKey,
  upsertPersonalDataEdit,
} from "../sources";
import { ALL_SCENARIOS, sampleDraftReview } from "../_fixtures/draftReview";

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
      {
        source_id: "src_2",
        name: "orders.csv",
        ui: { parsed: { table: "orders", accepted: 500 } },
      },
    ]);
    appendAcceptedEdits(queryClient, envId, {
      accepted: [
        {
          id: "scenario-boundary",
          kind: "scenario",
          title: "Scenarios: a return on day 30 and on day 31",
          cite: { sourceId: "src_1", ref: "policy.pdf · p.1", at: "p1" },
          scenario: [{ t: "Return on day 30" }, { t: "Return on day 31" }],
        },
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
    const files = sampleFileEdits(review);
    expect(DraftReviewSchema.safeParse(review).error?.issues).toBe(undefined);
    // They join the sample, so its counts don't jump once it loads.
    expect(review.counts.total).toBe(sampleDraftReview().counts.total + 5);
    // One row per proposed scenario.
    expect(files.changes.map((one) => one.kind)).toEqual([
      "rule",
      "rule",
      "world_state",
      "scenario",
      "scenario",
    ]);
    expect(files.changes.every((one) => one.decision === "accepted")).toBe(
      true,
    );
    // Rules and records recheck every sample scenario; new scenarios add theirs.
    expect(new Set(files.changes.flatMap((one) => one.revalidate)).size).toBe(
      ALL_SCENARIOS.length + 2,
    );
    expect(files.failed.map((one) => one.fix)).toEqual(["supply", "attach"]);
    // A refetch (chat nudge, save) keeps what files added.
    const refetched = freshSampleReview(review);
    expect(refetched.counts.total).toBe(sampleDraftReview().counts.total + 5);
    expect(refetched.failed).toHaveLength(
      sampleDraftReview().failed.length + 2,
    );
    expect(files.changes[0].sources[0].used[0]).toMatchObject({
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
    const first = sampleFileEdits(reviewIn(queryClient)).changes[0].summary;
    upsertPersonalDataEdit(queryClient, envId, {
      ...orders,
      ui: { personal_choice: "keep" },
    });
    const { changes } = sampleFileEdits(reviewIn(queryClient));
    expect(changes).toHaveLength(1);
    expect(changes[0].summary).not.toBe(first);
    expect(changes[0].decision).toBe("accepted");
  });

  it("clears a failed row once its table is added, and moves the world off generated data once", () => {
    const queryClient = clientWith([
      {
        source_id: "src_2",
        name: "orders.csv",
        ui: { parsed: { table: "orders" } },
      },
      {
        source_id: "src_4",
        name: "customers.csv",
        ui: { parsed: { table: "customers" } },
      },
    ]);
    const table = (sourceId) => ({
      id: `table-${sourceId}`,
      kind: "table",
      title: "New table",
      cite: { sourceId, ref: "file" },
    });
    appendAcceptedEdits(queryClient, envId, {
      accepted: [table("src_2")],
      failed: [
        {
          id: "missing-src_2-customer_id",
          kind: "missing",
          title: "Missing table: customers",
          sourceId: "src_2",
          needs: "customers",
          blocking: true,
        },
      ],
    });
    appendAcceptedEdits(queryClient, envId, { accepted: [table("src_4")] });
    appendAcceptedEdits(queryClient, envId, { accepted: [table("src_2")] });

    const review = reviewIn(queryClient);
    expect(sampleFileEdits(review).failed).toEqual([]);
    expect(
      review.changes
        .filter((one) => one.patch.some((op) => op.path === "/world_data"))
        .map((one) => one.id),
    ).toEqual(["world_state:table-src_2"]);
  });
});
