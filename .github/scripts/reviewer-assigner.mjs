#!/usr/bin/env node
// Reviewer Assigner: routes each PR to one owning-team member, reassigns stale
// requests, DM-pings stale reviewers, and posts a daily reviewer-load summary.
// Driven by .github/reviewer-config.json; selection logic is in reviewer-rotation.mjs.
//
// Modes (selected via MODE env var):
//   sweep    — one GraphQL read of every open PR; request the least-loaded member of the
//              owning team(s) where there is no reviewer, and move a request with no
//              devflow-review after 4 working hours to the next least-loaded member
//   ping     — DM each pending reviewer of an open PR >3 days old (gated by REVIEWER_PING_ENABLED)
//   summary  — post sorted "reviewer → open PR count" to SLACK_WEBHOOK_URL

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import {
  REASSIGN_AFTER_MS,
  candidatesFor,
  hasHumanReviewRequest,
  isAssignerComment,
  parseAssignment,
  pickLeastLoaded,
  reassignDue,
  renderAssignment,
  workingMsBetween,
} from "./reviewer-rotation.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const CONFIG_PATH = resolve(__dirname, "..", "reviewer-config.json");

const {
  MODE,
  GITHUB_TOKEN,
  GITHUB_REPOSITORY,
  PR_NUMBER,
  ASSIGNER_LOGIN,
  SLACK_BOT_TOKEN,
  SLACK_WEBHOOK_URL,
  REVIEWER_PING_ENABLED,
  DRY_RUN,
} = process.env;

if (!MODE) die("MODE env var is required (sweep|ping|summary)");
if (!GITHUB_TOKEN) die("GITHUB_TOKEN is required");
if (!GITHUB_REPOSITORY) die("GITHUB_REPOSITORY is required");

const [OWNER, REPO] = GITHUB_REPOSITORY.split("/");
const GH_API = "https://api.github.com";
const PING_AGE_DAYS = 3;
// Only pull requests opened in the last week rotate, so turning the sweep on
// never assigns the backlog.
const NEW_PR_MS = 7 * 24 * 3600 * 1000;
const dryRun = DRY_RUN === "true";

const config = JSON.parse(readFileSync(CONFIG_PATH, "utf8"));

const ghHeaders = {
  Accept: "application/vnd.github+json",
  Authorization: `Bearer ${GITHUB_TOKEN}`,
  "X-GitHub-Api-Version": "2022-11-28",
  "User-Agent": `${OWNER}-${REPO}-reviewer-assigner`,
};

// ─── small helpers ────────────────────────────────────────────────────────────

function die(msg) {
  console.error(msg);
  process.exit(1);
}

function log(...args) {
  console.log(...args);
}

async function gh(path, init = {}) {
  const res = await fetch(`${GH_API}${path}`, {
    ...init,
    headers: { ...ghHeaders, ...(init.headers || {}) },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(
      `GitHub ${init.method || "GET"} ${path} → ${res.status}: ${body}`,
    );
  }
  if (res.status === 204) return null;
  return res.json();
}

async function ghPaginated(path) {
  // Walk Link headers until no `rel="next"`.
  const out = [];
  let url = `${GH_API}${path}`;
  while (url) {
    const res = await fetch(url, { headers: ghHeaders });
    if (!res.ok)
      throw new Error(`GitHub GET ${url} → ${res.status}: ${await res.text()}`);
    out.push(...(await res.json()));
    const link = res.headers.get("link");
    const next = link
      ?.split(",")
      .map((s) => s.trim())
      .find((s) => s.endsWith('rel="next"'));
    url = next ? next.slice(1, next.indexOf(">")) : null;
  }
  return out;
}

// Convert a labeler-style glob (** and *) to a RegExp. Anchored at both ends.
function globToRegex(glob) {
  let re = "";
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === "*" && glob[i + 1] === "*") {
      re += ".*";
      i++;
    } else if (c === "*") {
      re += "[^/]*";
    } else if (/[.+?^${}()|[\]\\]/.test(c)) {
      re += "\\" + c;
    } else {
      re += c;
    }
  }
  return new RegExp("^" + re + "$");
}

