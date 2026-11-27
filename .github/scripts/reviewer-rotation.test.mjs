// Run: node --test .github/scripts/reviewer-rotation.test.mjs
import assert from "node:assert/strict";
import test from "node:test";

import {
  candidatesFor,
  hasHumanReviewRequest,
  isAssignerComment,
  parseAssignment,
  pickLeastLoaded,
  reassignDue,
  renderAssignment,
  workingMsBetween,
} from "./reviewer-rotation.mjs";

const HOUR = 3600 * 1000;

// Synthetic config: fake logins, not .github/reviewer-config.json.
const config = {
  fallback_lead: "lead-f",
  teams: {
    alpha: { lead: "lead-a", members: ["lead-a", "ann", "bob"] },
    beta: { lead: "lead-b", members: ["lead-b", "Bob", "cy"] },
    pair: { lead: "pa", members: ["pa", "pb"] },
  },
};
const team = (id) => config.teams[id];

test("candidates are the members of every matched team, deduplicated, never the author", () => {
  assert.deepEqual(
    candidatesFor([team("alpha"), team("beta")], config, "ANN"),
    ["lead-a", "bob", "lead-b", "cy"],
  );
});

test("no matched team falls back to fallback_lead", () => {
  assert.deepEqual(candidatesFor([], config, "ann"), ["lead-f"]);
});

test("author as the only member and as fallback_lead falls back to all members", () => {
  const solo = { lead: "lead-f", members: ["lead-f"] };
  assert.deepEqual(candidatesFor([solo], config, "lead-f"), [
    "lead-a",
    "ann",
    "bob",
    "lead-b",
    "cy",
    "pa",
    "pb",
  ]);
});

test("picks the least-loaded member, not the lead", () => {
  const load = new Map([
    ["lead-a", 3],
    ["ann", 0],
    ["bob", 1],
  ]);
  assert.equal(pickLeastLoaded(["lead-a", "ann", "bob"], load), "ann");
  assert.equal(
    pickLeastLoaded(["lead-a", "ann", "bob"], load, { exclude: ["ANN"] }),
    "bob",
  );
  // A login with no open requests counts as zero load.
  assert.equal(pickLeastLoaded(["lead-a", "dee"], load), "dee");
});

test("ties rotate on the seed, so a burst of PRs does not all land on one person", () => {
  const load = new Map([
    ["ann", 2],
    ["bob", 2],
    ["cy", 2],
  ]);
  const picks = [100, 101, 102].map((seed) =>
    pickLeastLoaded(["cy", "ann", "bob"], load, { seed }),
  );
  assert.deepEqual(new Set(picks), new Set(["ann", "bob", "cy"]));
});

test("nobody left after excluding author and current assignee returns null", () => {
  // Two-member team: one is the author, the other is the stale assignee.
  const candidates = candidatesFor([team("pair")], config, "pa");
  assert.equal(
    pickLeastLoaded(candidates, new Map(), { exclude: ["pb"] }),
    null,
  );
});

test("working time counts only 10:00-19:00 IST on weekdays", () => {
  // Fri 2026-11-27 17:00 IST -> Mon 2026-11-30 12:00 IST = 2 h Friday + 2 h Monday.
  const fri = Date.parse("2026-11-27T11:30:00Z");
  const mon = Date.parse("2026-11-30T06:30:00Z");
  assert.equal(workingMsBetween(fri, mon), 4 * HOUR);
  // Overnight Tue 18:00 IST -> Wed 09:00 IST holds 1 h.
  assert.equal(
    workingMsBetween(
      Date.parse("2026-12-01T12:30:00Z"),
      Date.parse("2026-12-02T03:30:00Z"),
    ),
    1 * HOUR,
  );
  assert.equal(workingMsBetween(mon, fri), 0);
});

test("assignment marker round-trips and ignores unknown keys", () => {
  const line = renderAssignment({
    login: "bob",
    at: "2026-11-26T05:00:00.000Z",
    round: 2,
  });
  assert.equal(
    line,
    "<!-- devflow-assigned:v1 login=bob at=2026-11-26T05:00:00.000Z by=reviewer-assigner round=2 -->",
  );
  assert.deepEqual(parseAssignment(`hello\n${line}\nmore`), {
    login: "bob",
    at: "2026-11-26T05:00:00.000Z",
    round: 2,
  });
  assert.equal(
    parseAssignment("<!-- devflow-assigned:v1 at=2026-11-26T05:00:00Z -->"),
    null,
  );
  assert.equal(parseAssignment("no marker"), null);
});

test("reassign is due after 4 working hours with no devflow review from the assignee", () => {
  const assignment = { login: "bob", at: "2026-11-30T04:30:00Z", round: 1 }; // Mon 10:00 IST
  const now = Date.parse("2026-11-30T09:00:00Z"); // Mon 14:30 IST: 4.5 working hours
  const early = Date.parse("2026-11-30T08:00:00Z"); // 3.5 working hours
  const review = (login, body, at = "2026-11-30T06:00:00Z") => ({
    user: { login },
    body,
    submitted_at: at,
  });

  assert.equal(reassignDue(assignment, [], now), true);
  assert.equal(reassignDue(assignment, [], early), false);
  assert.equal(
    reassignDue(
      assignment,
      [review("Bob", "<!-- devflow-review:v1 -->\nok")],
      now,
    ),
    false,
  );
  // A plain review, another person's agent review, or one from before the assignment does not count.
  assert.equal(
    reassignDue(assignment, [review("bob", "looks fine")], now),
    true,
  );
  assert.equal(
    reassignDue(assignment, [review("cy", "<!-- devflow-review:v1 -->")], now),
    true,
  );
  assert.equal(
    reassignDue(
      assignment,
      [review("bob", "<!-- devflow-review:v1 -->", "2026-11-29T00:00:00Z")],
      now,
    ),
    true,
  );
});

test("a request for the code-owner team is not a human request; any other is", () => {
  const user = [{ login: "ann" }];
  const owners = [{ slug: "internal-all-write" }];
  const none = { requested_reviewers: [], requested_teams: [] };
  assert.equal(hasHumanReviewRequest(none), false);
  assert.equal(
    hasHumanReviewRequest({ ...none, requested_teams: owners }),
    false,
  );
  assert.equal(
    hasHumanReviewRequest({
      requested_reviewers: user,
      requested_teams: owners,
    }),
    true,
  );
  assert.equal(
    hasHumanReviewRequest({ ...none, requested_teams: [{ slug: "backend" }] }),
    true,
  );
  assert.equal(
    hasHumanReviewRequest({
      ...none,
      requested_teams: [...owners, { slug: "backend" }],
    }),
    true,
  );
});

test("the assigner's comments are the App's and github-actions', nobody else's", () => {
  const by = (login) => ({ user: { login } });
  const app = "devflow-approver[bot]";
  assert.equal(isAssignerComment(by("devflow-approver[bot]"), app), true);
  assert.equal(isAssignerComment(by("Devflow-Approver[bot]"), app), true);
  assert.equal(isAssignerComment(by("github-actions[bot]"), app), true);
  assert.equal(isAssignerComment(by("someone"), app), false);
  assert.equal(isAssignerComment(by("devflow-approver"), app), false);
  assert.equal(isAssignerComment(by(""), ""), false);
});
