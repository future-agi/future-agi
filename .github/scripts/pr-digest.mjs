#!/usr/bin/env node
// Posts a digest of open PRs (External vs Internal) to Slack.
// Invoked by .github/workflows/pr-digest.yml on a cron + workflow_dispatch.

const {
  GITHUB_TOKEN,
  SLACK_WEBHOOK_URL,
  GITHUB_REPOSITORY,
} = process.env;

for (const [k, v] of Object.entries({
  GITHUB_TOKEN,
  SLACK_WEBHOOK_URL,
  GITHUB_REPOSITORY,
})) {
  if (!v) {
    console.error(`Missing required env var: ${k}`);
    process.exit(1);
  }
}

const [OWNER, REPO] = GITHUB_REPOSITORY.split('/');
const GH_API = 'https://api.github.com';
const DESC_MAX = 140;
// Slack caps a single message at 50 blocks. We use 1 header + N PR sections
// per message; 35 keeps us well clear of the limit even with edge-case sections.
const PRS_PER_MESSAGE = 35;

const ghHeaders = {
  Accept: 'application/vnd.github+json',
  Authorization: `Bearer ${GITHUB_TOKEN}`,
  'X-GitHub-Api-Version': '2022-11-28',
  'User-Agent': `${OWNER}-${REPO}-pr-digest`,
};

async function gh(path) {
  const res = await fetch(`${GH_API}${path}`, { headers: ghHeaders });
  if (!res.ok) {
    throw new Error(`GitHub ${path} → ${res.status} ${await res.text()}`);
  }
  return res.json();
}

async function ghAll(path) {
  const out = [];
  let page = 1;
  while (true) {
    const sep = path.includes('?') ? '&' : '?';
    const batch = await gh(`${path}${sep}per_page=100&page=${page}`);
    out.push(...batch);
    if (batch.length < 100) break;
    page += 1;
  }
  return out;
}

function classifyArea(files) {
  let fe = false;
  let be = false;
  for (const f of files) {
    if (f.filename.startsWith('frontend/')) fe = true;
    else if (f.filename.startsWith('futureagi/')) be = true;
    if (fe && be) break;
  }
  if (fe && be) return { label: 'FE+BE', emoji: '🧩' };
  if (fe) return { label: 'FE', emoji: '🎨' };
  if (be) return { label: 'BE', emoji: '⚙️' };
  return { label: 'Other', emoji: '📦' };
}

function classifyReviewState(pr, reviews) {
  if (pr.draft) return { label: 'Draft', emoji: '📝' };
  // Use latest non-PENDING review per reviewer to determine state.
  const latestByUser = new Map();
  for (const r of reviews) {
    if (!r.user || r.state === 'PENDING') continue;
    const prev = latestByUser.get(r.user.login);
    if (!prev || new Date(r.submitted_at) > new Date(prev.submitted_at)) {
      latestByUser.set(r.user.login, r);
    }
  }
  const states = [...latestByUser.values()].map((r) => r.state);
  if (states.includes('CHANGES_REQUESTED')) return { label: 'Changes requested', emoji: '🟡' };
  if (states.includes('APPROVED')) return { label: 'Approved', emoji: '🟢' };
  if (states.includes('COMMENTED')) return { label: 'Commented', emoji: '💬' };
  return { label: 'Awaiting review', emoji: '⏳' };
}

function reviewerInfo(pr, reviews) {
  const requestedUsers = (pr.requested_reviewers || []).map((u) => u.login);
  const requestedTeams = (pr.requested_teams || []).map((t) => `@${t.slug}`);
  const reviewedUsers = [
    ...new Set(reviews.filter((r) => r.user).map((r) => r.user.login)),
  ];
  const all = [...new Set([...requestedUsers, ...requestedTeams, ...reviewedUsers])];
  return { attached: all.length > 0, names: all };
}

function bucket(pr) {
  return pr.author_association === 'MEMBER' || pr.author_association === 'OWNER'
    ? 'internal'
    : 'external';
}

function truncate(s) {
  if (!s) return '';
  // Collapse newlines & HTML comments (PR templates often include them).
  const cleaned = s
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/[\r\n]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (cleaned.length <= DESC_MAX) return cleaned;
  return cleaned.slice(0, DESC_MAX - 1).trimEnd() + '…';
}

