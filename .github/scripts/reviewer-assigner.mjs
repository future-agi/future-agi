#!/usr/bin/env node
// Reviewer Assigner: routes PRs to team leads, DM-pings stale reviewers,
// and posts a daily reviewer-load summary. Driven by .github/reviewer-config.json.
//
// Modes (selected via MODE env var):
//   assign   — request the right team lead(s) on PRs that have no reviewer
//   ping     — DM each pending reviewer of an open PR >3 days old (gated by REVIEWER_PING_ENABLED)
//   summary  — post sorted "reviewer → open PR count" to SLACK_WEBHOOK_URL

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const CONFIG_PATH = resolve(__dirname, '..', 'reviewer-config.json');

const {
  MODE,
  GITHUB_TOKEN,
  GITHUB_REPOSITORY,
  PR_NUMBER,
  SLACK_BOT_TOKEN,
  SLACK_WEBHOOK_URL,
  REVIEWER_PING_ENABLED,
  DRY_RUN,
} = process.env;

if (!MODE) die('MODE env var is required (assign|ping|summary)');
if (!GITHUB_TOKEN) die('GITHUB_TOKEN is required');
if (!GITHUB_REPOSITORY) die('GITHUB_REPOSITORY is required');

const [OWNER, REPO] = GITHUB_REPOSITORY.split('/');
const GH_API = 'https://api.github.com';
const PING_AGE_DAYS = 3;
const dryRun = DRY_RUN === 'true';

const config = JSON.parse(readFileSync(CONFIG_PATH, 'utf8'));

const ghHeaders = {
  Accept: 'application/vnd.github+json',
  Authorization: `Bearer ${GITHUB_TOKEN}`,
  'X-GitHub-Api-Version': '2022-11-28',
  'User-Agent': `${OWNER}-${REPO}-reviewer-assigner`,
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
  const res = await fetch(`${GH_API}${path}`, { ...init, headers: { ...ghHeaders, ...(init.headers || {}) } });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`GitHub ${init.method || 'GET'} ${path} → ${res.status}: ${body}`);
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
    if (!res.ok) throw new Error(`GitHub GET ${url} → ${res.status}: ${await res.text()}`);
    out.push(...(await res.json()));
    const link = res.headers.get('link');
    const next = link?.split(',').map((s) => s.trim()).find((s) => s.endsWith('rel="next"'));
    url = next ? next.slice(1, next.indexOf('>')) : null;
  }
  return out;
}

// Convert a labeler-style glob (** and *) to a RegExp. Anchored at both ends.
function globToRegex(glob) {
  let re = '';
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === '*' && glob[i + 1] === '*') {
      re += '.*';
      i++;
    } else if (c === '*') {
      re += '[^/]*';
    } else if (/[.+?^${}()|[\]\\]/.test(c)) {
      re += '\\' + c;
    } else {
      re += c;
    }
  }
  return new RegExp('^' + re + '$');
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
  compiledPaths: t.paths.map((p) => ({ glob: p, re: globToRegex(p), specificity: globSpecificity(p) })),
}));

// Find the team that owns a given file path (most-specific-glob-wins).
function ownerOf(file) {
  let best = null;
  for (const team of compiledTeams) {
    for (const p of team.compiledPaths) {
      if (p.re.test(file)) {
        if (!best || p.specificity > best.specificity) best = { team, specificity: p.specificity };
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

// ─── mode: assign ─────────────────────────────────────────────────────────────

async function assignForPr(prNumber) {
  log(`\n— PR #${prNumber} —`);
  const pr = await gh(`/repos/${OWNER}/${REPO}/pulls/${prNumber}`);

  if (pr.state !== 'open') {
    log(`  state=${pr.state}, skipping`);
    return;
  }
  if ((pr.requested_reviewers?.length ?? 0) > 0 || (pr.requested_teams?.length ?? 0) > 0) {
    log(`  already has reviewers (${pr.requested_reviewers.map((r) => r.login).join(',')} + teams=${pr.requested_teams.length}), skipping`);
    return;
  }

  const files = await ghPaginated(`/repos/${OWNER}/${REPO}/pulls/${prNumber}/files?per_page=100`);
  const matchedTeams = new Map(); // id → team
  for (const f of files) {
    const t = ownerOf(f.filename);
    if (t) matchedTeams.set(t.id, t);
  }

  let leads = [...new Set([...matchedTeams.values()].map((t) => t.lead))];
  const author = pr.user?.login;
  leads = leads.filter((l) => l !== author);

  if (leads.length === 0) {
    if (config.fallback_lead && config.fallback_lead !== author) {
      leads = [config.fallback_lead];
      log(`  no team matched (or author was the only lead) → fallback ${config.fallback_lead}`);
    } else {
      log('  no reviewers to assign (no match and author == fallback)');
      return;
    }
  }

  log(`  assigning: ${leads.join(', ')}`);
  if (dryRun) {
    log('  DRY_RUN: skipping API calls');
    return;
  }

  await gh(`/repos/${OWNER}/${REPO}/pulls/${prNumber}/requested_reviewers`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reviewers: leads }),
  });

  if (matchedTeams.size > 1) {
    const lines = [...matchedTeams.values()].map(
      (t) => `• **${t.display_name}** (${t.ownership}) — @${t.lead}`,
    );
    const body = `This PR touches code owned by multiple teams:\n\n${lines.join('\n')}\n\nPlease coordinate on review.`;
    await gh(`/repos/${OWNER}/${REPO}/issues/${prNumber}/comments`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ body }),
    });
    log(`  posted cross-team coordination comment (${matchedTeams.size} teams)`);
  }
}

