#!/usr/bin/env node
// Real-time issue/PR alerts to Slack so the team can triage immediately.
// Invoked by .github/workflows/issue-alerts.yml on issue + pull_request events.
//
// Three alert kinds, selected via the EVENT_KIND env var:
//   issue_opened   — a new issue was filed       → "review & triage"
//   issue_assigned — an open issue was picked up  → "assigned to @X"
//   pr_opened      — a PR was opened; if it links an issue, announce the pickup
//
// Reuses the same SLACK_WEBHOOK_URL incoming webhook as pr-digest.yml, so alerts
// land in the same channel. The GitHub-handle → Slack-mention map is read from
// .github/reviewer-config.json (best effort; falls back to the GitHub handle for
// anyone not listed, e.g. external issue reporters).

import { readFileSync } from 'node:fs';

const {
  GITHUB_TOKEN,
  SLACK_WEBHOOK_URL,
  GITHUB_REPOSITORY,
  GITHUB_EVENT_PATH,
  EVENT_KIND,
} = process.env;

for (const [k, v] of Object.entries({
  GITHUB_TOKEN,
  SLACK_WEBHOOK_URL,
  GITHUB_REPOSITORY,
  GITHUB_EVENT_PATH,
  EVENT_KIND,
})) {
  if (!v) {
    console.error(`Missing required env var: ${k}`);
    process.exit(1);
  }
}

const [OWNER, REPO] = GITHUB_REPOSITORY.split('/');
const GH_GRAPHQL = 'https://api.github.com/graphql';
// Issue/PR bodies can be long; keep the Slack preview readable.
const DESC_MAX = 280;

const event = JSON.parse(readFileSync(GITHUB_EVENT_PATH, 'utf8'));

const ghHeaders = {
  Accept: 'application/vnd.github+json',
  Authorization: `Bearer ${GITHUB_TOKEN}`,
  'X-GitHub-Api-Version': '2022-11-28',
  'User-Agent': `${OWNER}-${REPO}-issue-alerts`,
};

// --- Slack mention mapping (best effort) -----------------------------------
function loadSlackMap() {
  try {
    const cfg = JSON.parse(readFileSync('.github/reviewer-config.json', 'utf8'));
    const map = new Map();
    for (const [login, u] of Object.entries(cfg.users || {})) {
      if (u && u.slack_id) map.set(login.toLowerCase(), u.slack_id);
    }
    return map;
  } catch {
    // Map is a nicety, not a requirement — degrade to plain handles.
    return new Map();
  }
}
const SLACK_IDS = loadSlackMap();

function mention(login) {
  if (!login) return '_unassigned_';
  const id = SLACK_IDS.get(login.toLowerCase());
  return id ? `<@${id}>` : `\`${login}\``;
}

function escapeMrkdwn(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function truncate(s) {
  if (!s) return '';
  // Collapse newlines & HTML comments (issue/PR templates often include them).
  const cleaned = String(s)
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/[\r\n]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (cleaned.length <= DESC_MAX) return cleaned;
  return cleaned.slice(0, DESC_MAX - 1).trimEnd() + '…';
}

// --- Slack block helpers (mirrors pr-digest.mjs) ---------------------------
function header(text) {
  return { type: 'header', text: { type: 'plain_text', text, emoji: true } };
}
function section(text) {
  return { type: 'section', text: { type: 'mrkdwn', text } };
}
function context(text) {
  return { type: 'context', elements: [{ type: 'mrkdwn', text }] };
}

async function postSlack(blocks, fallbackText) {
  const res = await fetch(SLACK_WEBHOOK_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
    body: JSON.stringify({
      text: fallbackText,
      blocks,
      unfurl_links: false,
      unfurl_media: false,
    }),
  });
  if (!res.ok) {
    throw new Error(`Slack webhook failed: ${res.status} ${await res.text()}`);
  }
}

