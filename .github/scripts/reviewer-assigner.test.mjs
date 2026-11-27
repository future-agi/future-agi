// Run: node --test .github/scripts/reviewer-assigner.test.mjs
// Runs the sweep as a child process against a fake GitHub and counts its API calls.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { renderAssignment } from "./reviewer-rotation.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const BOT = "approver-app[bot]";

// Preloaded into the sweep: replaces fetch, logs every call, serves one or two
// GraphQL pages and the write-access collaborators.
const FAKE_GITHUB = `
import { appendFileSync, readFileSync } from "node:fs";
const log = (c) => appendFileSync(process.env.CALL_LOG, JSON.stringify(c) + "\\n");
let pages = 0;
globalThis.fetch = async (url, init = {}) => {
  const path = String(url).replace("https://api.github.com", "");
  const method = init.method || "GET";
  log(path === "/graphql" ? { method, path } : { method, path, body: init.body });
  const reply = (body) => ({ ok: true, status: 200, headers: { get: () => null }, json: async () => body, text: async () => "" });
  if (method === "POST" && path === "/graphql") {
    pages += 1;
    const file = pages === 1 ? process.env.FAKE_PRS : process.env.FAKE_PRS_PAGE2;
    const nodes = file ? JSON.parse(readFileSync(file, "utf8")) : [];
    const more = pages === 1 && Boolean(process.env.FAKE_PRS_PAGE2);
    return reply({ data: { repository: { pullRequests: { pageInfo: { hasNextPage: more, endCursor: more ? "c1" : null }, nodes } } } });
  }
  if (method === "GET" && path === "/repos/o/r/collaborators?permission=push&per_page=100")
    return reply(JSON.parse(process.env.FAKE_WRITERS).map((login) => ({ login })));
  if (method !== "GET") return reply({});
  return { ok: false, status: 500, text: async () => "unexpected read " + path };
};
`;
const config = JSON.parse(
  readFileSync(resolve(here, "..", "reviewer-config.json"), "utf8"),
);
const WRITERS = [...Object.keys(config.users), "some-author"];

const marker = (login, at) => ({
  databaseId: 1,
  body: `${renderAssignment({ login, at, round: 1 })}\nassigned`,
  author: { login: BOT },
});

function pr(number, over = {}) {
  return {
    number,
    isDraft: false,
    isCrossRepository: false,
    createdAt: new Date().toISOString(),
    author: { login: "some-author" },
    reviewRequests: { nodes: [] },
    latestReviews: { nodes: [] },
    comments: { nodes: [] },
    ...over,
  };
}
const requestedFrom = (login) => ({
  nodes: [{ requestedReviewer: { __typename: "User", login } }],
});