async function runAssign() {
  let prs;
  if (PR_NUMBER) {
    prs = [Number(PR_NUMBER)];
  } else {
    log('No PR_NUMBER → sweeping all open PRs');
    const open = await ghPaginated(`/repos/${OWNER}/${REPO}/pulls?state=open&per_page=100`);
    prs = open.map((p) => p.number);
  }
  for (const n of prs) {
    try {
      await assignForPr(n);
    } catch (e) {
      console.error(`PR #${n}: ${e.message}`);
    }
  }
}

// ─── mode: ping ───────────────────────────────────────────────────────────────

async function postSlackDm(slackId, text) {
  if (dryRun) {
    log(`  DRY_RUN: would DM ${slackId}: ${text.slice(0, 80)}…`);
    return;
  }
  const res = await fetch('https://slack.com/api/chat.postMessage', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${SLACK_BOT_TOKEN}`,
      'Content-Type': 'application/json; charset=utf-8',
    },
    body: JSON.stringify({ channel: slackId, text, unfurl_links: false }),
  });
  const json = await res.json();
  if (!json.ok) throw new Error(`Slack chat.postMessage failed for ${slackId}: ${json.error}`);
}

function pingBodyFor(reviewerGh, pr, ageDays, authorGh) {
  // Lead the DM with an explicit <@slack_id> mention so Slack treats this as a
  // hard "you were mentioned" event — guarantees a push/badge even for users
  // with stricter bot-DM notification rules.
  const slackMentionOfRecipient = `<@${userMeta(reviewerGh).slack_id}>`;
  const prLink = `<${pr.html_url}|PR #${pr.number} — ${pr.title}>`;
  const lines = [`${slackMentionOfRecipient} reminder: ${prLink} has been waiting on your review for ${ageDays} day${ageDays === 1 ? '' : 's'}.`];
  const ledTeams = teamsLedBy(reviewerGh);
  if (ledTeams.length) {
    for (const t of ledTeams) {
      const candidates = t.members
        .filter((m) => m !== reviewerGh && m !== authorGh)
        .map((m) => userMeta(m)?.display_name || m);
      if (candidates.length) {
        lines.push('', `You can reassign this to a ${t.display_name} teammate if you'd like: ${candidates.join(', ')}.`);
      }
    }
  }
  return lines.join('\n');
}

async function runPing() {
  if (REVIEWER_PING_ENABLED !== 'true') {
    log('REVIEWER_PING_ENABLED != "true" → ping disabled, exiting');
    return;
  }
  if (!SLACK_BOT_TOKEN) die('SLACK_BOT_TOKEN is required for ping mode');

  const open = await ghPaginated(`/repos/${OWNER}/${REPO}/pulls?state=open&per_page=100`);
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
        log(`  PR #${pr.number}: reviewer @${gh} has no Slack ID in config — skipping`);
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
  if (!SLACK_WEBHOOK_URL) die('SLACK_WEBHOOK_URL is required for summary mode');

  const open = await ghPaginated(`/repos/${OWNER}/${REPO}/pulls?state=open&per_page=100`);
  const counts = new Map();
  for (const pr of open) {
    if (pr.draft) continue;
    for (const r of pr.requested_reviewers || []) {
      counts.set(r.login, (counts.get(r.login) || 0) + 1);
    }
  }

  if (counts.size === 0) {
    log('No pending reviewers — skipping summary post');
    return;
  }

  const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const today = new Date().toISOString().slice(0, 10);
  const lines = [`*Open PR Review Load — ${today}*`];
  for (const [gh, n] of sorted) {
    lines.push(`• ${slackMention(gh)} — ${n} PR${n === 1 ? '' : 's'}`);
  }
  const text = lines.join('\n');

  if (dryRun) {
    log('DRY_RUN: would post summary:\n' + text);
    return;
  }

  const res = await fetch(SLACK_WEBHOOK_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) throw new Error(`Slack webhook → ${res.status}: ${await res.text()}`);
  log(`Posted summary (${sorted.length} reviewers).`);
}

// ─── entrypoint ───────────────────────────────────────────────────────────────

const RUNNERS = { assign: runAssign, ping: runPing, summary: runSummary };
const runner = RUNNERS[MODE];
if (!runner) die(`Unknown MODE: ${MODE}`);
runner().catch((e) => {
  console.error(e);
  process.exit(1);
});
