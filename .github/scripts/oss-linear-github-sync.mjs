#!/usr/bin/env node
// OSS Linear ↔ GitHub Sync
// Keeps OSS Issues project tickets and their GitHub PR reviewers in sync.
//
// Rules (OSS only — never touches internal tickets):
//   • GitHub PR MERGED  → Linear status: Done
//   • GitHub PR CLOSED  → Linear status: Cancelled
//   • GitHub PR OPEN, Linear assignee ≠ GitHub reviewer → patch GitHub reviewer

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const CONFIG_PATH = resolve(__dirname, '..', 'reviewer-config.json')

const {
  LINEAR_API_KEY,
  GITHUB_TOKEN,
  GITHUB_REPOSITORY,
  DRY_RUN,
} = process.env

if (!LINEAR_API_KEY) die('LINEAR_API_KEY is required')
if (!GITHUB_TOKEN)   die('GITHUB_TOKEN is required')

const DRY = DRY_RUN === 'true'
if (DRY) log('DRY RUN — no writes will happen')

// ── Config ────────────────────────────────────────────────────────────────────

const OSS_PROJECT_ID = '3d417a18-9ef6-4519-ba85-9aba721da1e0'

// GitHub handle → FutureAGI email (kept in sync with reviewer-config.json users)
const GITHUB_TO_EMAIL = {
  'abhijaisrivastava15': 'abhijai@futureagi.com',
  'commitPirate':        'nosang.s@futureagi.com',
  'cdileep23':           'dileep.kumar@futureagi.com',
  'KarthikAvinashFI':    'karthik.avinash@futureagi.com',
  'hadarishav':          'rishav@futureagi.com',
  'JayaSurya-27':        'jayasurya@futureagi.com',
  'sarthakFuture':       'sarthak@futureagi.com',
  'khushalsonawat':      'khushal.sonawat@futureagi.com',
  'atharva-bhange':      'atharva@futureagi.com',
  'azain-commits':       'azain@futureagi.com',
  'NVJKKartik':          'kartik.nvj@futureagi.com',
  'Tanmaycode1':         'tanmay@futureagi.com',
  'definitelynotchirag': 'chintan@futureagi.com',
}
const EMAIL_TO_GITHUB = Object.fromEntries(
  Object.entries(GITHUB_TO_EMAIL).map(([h, e]) => [e, h])
)

// ── Linear API ────────────────────────────────────────────────────────────────

async function linearQuery(query, variables = {}) {
  const res = await fetch('https://api.linear.app/graphql', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: LINEAR_API_KEY,
    },
    body: JSON.stringify({ query, variables }),
  })
  const json = await res.json()
  if (json.errors) throw new Error(`Linear API error: ${JSON.stringify(json.errors)}`)
  return json.data
}

async function fetchOssTickets() {
  const GQL = `
    query($projectId: String!, $after: String) {
      project(id: $projectId) {
        issues(
          first: 250
          after: $after
          filter: { state: { type: { nin: ["completed", "canceled"] } } }
        ) {
          nodes {
            id
            identifier
            title
            state { id name type }
            assignee { email }
            attachments { nodes { url } }
          }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
  `
  const tickets = []
  let cursor = null
  do {
    const data = await linearQuery(GQL, { projectId: OSS_PROJECT_ID, after: cursor })
    const page = data?.project?.issues
    if (!page) break
    for (const issue of page.nodes) {
      const prLinks = (issue.attachments?.nodes ?? [])
        .map(a => parsePrUrl(a.url))
        .filter(Boolean)
      if (prLinks.length > 0) {
        tickets.push({
          id:            issue.id,
          identifier:    issue.identifier,
          title:         issue.title,
          stateId:       issue.state.id,
          stateName:     issue.state.name,
          statusType:    issue.state.type,
          assigneeEmail: issue.assignee?.email ?? null,
          prLinks,
        })
      }
    }
    cursor = page.pageInfo.hasNextPage ? page.pageInfo.endCursor : null
  } while (cursor)
  return tickets
}

async function getTeamStateId(issueId, stateName) {
  const data = await linearQuery(`
    query($id: String!) {
      issue(id: $id) { team { states { nodes { id name } } } }
    }
  `, { id: issueId })
  return data?.issue?.team?.states?.nodes?.find(s => s.name === stateName)?.id ?? null
}

async function updateLinearStatus(issueId, stateName) {
  const stateId = await getTeamStateId(issueId, stateName)
  if (!stateId) { log(`  ⚠  state "${stateName}" not found for ${issueId}`); return false }
  if (DRY) { log(`  [dry] would set ${issueId} → ${stateName}`); return true }
  const data = await linearQuery(`
    mutation($id: String!, $stateId: String!) {
      issueUpdate(id: $id, input: { stateId: $stateId }) { success }
    }
  `, { id: issueId, stateId })
  return data?.issueUpdate?.success === true
}

// ── GitHub API ────────────────────────────────────────────────────────────────