// --- GraphQL: issues a PR will close --------------------------------------
// closingIssuesReferences covers BOTH closing keywords in the body
// (Closes/Fixes/Resolves #N) and issues linked manually via the GitHub UI.
async function linkedIssues(prNumber) {
  const query = `
    query($owner:String!, $repo:String!, $number:Int!) {
      repository(owner:$owner, name:$repo) {
        pullRequest(number:$number) {
          closingIssuesReferences(first: 20) {
            nodes { number title url }
          }
        }
      }
    }`;
  const res = await fetch(GH_GRAPHQL, {
    method: 'POST',
    headers: { ...ghHeaders, 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, variables: { owner: OWNER, repo: REPO, number: prNumber } }),
  });
  if (!res.ok) throw new Error(`GitHub GraphQL → ${res.status} ${await res.text()}`);
  const json = await res.json();
  if (json.errors) throw new Error(`GitHub GraphQL errors: ${JSON.stringify(json.errors)}`);
  return json.data?.repository?.pullRequest?.closingIssuesReferences?.nodes || [];
}

// --- Handlers --------------------------------------------------------------
async function handleIssueOpened() {
  const i = event.issue;
  const labels = (i.labels || []).map((l) => l.name).filter(Boolean);
  const blocks = [
    header('🆕 New issue filed'),
    section(
      `*<${i.html_url}|${escapeMrkdwn(i.title)}>* \`#${i.number}\`\n👤 Opened by ${mention(i.user?.login)}`,
    ),
  ];
  if (labels.length) blocks.push(context(`🏷 ${labels.map(escapeMrkdwn).join(', ')}`));
  const snippet = truncate(i.body);
  if (snippet) blocks.push(section(`> ${escapeMrkdwn(snippet)}`));
  blocks.push(context('Please review and triage. 👀'));
  await postSlack(blocks, `New issue #${i.number}: ${i.title}`);
  console.log(`Posted new-issue alert for #${i.number}`);
}

async function handleIssueAssigned() {
  const i = event.issue;
  const assignee = event.assignee?.login; // the user just added
  const others = (i.assignees || []).map((a) => a.login).filter((l) => l && l !== assignee);
  let who = mention(assignee);
  if (others.length) who += ` (with ${others.map(mention).join(', ')})`;
  const blocks = [
    header('🙋 Issue picked up'),
    section(`*<${i.html_url}|${escapeMrkdwn(i.title)}>* \`#${i.number}\`\nAssigned to ${who}`),
  ];
  await postSlack(blocks, `Issue #${i.number} assigned to ${assignee || 'someone'}`);
  console.log(`Posted issue-assigned alert for #${i.number} → ${assignee}`);
}

async function handlePrOpened() {
  const pr = event.pull_request;
  const issues = await linkedIssues(pr.number);
  if (issues.length === 0) {
    console.log(`PR #${pr.number} links no issues; nothing to announce.`);
    return;
  }
  const list = issues
    .map((n) => `• <${n.url}|#${n.number} ${escapeMrkdwn(n.title)}>`)
    .join('\n');
  const verb = pr.draft ? 'opened a draft PR that will close' : 'opened a PR that closes';
  const blocks = [
    header('🔗 PR submitted for an open issue'),
    section(
      `*<${pr.html_url}|${escapeMrkdwn(pr.title)}>* \`#${pr.number}\`\n👤 ${mention(pr.user?.login)} ${verb}:\n${list}`,
    ),
    context(pr.draft ? 'Draft — work in progress.' : 'Ready for review. 👀'),
  ];
  const fallback = `PR #${pr.number} closes ${issues.map((n) => `#${n.number}`).join(', ')}`;
  await postSlack(blocks, fallback);
  console.log(
    `Posted PR-pickup alert for #${pr.number} → issues ${issues.map((n) => n.number).join(',')}`,
  );
}

async function main() {
  switch (EVENT_KIND) {
    case 'issue_opened':
      return handleIssueOpened();
    case 'issue_assigned':
      return handleIssueAssigned();
    case 'pr_opened':
      return handlePrOpened();
    default:
      console.error(`Unknown EVENT_KIND: ${EVENT_KIND}`);
      process.exit(1);
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