function sweep(prs, writers = WRITERS, page2 = null) {
  const dir = mkdtempSync(join(tmpdir(), "assigner-"));
  try {
    const preload = join(dir, "fake-github.mjs");
    writeFileSync(preload, FAKE_GITHUB);
    writeFileSync(join(dir, "prs.json"), JSON.stringify(prs));
    if (page2) writeFileSync(join(dir, "prs2.json"), JSON.stringify(page2));
    const run = spawnSync(
      process.execPath,
      [
        "--import",
        pathToFileURL(preload).href,
        resolve(here, "reviewer-assigner.mjs"),
      ],
      {
        env: {
          ...process.env,
          MODE: "sweep",
          GITHUB_TOKEN: "t",
          GITHUB_REPOSITORY: "o/r",
          ASSIGNER_LOGIN: BOT,
          CALL_LOG: join(dir, "calls.jsonl"),
          FAKE_PRS: join(dir, "prs.json"),
          FAKE_WRITERS: JSON.stringify(writers),
          ...(page2 ? { FAKE_PRS_PAGE2: join(dir, "prs2.json") } : {}),
        },
        encoding: "utf8",
      },
    );
    const calls = readFileSync(join(dir, "calls.jsonl"), "utf8")
      .trim()
      .split("\n")
      .map((l) => JSON.parse(l));
    return { run, calls };
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test("a sweep of 50 open PRs reads once and writes only for the PRs that need it", () => {
  const prs = [];
  const now = new Date().toISOString();
  // 1-3 drafts, 4-7 need an assignment, 8-10 are overdue, 11-50 are settled.
  for (let n = 1; n <= 3; n++) prs.push(pr(n, { isDraft: true }));
  for (let n = 4; n <= 7; n++) prs.push(pr(n));
  for (let n = 8; n <= 10; n++)
    prs.push(
      pr(n, {
        reviewRequests: requestedFrom("khushalsonawat"),
        comments: { nodes: [marker("khushalsonawat", "2020-01-06T05:00:00Z")] },
      }),
    );
  for (let n = 11; n <= 50; n++)
    prs.push(
      pr(n, {
        reviewRequests: requestedFrom("cdileep23"),
        comments: { nodes: [marker("cdileep23", now)] },
      }),
    );

  const { run, calls } = sweep(prs);
  assert.equal(run.status, 0, run.stderr);

  const reads = calls.filter(
    (c) => c.method === "GET" || c.path === "/graphql",
  );
  const writes = calls.filter(
    (c) => c.method !== "GET" && c.path !== "/graphql",
  );
  assert.ok(reads.length <= 2, `reads: ${reads.length}`);
  assert.ok(
    !calls.some((c) => c.path.startsWith("/search")),
    "no search calls",
  );

  const touched = new Set(
    writes.map((w) => Number(w.path.match(/\/(\d+)\//)?.[1])),
  );
  // Comment edits address the comment id, not the PR, so check the PR-addressed writes.
  const byPr = writes.filter((w) => /\/(pulls|issues)\/\d+\//.test(w.path));
  for (const n of touched)
    if (!Number.isNaN(n)) assert.ok(n >= 4 && n <= 10, `PR #${n}`);
  // 4 assignments x (request + comment) + 3 reassignments x (delete + request + comment edit).
  assert.equal(writes.length, 4 * 2 + 3 * 3);
  assert.equal(byPr.length, 4 * 2 + 3 * 2);
});

test("a sweep with nothing to assign makes no write", () => {
  const prs = [pr(1, { isDraft: true })];
  const now = new Date().toISOString();
  for (let n = 2; n <= 20; n++)
    prs.push(
      pr(n, {
        reviewRequests: requestedFrom("cdileep23"),
        comments: { nodes: [marker("cdileep23", now)] },
      }),
    );
  const { run, calls } = sweep(prs);
  assert.equal(run.status, 0, run.stderr);
  assert.deepEqual(calls, [{ method: "POST", path: "/graphql" }]);
});

test("an external author's PR is never assigned or reassigned", () => {
  const prs = [
    pr(1, { author: { login: "outsider" } }),
    pr(2, {
      author: { login: "outsider" },
      reviewRequests: requestedFrom("khushalsonawat"),
      comments: { nodes: [marker("khushalsonawat", "2020-01-06T05:00:00Z")] },
    }),
  ];
  const { run, calls } = sweep(prs);
  assert.equal(run.status, 0, run.stderr);
  assert.deepEqual(
    calls.filter((c) => c.method !== "GET" && c.path !== "/graphql"),
    [],
  );
});

test("a PR opened more than a week ago is left alone, so the backlog never rotates", () => {
  const old = new Date(Date.now() - 8 * 24 * 3600 * 1000).toISOString();
  const { run, calls } = sweep([pr(1, { createdAt: old })]);
  assert.equal(run.status, 0, run.stderr);
  assert.deepEqual(calls, [{ method: "POST", path: "/graphql" }]);
});

test("the sweep reads newest first and stops at the first page past a week", () => {
  const source = readFileSync(resolve(here, "reviewer-assigner.mjs"), "utf8");
  assert.match(source, /orderBy: \{ field: CREATED_AT, direction: DESC \}/);
  const old = new Date(Date.now() - 8 * 24 * 3600 * 1000).toISOString();
  // Page 2 holds a PR that would be assigned; reading it would be a write.
  const { run, calls } = sweep([pr(1, { createdAt: old })], WRITERS, [pr(2)]);
  assert.equal(run.status, 0, run.stderr);
  assert.deepEqual(calls, [{ method: "POST", path: "/graphql" }]);
});

test("a page of recent PRs reads the next page", () => {
  const now = new Date().toISOString();
  const settled = pr(1, {
    reviewRequests: requestedFrom("cdileep23"),
    comments: { nodes: [marker("cdileep23", now)] },
  });
  const { run, calls } = sweep([settled], WRITERS, [pr(2)]);
  assert.equal(run.status, 0, run.stderr);
  assert.equal(calls.filter((c) => c.path === "/graphql").length, 2);
  assert.ok(
    calls.some((c) => c.path === "/repos/o/r/pulls/2/requested_reviewers"),
  );
});

test("anyone on the roster reviews any PR: teams do not narrow the pool", () => {
  // atharva-bhange owns none of the simulate paths, and is the only reviewer who can push.
  const { run, calls } = sweep(
    [pr(1, { files: { nodes: [{ path: "futureagi/simulate/a.py" }] } })],
    ["some-author", "atharva-bhange"],
  );
  assert.equal(run.status, 0, run.stderr);
  const requests = calls.filter((c) => c.path.endsWith("/requested_reviewers"));
  assert.deepEqual(
    requests.map((c) => JSON.parse(c.body).reviewers),
    [["atharva-bhange"]],
  );
});

test("only a reviewer with write access is requested", () => {
  // The owning team has five members; only cdileep23 can push here.
  const { run, calls } = sweep([pr(1)], ["some-author", "cdileep23"]);
  assert.equal(run.status, 0, run.stderr);
  const requests = calls.filter((c) => c.path.endsWith("/requested_reviewers"));
  assert.deepEqual(
    requests.map((c) => JSON.parse(c.body).reviewers),
    [["cdileep23"]],
  );
});

test("an outside contributor's fork PR is never assigned or reassigned", () => {
  const prs = [
    pr(1, { isCrossRepository: true }),
    pr(2, {
      isCrossRepository: true,
      reviewRequests: requestedFrom("khushalsonawat"),
      comments: { nodes: [marker("khushalsonawat", "2020-01-06T05:00:00Z")] },
    }),
  ];
  const { run, calls } = sweep(prs);
  assert.equal(run.status, 0, run.stderr);
  assert.deepEqual(calls, [{ method: "POST", path: "/graphql" }]);
});