async function gh(path, method = 'GET', body = null) {
  const res = await fetch(`https://api.github.com${path}`, {
    method,
    headers: {
      Authorization:        `token ${GITHUB_TOKEN}`,
      Accept:               'application/vnd.github.v3+json',
      'User-Agent':         'oss-linear-github-sync',
      ...(body ? { 'Content-Type': 'application/json' } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  })
  if (res.status === 204) return null
  return res.json()
}

async function getPr(owner, repo, number) {
  const pr = await gh(`/repos/${owner}/${repo}/pulls/${number}`)
  if (pr?.message === 'Not Found') return null
  return pr
}

async function getReviewers(owner, repo, number) {
  const data = await gh(`/repos/${owner}/${repo}/pulls/${number}/requested_reviewers`)
  return (data?.users ?? []).map(u => u.login)
}

async function addReviewer(owner, repo, number, handle) {
  if (DRY) { log(`  [dry] would add reviewer ${handle} to ${owner}/${repo}#${number}`); return }
  await gh(`/repos/${owner}/${repo}/pulls/${number}/requested_reviewers`, 'POST', { reviewers: [handle] })
}

async function removeReviewers(owner, repo, number, handles) {
  if (!handles.length) return
  if (DRY) { log(`  [dry] would remove reviewers ${handles.join(', ')} from ${owner}/${repo}#${number}`); return }
  await gh(`/repos/${owner}/${repo}/pulls/${number}/requested_reviewers`, 'DELETE', { reviewers: handles })
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function parsePrUrl(url) {
  const m = url?.match(/github\.com\/([^/]+)\/([^/]+)\/pull\/(\d+)/)
  if (!m) return null
  return { owner: m[1], repo: m[2], number: parseInt(m[3], 10) }
}

function log(...args) { console.log(...args) }
function die(msg) { console.error(`[oss-sync] ERROR: ${msg}`); process.exit(1) }

// ── Main ──────────────────────────────────────────────────────────────────────

async function main() {
  log(`[oss-sync] starting${DRY ? ' (dry run)' : ''}`)

  const tickets = await fetchOssTickets()
  log(`[oss-sync] ${tickets.length} OSS tickets with GitHub PR links`)

  let linearUpdates = 0
  let githubUpdates = 0

  // Process all ticket×PR pairs concurrently (capped at 10 in-flight)
  const pairs = tickets.flatMap(t => t.prLinks.map(pr => ({ ticket: t, pr })))
  const CONCURRENCY = 10

  async function processPair({ ticket, pr }) {
    const ghPr = await getPr(pr.owner, pr.repo, pr.number)
    if (!ghPr) {
      log(`  ⚠  ${ticket.identifier}: PR #${pr.number} not found in ${pr.repo}`)
      return { linear: 0, github: 0 }
    }

    const merged = !!ghPr.merged_at
    const closed = ghPr.state === 'closed'

    // ── Status sync ──────────────────────────────────────────────────────────
    if (merged && ticket.statusType !== 'completed') {
      log(`  ✅ ${ticket.identifier}: merged → Done`)
      return { linear: (await updateLinearStatus(ticket.id, 'Done')) ? 1 : 0, github: 0 }

    } else if (closed && !merged && ticket.statusType !== 'canceled') {
      log(`  🚫 ${ticket.identifier}: closed (no merge) → Canceled`)
      return { linear: (await updateLinearStatus(ticket.id, 'Canceled')) ? 1 : 0, github: 0 }

    } else if (!closed) {
      // ── Reviewer sync (open PRs only) ──────────────────────────────────────
      if (!ticket.assigneeEmail) return { linear: 0, github: 0 }
      const expectedHandle = EMAIL_TO_GITHUB[ticket.assigneeEmail]
      if (!expectedHandle) return { linear: 0, github: 0 }

      const currentReviewers = await getReviewers(pr.owner, pr.repo, pr.number)
      if (currentReviewers.includes(expectedHandle)) return { linear: 0, github: 0 }

      const toRemove = currentReviewers.filter(r => GITHUB_TO_EMAIL[r] && r !== expectedHandle)
      log(`  👤 ${ticket.identifier}: PR #${pr.number} reviewer → ${expectedHandle}${toRemove.length ? ` (removing ${toRemove.join(', ')})` : ''}`)
      await removeReviewers(pr.owner, pr.repo, pr.number, toRemove)
      await addReviewer(pr.owner, pr.repo, pr.number, expectedHandle)
      return { linear: 0, github: 1 }
    }
    return { linear: 0, github: 0 }
  }

  // Process in chunks of CONCURRENCY
  for (let i = 0; i < pairs.length; i += CONCURRENCY) {
    const chunk = pairs.slice(i, i + CONCURRENCY)
    const results = await Promise.all(chunk.map(processPair))
    for (const r of results) {
      linearUpdates += r.linear
      githubUpdates += r.github
    }
  }

  log(`[oss-sync] done — Linear: ${linearUpdates} updates, GitHub: ${githubUpdates} updates`)
}

main().catch(err => { console.error('[oss-sync] fatal:', err); process.exit(1) })