// Specificity = length of the literal prefix before the first wildcard.
// Used to pick the owning team when multiple globs match the same file.
function globSpecificity(glob) {
  const i = glob.search(/[*?]/);
  return i === -1 ? glob.length : i;
}

// Precompile globs once.
const compiledTeams = Object.entries(config.teams).map(([id, t]) => ({
  id,
  ...t,
  compiledPaths: t.paths.map((p) => ({
    glob: p,
    re: globToRegex(p),
    specificity: globSpecificity(p),
  })),
}));

// Find the team that owns a given file path (most-specific-glob-wins).
function ownerOf(file) {
  let best = null;
  for (const team of compiledTeams) {
    for (const p of team.compiledPaths) {
      if (p.re.test(file)) {
        if (!best || p.specificity > best.specificity)
          best = { team, specificity: p.specificity };
        break; // one team can only "claim" via its most-specific own glob anyway
      }
    }
  }
  return best?.team || null;
}

// Find every team that has `gh-user` as its lead (a person may lead multiple teams).
function teamsLedBy(ghUser) {
  return compiledTeams.filter((t) => t.lead === ghUser);
}

function userMeta(ghUser) {
  return config.users[ghUser] || null;
}

function slackMention(ghUser) {
  const u = userMeta(ghUser);
  return u?.slack_id ? `<@${u.slack_id}>` : ghUser;
}

// ─── mode: sweep (assign + reassign) ──────────────────────────────────────────

// One paginated GraphQL read covers every open PR: reviewers, assignment marker,
// reviews and files. A sweep costs one request per PAGE_SIZE open PRs, plus
// writes for the PRs that need an assignment or a reassignment. The marker is
// posted when the PR opens, so the first 30 comments hold it.
const PAGE_SIZE = 50;
const SWEEP_QUERY = `query($owner: String!, $repo: String!, $after: String) {
  repository(owner: $owner, name: $repo) {
    pullRequests(states: OPEN, first: ${PAGE_SIZE}, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        isDraft
        isCrossRepository
        createdAt
        author { login }
        reviewRequests(first: 20) {
          nodes { requestedReviewer { __typename ... on User { login } ... on Team { slug } } }
        }
        latestReviews(first: 20) { nodes { author { login } body submittedAt } }
        comments(first: 30) { nodes { databaseId body author { login } } }
        files(first: 100) { nodes { path } }
      }
    }
  }
}`;

// GraphQL node → the REST-shaped PR that reviewer-rotation.mjs works on.
function normalise(n) {
  const asked = n.reviewRequests.nodes
    .map((r) => r.requestedReviewer)
    .filter(Boolean);
  return {
    number: n.number,
    draft: n.isDraft,
    fork: n.isCrossRepository,
    created_at: n.createdAt,
    author: n.author?.login,
    requested_reviewers: asked
      .filter((r) => r.__typename === "User")
      .map((r) => ({ login: r.login })),
    requested_teams: asked
      .filter((r) => r.__typename === "Team")
      .map((r) => ({ slug: r.slug })),
    comments: n.comments.nodes.map((c) => ({
      id: c.databaseId,
      body: c.body,
      user: { login: c.author?.login },
    })),
    reviews: n.latestReviews.nodes.map((r) => ({
      user: { login: r.author?.login },
      body: r.body,
      submitted_at: r.submittedAt,
    })),
    files: n.files.nodes.map((f) => f.path),
  };
}

async function openPrs() {
  const out = [];
  let after = null;
  do {
    const res = await gh("/graphql", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query: SWEEP_QUERY,
        variables: { owner: OWNER, repo: REPO, after },
      }),
    });
    if (res.errors?.length)
      throw new Error(
        `GitHub GraphQL: ${res.errors.map((e) => e.message).join("; ")}`,
      );
    const page = res.data.repository.pullRequests;
    out.push(...page.nodes.map(normalise));
    after = page.pageInfo.hasNextPage ? page.pageInfo.endCursor : null;
  } while (after);
  return out;
}