function escapeMrkdwn(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function prSection(pr) {
  const lines = [];
  lines.push(`*<${pr.html_url}|${escapeMrkdwn(pr.title)}>* \`#${pr.number}\``);
  lines.push(
    `👤 ${pr.user.login}  •  ${pr.area.emoji} ${pr.area.label}  •  ${pr.review.emoji} ${pr.review.label}`,
  );
  if (pr.reviewers.attached) {
    lines.push(`👀 Reviewers: ${pr.reviewers.names.join(', ')}`);
  } else {
    lines.push('🚨 *No reviewer assigned*');
  }
  if (pr.tags.length) {
    lines.push(`🏷 ${pr.tags.join(', ')}`);
  }
  if (pr.snippet) {
    lines.push(`> ${escapeMrkdwn(pr.snippet)}`);
  }
  return {
    type: 'section',
    text: { type: 'mrkdwn', text: lines.join('\n') },
  };
}

function header(text) {
  return { type: 'header', text: { type: 'plain_text', text, emoji: true } };
}

function context(text) {
  return {
    type: 'context',
    elements: [{ type: 'mrkdwn', text }],
  };
}

function emptyStub() {
  return {
    type: 'section',
    text: { type: 'mrkdwn', text: '_No open PRs in this bucket._' },
  };
}

async function enrichPR(pr) {
  const [files, reviews] = await Promise.all([
    ghAll(`/repos/${OWNER}/${REPO}/pulls/${pr.number}/files`),
    ghAll(`/repos/${OWNER}/${REPO}/pulls/${pr.number}/reviews`),
  ]);
  return {
    number: pr.number,
    title: pr.title,
    html_url: pr.html_url,
    user: { login: pr.user?.login || 'unknown' },
    bucket: bucket(pr),
    area: classifyArea(files),
    review: classifyReviewState(pr, reviews),
    reviewers: reviewerInfo(pr, reviews),
    tags: (pr.labels || []).map((l) => l.name),
    snippet: truncate(pr.body),
    updated_at: pr.updated_at,
  };
}

function istDateString(now = new Date()) {
  return now.toLocaleDateString('en-IN', {
    timeZone: 'Asia/Kolkata',
    weekday: 'short',
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  });
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
    const body = await res.text();
    throw new Error(
      `Slack webhook failed: ${res.status} ${body} (block count: ${blocks.length})`,
    );
  }
}

async function postBucket(bucketEmoji, bucketLabel, prs) {
  if (prs.length === 0) {
    await postSlack(
      [header(`${bucketEmoji} ${bucketLabel} (0)`), emptyStub()],
      `${bucketLabel}: none`,
    );
    return 1;
  }
  const totalParts = Math.ceil(prs.length / PRS_PER_MESSAGE);
  for (let i = 0; i < totalParts; i++) {
    const chunk = prs.slice(i * PRS_PER_MESSAGE, (i + 1) * PRS_PER_MESSAGE);
    const partSuffix = totalParts > 1 ? ` — part ${i + 1}/${totalParts}` : '';
    const blocks = [
      header(`${bucketEmoji} ${bucketLabel} (${prs.length})${partSuffix}`),
      ...chunk.map(prSection),
    ];
    await postSlack(blocks, `${bucketLabel}${partSuffix}`);
  }
  return totalParts;
}

async function main() {
  const openPRs = await ghAll(`/repos/${OWNER}/${REPO}/pulls?state=open`);
  const enriched = await Promise.all(openPRs.map(enrichPR));
  enriched.sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));

  const external = enriched.filter((p) => p.bucket === 'external');
  const internal = enriched.filter((p) => p.bucket === 'internal');

  // Message 1: digest summary. Subsequent messages: each bucket, chunked if needed.
  const summaryBlocks = [
    header(`🚀 PR Digest — ${istDateString()}`),
    context(
      `${enriched.length} open  •  ${external.length} External  •  ${internal.length} Internal`,
    ),
  ];
  const fallback = `PR Digest — ${enriched.length} open (${external.length} external, ${internal.length} internal)`;
  await postSlack(summaryBlocks, fallback);

  const externalParts = await postBucket('📤', 'External PRs', external);
  const internalParts = await postBucket('🏢', 'Internal PRs', internal);

  const totalMessages = 1 + externalParts + internalParts;
  console.log(`Posted digest in ${totalMessages} message(s): ${fallback}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
