import test from "node:test";
import assert from "node:assert/strict";
import {
  buildIssueGroups,
  extractClaimants,
  extractIssueRefs,
  renderTriageComment,
} from "./pr-triage.mjs";

test("extractIssueRefs returns unique explicit closing references", () => {
  assert.deepEqual(
    extractIssueRefs("Fixes #12 and closes #12; resolves #34, related to #56"),
    [12, 34],
  );
});

test("buildIssueGroups relates multiple PRs through the same open issue", () => {
  const issues = [{ number: 12, title: "Timezone bug" }];
  const pullRequests = [
    {
      number: 101,
      title: "Fix chart parsing",
      body: "Fixes #12",
      html_url: "https://github.com/future-agi/future-agi/pull/101",
      user: { login: "alice" },
      draft: false,
      updated_at: "2026-09-15T00:00:00Z",
    },
    {
      number: 102,
      title: "Preserve chart offsets",
      body: "Closes #12",
      html_url: "https://github.com/future-agi/future-agi/pull/102",
      user: { login: "bob" },
      draft: false,
      updated_at: "2026-09-15T00:00:00Z",
    },
  ];

  const groups = buildIssueGroups(issues, pullRequests);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].issue.number, 12);
  assert.deepEqual(groups[0].pullRequests.map((pr) => pr.number), [101, 102]);
});

test("buildIssueGroups ignores one-off references and issues not in the open issue set", () => {
  const pullRequests = [
    { number: 101, title: "Fix", body: "Fixes #12", user: { login: "alice" } },
    { number: 102, title: "Fix", body: "Fixes #99", user: { login: "bob" } },
  ];
  assert.deepEqual(buildIssueGroups([{ number: 12, title: "One PR" }], pullRequests), []);
});

test("extractClaimants only returns comments that claim work on the issue", () => {
  const claimants = extractClaimants([
    { body: "I am working on this", user: { login: "alice" }, html_url: "a" },
    { body: "This looks useful", user: { login: "bob" }, html_url: "b" },
    { body: "I'll take this issue", user: { login: "carol" }, html_url: "c" },
  ]);
  assert.deepEqual(claimants, [
    { login: "alice", url: "a" },
    { login: "carol", url: "c" },
  ]);
});

test("renderTriageComment is marked for idempotent updates", () => {
  const body = renderTriageComment({
    issue: { number: 12, title: "Timezone bug" },
    pullRequests: [
      { number: 101, title: "Fix chart parsing", url: "https://example.test/101", author: "alice", draft: false },
    ],
  });
  assert.match(body, /<!-- pr-triage: issue-12 -->/);
  assert.match(body, /canonical PR/);
});
