#!/usr/bin/env node

// Groups open issues and pull requests by explicit issue references. The workflow
// using this script must never execute code from a forked pull request.

const {
  GITHUB_TOKEN,
  GITHUB_REPOSITORY,
  EVENT_NAME = "workflow_dispatch",
  EVENT_NUMBER = "",
  DRY_RUN = "false",
  SLACK_WEBHOOK_URL,
  DISCORD_WEBHOOK_URL,
} = process.env;

const TRIAGE_LABEL = "needs-maintainer-triage";
const TRIAGE_MARKER = "<!-- pr-triage:";
const REFERENCE_RE = /\b(?:closes|fixes|resolves)\s+#(\d+)\b/gi;

const [OWNER, REPO] = (GITHUB_REPOSITORY || "").split("/");
const dryRun = DRY_RUN === "true";

const headers = {
  Accept: "application/vnd.github+json",
  Authorization: `Bearer ${GITHUB_TOKEN}`,
  "X-GitHub-Api-Version": "2022-11-28",
  "User-Agent": `${OWNER || "future-agi"}-${REPO || "triage"}-pr-triage`,
};

function requireEnvironment() {
  for (const [name, value] of Object.entries({ GITHUB_TOKEN, GITHUB_REPOSITORY })) {
    if (!value) throw new Error(`Missing required environment variable: ${name}`);
  }
}

async function github(path, init = {}) {
  const response = await fetch(`https://api.github.com${path}`, {
    ...init,
    headers: { ...headers, ...(init.headers || {}) },
  });
  if (!response.ok) {
    const error = new Error(`GitHub ${init.method || "GET"} ${path} → ${response.status}`);
    error.status = response.status;
    error.detail = await response.text();
    throw error;
  }
  if (response.status === 204) return null;
  return response.json();
}

async function githubAll(path) {
  const results = [];
  for (let page = 1; ; page += 1) {
    const separator = path.includes("?") ? "&" : "?";
    const batch = await github(`${path}${separator}per_page=100&page=${page}`);
    results.push(...batch);
    if (batch.length < 100) return results;
  }
}

export function extractIssueRefs(text = "") {
  return [...new Set([...text.matchAll(REFERENCE_RE)].map((match) => Number(match[1])))];
}

export function extractClaimants(comments = []) {
  const claimPattern = /\b(?:i(?:'m| am|['’]ll)|we(?:'re| are)|working on|taking|claiming|can take)\b/i;
  const subjectPattern = /\b(?:this|it|issue|bug|pr)\b/i;
  const claimants = new Map();

  for (const comment of comments) {
    const body = comment.body || "";
    if (!claimPattern.test(body) || !subjectPattern.test(body)) continue;
    const login = comment.user?.login;
    if (login) claimants.set(login, comment.html_url || "");
  }

  return [...claimants.entries()].map(([login, url]) => ({ login, url }));
}

export function buildIssueGroups(issues, pullRequests) {
  const issuesByNumber = new Map(issues.map((issue) => [issue.number, issue]));
  const groups = new Map();

  for (const pullRequest of pullRequests) {
    const references = extractIssueRefs(
      `${pullRequest.title || ""}\n${pullRequest.body || ""}`,
    );
    for (const issueNumber of references) {
      const issue = issuesByNumber.get(issueNumber);
      if (!issue) continue;
      if (!groups.has(issueNumber)) groups.set(issueNumber, { issue, pullRequests: [] });
      groups.get(issueNumber).pullRequests.push({
        number: pullRequest.number,
        title: pullRequest.title,
        url: pullRequest.html_url,
        author: pullRequest.user?.login || "unknown",
        draft: Boolean(pullRequest.draft),
        updatedAt: pullRequest.updated_at,
        reviewers: [
          ...(pullRequest.requested_reviewers || []).map((reviewer) => reviewer.login),
          ...(pullRequest.requested_teams || []).map((team) => `@${team.slug}`),
        ],
      });
    }
  }

  return [...groups.values()]
    .filter((group) => group.pullRequests.length > 1)
    .sort((a, b) => a.issue.number - b.issue.number);
}

export function renderTriageComment(group) {
  const lines = [
    `${TRIAGE_MARKER} issue-${group.issue.number} -->`,
    "### Related open pull requests",
    "",
    ...group.pullRequests.map((pullRequest) => {
      const draft = pullRequest.draft ? " (draft)" : "";
      return `- [#${pullRequest.number} — ${pullRequest.title}](${pullRequest.url}) — @${pullRequest.author}${draft}`;
    }),
    "",
    "This is an automated grouping based on explicit issue references. A maintainer should choose the canonical PR and close duplicate work if appropriate.",
  ];
  return lines.join("\n");
}

function renderDigest(groups, claims = []) {
  if (!groups.length && !claims.length) {
    return "PR triage: no related open pull requests or advisory work claims.";
  }

  const lines = ["PR triage digest", ""];
  for (const group of groups) {
    lines.push(`Issue #${group.issue.number}: ${group.issue.title}`);
    for (const pullRequest of group.pullRequests) {
      lines.push(`- PR #${pullRequest.number}: ${pullRequest.title} (${pullRequest.url})`);
    }
    lines.push("Maintainer action: choose the canonical PR or close duplicate work.", "");
  }

  if (claims.length) {
    lines.push("Advisory contributor claims", "");
    for (const claim of claims) {
      lines.push(`Issue #${claim.issue.number}: ${claim.issue.title} (${claim.issue.url})`);
      for (const claimant of claim.claimants) {
        lines.push(`- @${claimant.login}${claimant.url ? ` (${claimant.url})` : ""}`);
      }
    }
  }

  return lines.join("\n").trim();
}

async function listOpenIssues() {
  const issues = await githubAll(`/repos/${OWNER}/${REPO}/issues?state=open&sort=updated&direction=desc`);
  return issues
    .filter((issue) => !issue.pull_request)
    .map((issue) => ({
      number: issue.number,
      title: issue.title,
      url: issue.html_url,
      labels: (issue.labels || []).map((label) => label.name),
      assignees: (issue.assignees || []).map((assignee) => assignee.login),
      updatedAt: issue.updated_at,
    }));
}

async function listOpenPullRequests() {
  const pullRequests = await githubAll(
    `/repos/${OWNER}/${REPO}/pulls?state=open&sort=updated&direction=desc`,
  );
  return pullRequests.map((pullRequest) => ({
    number: pullRequest.number,
    title: pullRequest.title,
    body: pullRequest.body || "",
    html_url: pullRequest.html_url,
    user: pullRequest.user,
    draft: pullRequest.draft,
    updated_at: pullRequest.updated_at,
    requested_reviewers: pullRequest.requested_reviewers || [],
    requested_teams: pullRequest.requested_teams || [],
  }));
}

async function listComments(issueNumber) {
  return githubAll(`/repos/${OWNER}/${REPO}/issues/${issueNumber}/comments`);
}

async function collectClaimants(issues, groups) {
  const groupedIssueNumbers = new Set(groups.map((group) => group.issue.number));
  const candidates = issues
    .filter((issue) => {
      const labels = new Set(issue.labels);
      return !groupedIssueNumbers.has(issue.number)
        && (labels.has("good first issue") || labels.has("accepted"));
    })
    .slice(0, 25);

  const claims = [];
  for (const issue of candidates) {
    const claimants = extractClaimants(await listComments(issue.number));
    if (claimants.length) claims.push({ issue, claimants });
  }
  return claims;
}

async function ensureTriageLabel() {
  try {
    await github(`/repos/${OWNER}/${REPO}/labels/${encodeURIComponent(TRIAGE_LABEL)}`);
  } catch (error) {
    if (error.status !== 404) throw error;
    await github(`/repos/${OWNER}/${REPO}/labels`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: TRIAGE_LABEL,
        color: "E4E669",
        description: "Maintainer decision needed for related issues or pull requests",
      }),
    });
  }
}