// login → open non-draft PRs with a review requested from them, counted from
// the sweep's own read (no search calls). Logins compare case-insensitively.
const loads = {
  counts: new Map(),
  get(login) {
    return this.counts.get(login.toLowerCase());
  },
  add(login, delta) {
    const k = login.toLowerCase();
    this.counts.set(k, Math.max(0, (this.counts.get(k) ?? 0) + delta));
  },
};

async function requestReviewer(prNumber, login) {
  await gh(`/repos/${OWNER}/${REPO}/pulls/${prNumber}/requested_reviewers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reviewers: [login] }),
  });
  loads.add(login, 1);
}

// The newest assigner comment carrying the marker, so a quoted marker in a
// human comment is ignored.
const isBot = (c) => isAssignerComment(c, ASSIGNER_LOGIN);

function findAssignment(comments) {
  for (let i = comments.length - 1; i >= 0; i--) {
    const parsed = isBot(comments[i]) && parseAssignment(comments[i].body);
    if (parsed) return { comment: comments[i], ...parsed };
  }
  return null;
}

// One sticky marker comment per PR, edited in place on reassign.
async function upsertAssignment(pr, { login, round, note }) {
  const hours = REASSIGN_AFTER_MS / 3600000;
  const body = [
    renderAssignment({ login, at: new Date().toISOString(), round }),
    `${note} If no agent review arrives within ${hours} working hours (10:00–19:00 IST, Mon–Fri), the next least-loaded member is requested.`,
  ].join("\n");
  const existing = findAssignment(pr.comments);
  const path = existing
    ? `/repos/${OWNER}/${REPO}/issues/comments/${existing.comment.id}`
    : `/repos/${OWNER}/${REPO}/issues/${pr.number}/comments`;
  await gh(path, {
    method: existing ? "PATCH" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ body }),
  });
}

function owningTeams(pr) {
  const teams = new Map(); // id → team
  for (const file of pr.files) {
    const t = ownerOf(file);
    if (t) teams.set(t.id, t);
  }
  return teams;
}

async function assign(pr) {
  log(`\n— PR #${pr.number} —`);
  const matchedTeams = owningTeams(pr);
  const team = await writers();
  const candidates = candidatesFor(
    [...matchedTeams.values()],
    config,
    pr.author,
  ).filter((c) => team.has(c.toLowerCase()));
  const reviewer = pickLeastLoaded(candidates, loads, { seed: pr.number });
  if (!reviewer) {
    log("  no reviewer to assign (the author is the only configured member)");
    return;
  }
  const teamNames = [...matchedTeams.values()]
    .map((t) => t.display_name)
    .join(", ");

  log(`  assigning: ${reviewer} (least loaded of ${candidates.join(", ")})`);
  if (dryRun) {
    log("  DRY_RUN: skipping API calls");
    return;
  }

  await requestReviewer(pr.number, reviewer);
  await upsertAssignment(pr, {
    login: reviewer,
    round: 1,
    note: teamNames
      ? `Review requested from \`${reviewer}\`, the least-loaded member of ${teamNames}.`
      : `No owning team matched; review requested from \`${reviewer}\`.`,
  });

  if (matchedTeams.size > 1) {
    const lines = [...matchedTeams.values()].map(
      (t) => `• **${t.display_name}** (${t.ownership}) — @${t.lead}`,
    );
    const body = `This PR touches code owned by multiple teams:\n\n${lines.join("\n")}\n\nPlease coordinate on review.`;
    await gh(`/repos/${OWNER}/${REPO}/issues/${pr.number}/comments`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ body }),
    });
    log(
      `  posted cross-team coordination comment (${matchedTeams.size} teams)`,
    );
  }
}

async function reassign(pr, current) {
  const team = await writers();
  const candidates = candidatesFor(
    [...owningTeams(pr).values()],
    config,
    pr.author,
  ).filter((c) => team.has(c.toLowerCase()));
  const next = pickLeastLoaded(candidates, loads, {
    exclude: [current.login],
    seed: pr.number + current.round,
  });
  log(
    `\n— PR #${pr.number} — ${current.login} has no agent review after ${REASSIGN_AFTER_MS / 3600000} working hours`,
  );
  if (!next) {
    log("  nobody else to request; leaving the current assignment");
    return;
  }
  log(`  reassigning: ${current.login} → ${next}`);
  if (dryRun) {
    log("  DRY_RUN: skipping API calls");
    return;
  }

  await gh(`/repos/${OWNER}/${REPO}/pulls/${pr.number}/requested_reviewers`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reviewers: [current.login] }),
  });
  loads.add(current.login, -1);
  await requestReviewer(pr.number, next);
  await upsertAssignment(pr, {
    login: next,
    round: current.round + 1,
    note: `Review moved from \`${current.login}\` to \`${next}\`: no agent review within ${REASSIGN_AFTER_MS / 3600000} working hours.`,
  });
}

