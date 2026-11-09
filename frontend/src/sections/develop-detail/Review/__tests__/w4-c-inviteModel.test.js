// W4 C3: the invite form's pure helpers (sample size and words, split,
// filters as R3 pills, statuses, emails, the POST body and its problems).
import { describe, expect, it } from "vitest";
import {
  filterLabel,
  initialInvite,
  inviteBody,
  inviteProblem,
  inviteStatus,
  outsideProblem,
  parseEmails,
  perReviewerCounts,
  sampleLabel,
  samplePills,
} from "../inviteModel";

const POLICY = {
  max_days: 30,
  expiry_days: [1, 7, 14, 30],
  defaults: { percent: 20, seed: 7, expires_in_days: 7 },
};

describe("sample words", () => {
  it("reads like the Items menu", () => {
    expect(sampleLabel(30, { mode: "all" })).toBe("All 30 items");
    expect(sampleLabel(30, { mode: "percent", value: 20 })).toBe(
      "A random 20% (6 items)",
    );
    expect(sampleLabel(30, { mode: "count", value: 6 })).toBe(
      "A random 6 items",
    );
    expect(perReviewerCounts(6, 2, "same")).toEqual([6, 6]);
    expect(perReviewerCounts(5, 2, "split")).toEqual([3, 2]);
  });

  it("filters become R3 pills over the queue's runs", () => {
    expect(samplePills("all", {})).toEqual([]);
    expect(
      samplePills("failing", { mode: "rubric", metric: "m", runId: "r" }),
    ).toEqual([{ type: "fails", metric: "m", run: "r" }]);
    expect(
      samplePills("disagree", {
        mode: "pairwise",
        metric: "m",
        runA: "a",
        runB: "b",
      }),
    ).toEqual([{ type: "movement", dir: "changed", metric: "m", run: "b" }]);
    expect(
      samplePills("disagree", { mode: "rubric", metric: "m", runId: "r" }),
    ).toBeNull();
    expect(samplePills("failing", { mode: "rubric", runId: "r" })).toBeNull();
    expect(filterLabel("failing", { mode: "rubric", label: "#4A" })).toBe(
      "Failing on #4A",
    );
    expect(filterLabel("disagree", { labelA: "#3A", labelB: "#4A" })).toBe(
      "Where #4A and #3A disagree",
    );
  });
});

describe("status and emails", () => {
  it("revoked, expired, waiting, done, active", () => {
    const base = {
      item_ids: ["a", "b"],
      expires_at: "2026-10-29T00:00:00Z",
      progress: { done: 0 },
    };
    const now = Date.parse("2026-10-22T00:00:00Z");
    expect(inviteStatus(base, now)).toBe("active");
    expect(inviteStatus({ ...base, needs_approval: true }, now)).toBe(
      "pending_approval",
    );
    expect(inviteStatus({ ...base, progress: { done: 2 } }, now)).toBe("done");
    expect(inviteStatus({ ...base, revoked_at: "x" }, now)).toBe("revoked");
    expect(inviteStatus(base, Date.parse("2026-10-30T00:00:00Z"))).toBe(
      "expired",
    );
  });

  it("parses pasted addresses", () => {
    expect(
      parseEmails(
        "QA1@vendor.test, qa2@vendor.test;qa1@vendor.test <qa3@x.io> nope",
      ),
    ).toEqual({
      emails: ["qa1@vendor.test", "qa2@vendor.test", "qa3@x.io"],
      invalid: ["nope"],
    });
  });
});

describe("form", () => {
  it("starts from the policy and builds the POST body", () => {
    const v = {
      ...initialInvite(POLICY),
      emails: ["qa1@vendor.test"],
      mode: "percent",
    };
    expect(v).toMatchObject({
      percent: 20,
      seed: 7,
      days: 7,
      split: "same",
      filter: "all",
    });
    expect(inviteBody(v, { metric: "eval:x" })).toEqual({
      emails: ["qa1@vendor.test"],
      sample: {
        mode: "percent",
        value: 20,
        seed: 7,
        filter: { key: "all", metric: "eval:x" },
        split: "same",
      },
      expires_in_days: 7,
    });
  });

  it("says what blocks sending", () => {
    const v = initialInvite(POLICY);
    expect(inviteProblem(v, 30)).toBe("Add at least one email");
    expect(inviteProblem({ ...v, emails: ["bad"] }, 30)).toMatch(
      /not an email/,
    );
    expect(inviteProblem({ ...v, emails: ["a@b.co"] }, 0)).toBe(
      "The sample has no items",
    );
    expect(
      inviteProblem(
        {
          ...v,
          emails: ["a@b.co", "c@d.co"],
          mode: "count",
          count: 1,
          split: "split",
        },
        30,
      ),
    ).toMatch(/can't be shared/);
    expect(inviteProblem({ ...v, emails: ["a@b.co"] }, 30)).toBeNull();
    expect(outsideProblem({ ...v, on: false }, 0)).toBeNull();
  });
});