async function addLabel(issueOrPullRequestNumber) {
  await github(`/repos/${OWNER}/${REPO}/issues/${issueOrPullRequestNumber}/labels`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ labels: [TRIAGE_LABEL] }),
  });
}

async function upsertIssueComment(group) {
  const comments = await listComments(group.issue.number);
  const body = renderTriageComment(group);
  const existing = comments.find((comment) => comment.body?.startsWith(
    `${TRIAGE_MARKER} issue-${group.issue.number}`,
  ));

  if (existing) {
    await github(`/repos/${OWNER}/${REPO}/issues/comments/${existing.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ body }),
    });
  } else {
    await github(`/repos/${OWNER}/${REPO}/issues/${group.issue.number}/comments`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ body }),
    });
  }
}

async function postWebhook(url, payload) {
  if (!url) return;
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(`Webhook returned ${response.status}`);
}

async function notifyMaintainers(groups, claims) {
  const digest = renderDigest(groups, claims);
  await postWebhook(SLACK_WEBHOOK_URL, { text: digest });
  await postWebhook(DISCORD_WEBHOOK_URL, { content: digest.slice(0, 1900) });
}

async function applyGroupActions(group, labelsAvailable) {
  await upsertIssueComment(group);
  if (!labelsAvailable) return;

  for (const number of [
    group.issue.number,
    ...group.pullRequests.map((pullRequest) => pullRequest.number),
  ]) {
    try {
      await addLabel(number);
    } catch (error) {
      console.warn(`Could not apply ${TRIAGE_LABEL} to #${number}: ${error.message}`);
    }
  }
}

function isSweepEvent() {
  return EVENT_NAME === "schedule" || EVENT_NAME === "workflow_dispatch";
}

function selectedGroups(groups) {
  const eventNumber = Number(EVENT_NUMBER);
  if (!eventNumber || isSweepEvent()) return groups;
  return groups.filter(
    (group) => group.issue.number === eventNumber
      || group.pullRequests.some((pullRequest) => pullRequest.number === eventNumber),
  );
}

async function main() {
  requireEnvironment();
  const [issues, pullRequests] = await Promise.all([listOpenIssues(), listOpenPullRequests()]);
  const groups = buildIssueGroups(issues, pullRequests);
  const claims = await collectClaimants(issues, groups);
  const targets = selectedGroups(groups);

  if (dryRun) {
    console.log(JSON.stringify({
      dryRun: true,
      groups,
      claims,
      digest: renderDigest(groups, claims),
    }, null, 2));
    return;
  }

  let labelsAvailable = true;
  if (targets.length) {
    try {
      await ensureTriageLabel();
    } catch (error) {
      labelsAvailable = false;
      console.warn(`Could not create or find ${TRIAGE_LABEL}: ${error.message}`);
    }
    for (const group of targets) await applyGroupActions(group, labelsAvailable);
  }

  if (isSweepEvent()) await notifyMaintainers(groups, claims);

  console.log(JSON.stringify({
    event: EVENT_NAME,
    groups: groups.length,
    claims: claims.length,
    actedOn: targets.length,
  }));
}

const isMain = process.argv[1]
  && process.argv[1].endsWith(".github/scripts/pr-triage.mjs");
if (isMain) main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