// Internal means write access here: lower-case logins with push or higher,
// read once per sweep and only when a write is due. Only internal authors'
// pull requests rotate (external ones have their own flow), and only
// internal reviewers are requested (GitHub refuses anyone else).
let writersRead = null;
function writers() {
  writersRead ??= ghPaginated(
    `/repos/${OWNER}/${REPO}/collaborators?permission=push&per_page=100`,
  ).then((users) => new Set(users.map((u) => u.login.toLowerCase())));
  return writersRead;
}

async function isInternal(login) {
  return Boolean(login) && (await writers()).has(login.toLowerCase());
}

// Writes only when this PR needs them: no reviewer and never assigned → assign;
// the assigned reviewer is overdue → reassign. Anything else is read-only.
// A fork is always external: without write access a branch cannot live here.
async function sweepPr(pr, now) {
  if (pr.draft || pr.fork || now - Date.parse(pr.created_at) > NEW_PR_MS)
    return;
  const current = findAssignment(pr.comments);
  if (!hasHumanReviewRequest(pr)) {
    // A marker without a request means someone removed the reviewer by hand.
    if (!current && (await isInternal(pr.author))) await assign(pr);
    return;
  }
  if (!current) return; // assigned before this rotation existed, or by hand
  const requested = pr.requested_reviewers.map((r) => r.login.toLowerCase());
  if (!requested.includes(current.login.toLowerCase())) return; // reviewed, or changed by hand
  if (reassignDue(current, pr.reviews, now) && (await isInternal(pr.author)))
    await reassign(pr, current);
}

// A cancelled run (a newer dev push) finishes the PR it is writing, so a request
// is never left without its marker, then stops.
let stopping = false;
for (const sig of ["SIGINT", "SIGTERM"])
  process.on(sig, () => (stopping = true));

async function runSweep() {
  const open = await openPrs();
  for (const pr of open) {
    if (pr.draft) continue;
    for (const r of pr.requested_reviewers) loads.add(r.login, 1);
  }
  const prs = PR_NUMBER
    ? open.filter((p) => p.number === Number(PR_NUMBER))
    : open;
  log(`${open.length} open PRs; sweeping ${prs.length}`);
  const now = Date.now();
  for (const pr of prs) {
    if (stopping) {
      log("cancelled; stopping before the next PR");
      break;
    }
    try {
      await sweepPr(pr, now);
    } catch (e) {
      console.error(`PR #${pr.number}: ${e.message}`);
      process.exitCode = 1;
    }
  }
}

// ─── mode: ping ───────────────────────────────────────────────────────────────

async function postSlackDm(slackId, text) {
  if (dryRun) {
    log(`  DRY_RUN: would DM ${slackId}: ${text.slice(0, 80)}…`);
    return;
  }
  const res = await fetch("https://slack.com/api/chat.postMessage", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${SLACK_BOT_TOKEN}`,
      "Content-Type": "application/json; charset=utf-8",
    },
    body: JSON.stringify({ channel: slackId, text, unfurl_links: false }),
  });
  const json = await res.json();
  if (!json.ok)
    throw new Error(
      `Slack chat.postMessage failed for ${slackId}: ${json.error}`,
    );
}

function pingBodyFor(reviewerGh, pr, ageDays, authorGh) {
  // Lead the DM with an explicit <@slack_id> mention so Slack treats this as a
  // hard "you were mentioned" event — guarantees a push/badge even for users
  // with stricter bot-DM notification rules.
  const slackMentionOfRecipient = `<@${userMeta(reviewerGh).slack_id}>`;
  const prLink = `<${pr.html_url}|PR #${pr.number} — ${pr.title}>`;
  const lines = [
    `${slackMentionOfRecipient} reminder: ${prLink} has been waiting on your review for ${ageDays} day${ageDays === 1 ? "" : "s"}.`,
  ];
  const ledTeams = teamsLedBy(reviewerGh);
  if (ledTeams.length) {
    for (const t of ledTeams) {
      const candidates = t.members
        .filter((m) => m !== reviewerGh && m !== authorGh)
        .map((m) => userMeta(m)?.display_name || m);
      if (candidates.length) {
        lines.push(
          "",
          `You can reassign this to a ${t.display_name} teammate if you'd like: ${candidates.join(", ")}.`,
        );
      }
    }
  }
  return lines.join("\n");
}

async function runPing() {
  if (REVIEWER_PING_ENABLED !== "true") {
    log('REVIEWER_PING_ENABLED != "true" → ping disabled, exiting');
    return;
  }
  if (!SLACK_BOT_TOKEN) die("SLACK_BOT_TOKEN is required for ping mode");

  const open = await ghPaginated(
    `/repos/${OWNER}/${REPO}/pulls?state=open&per_page=100`,
  );
  const now = Date.now();
  const threshold = PING_AGE_DAYS * 24 * 3600 * 1000;

  for (const pr of open) {
    if (pr.draft) continue;
    const ageMs = now - new Date(pr.created_at).getTime();
    if (ageMs <= threshold) continue;
    const ageDays = Math.floor(ageMs / (24 * 3600 * 1000));
    const authorGh = pr.user?.login;
    const reviewers = pr.requested_reviewers || [];
    if (!reviewers.length) continue;

    for (const r of reviewers) {
      const gh = r.login;
      const meta = userMeta(gh);
      if (!meta?.slack_id) {
        log(
          `  PR #${pr.number}: reviewer @${gh} has no Slack ID in config — skipping`,
        );
        continue;
      }
      const body = pingBodyFor(gh, pr, ageDays, authorGh);
      try {
        await postSlackDm(meta.slack_id, body);
        log(`  PR #${pr.number}: DM'd @${gh} (${meta.slack_id})`);
      } catch (e) {
        console.error(`  PR #${pr.number}: failed to DM @${gh}: ${e.message}`);
      }
    }
  }
}

// ─── mode: summary ────────────────────────────────────────────────────────────

async function runSummary() {
  if (!SLACK_WEBHOOK_URL) die("SLACK_WEBHOOK_URL is required for summary mode");

  const open = await ghPaginated(
    `/repos/${OWNER}/${REPO}/pulls?state=open&per_page=100`,
  );
  const counts = new Map();
  for (const pr of open) {
    if (pr.draft) continue;
    for (const r of pr.requested_reviewers || []) {
      counts.set(r.login, (counts.get(r.login) || 0) + 1);
    }
  }

  if (counts.size === 0) {
    log("No pending reviewers — skipping summary post");
    return;
  }

  const sorted = [...counts.entries()].sort(
    (a, b) => b[1] - a[1] || a[0].localeCompare(b[0]),
  );
  const today = new Date().toISOString().slice(0, 10);
  const lines = [`*Open PR Review Load — ${today}*`];
  for (const [gh, n] of sorted) {
    lines.push(`• ${slackMention(gh)} — ${n} PR${n === 1 ? "" : "s"}`);
  }
  const text = lines.join("\n");

  if (dryRun) {
    log("DRY_RUN: would post summary:\n" + text);
    return;
  }

  const res = await fetch(SLACK_WEBHOOK_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!res.ok)
    throw new Error(`Slack webhook → ${res.status}: ${await res.text()}`);
  log(`Posted summary (${sorted.length} reviewers).`);
}

// ─── entrypoint ───────────────────────────────────────────────────────────────

const RUNNERS = {
  sweep: runSweep,
  ping: runPing,
  summary: runSummary,
};
const runner = RUNNERS[MODE];
if (!runner) die(`Unknown MODE: ${MODE}`);
runner().catch((e) => {
  console.error(e);
  process.exit(1);
});
