#!/usr/bin/env node
// Auto-move Linear tickets to Done on a platform release.
//
// When a stable release tag (vX.Y.Z) lands on main, find every Linear ticket
// shipped in the release, move it to its team's Done state, comment the release
// link on it, and post a Slack summary.
//
// Ticket discovery: read the GitHub Release body (the release-please changelog),
// collect the PRs in the release, pull each PR's title + body, and extract
// Tech (TH-) identifiers. One PR can reference several tickets, so every textual
// reference counts. Branch names are intentionally NOT used — the repo
// convention (type/short-description) carries no ticket id.
//
// Scope: Tech team only. Customer (CSR-) tickets are intentionally left alone.
//
// Env:
//   LINEAR_API_KEY     Linear service-account key (member of the Tech team)
//   GITHUB_TOKEN       default Actions token (read releases/PRs/commits)
//   GITHUB_REPOSITORY  owner/repo (e.g. future-agi/future-agi)
//   VERSION            release tag, e.g. v1.23.0
//   RELEASE_URL        link to the release (used in the Linear comment)
//   SLACK_WEBHOOK_URL  optional; a summary is posted here when set
//   DRY_RUN            "true" → log intended writes only, change nothing

const {
  LINEAR_API_KEY,
  GITHUB_TOKEN,
  GITHUB_REPOSITORY,
  VERSION,
  RELEASE_URL,
  SLACK_WEBHOOK_URL,
  DRY_RUN,
} = process.env

if (!LINEAR_API_KEY)    die('LINEAR_API_KEY is required')
if (!GITHUB_TOKEN)      die('GITHUB_TOKEN is required')
if (!GITHUB_REPOSITORY) die('GITHUB_REPOSITORY is required')
if (!VERSION)           die('VERSION is required')

const [OWNER, REPO] = GITHUB_REPOSITORY.split('/')
const DRY = DRY_RUN === 'true'

// Linear team prefixes to look for. Tech only by design; add keys to widen scope.
const TEAM_PREFIXES = ['TH']
const ID_RE = new RegExp(`\\b(${TEAM_PREFIXES.join('|')})-\\d+\\b`, 'gi')

// State types we never move away from — already terminal, or not real in-flight
// work. Guards against "resurrecting" a ticket a PR merely name-drops.
const SKIP_TYPES = new Set(['completed', 'canceled', 'duplicate', 'triage', 'backlog'])

const CONCURRENCY = 8

// ── Linear API ──────────────────────────────────────────────────────────────

async function linearQuery(query, variables = {}) {
  const res = await fetch('https://api.linear.app/graphql', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: LINEAR_API_KEY },
    body: JSON.stringify({ query, variables }),
  })
  const json = await res.json()
  if (json.errors) throw new Error(`Linear API error: ${JSON.stringify(json.errors)}`)
  return json.data
}

// Linear's issue(id:) accepts the human identifier (e.g. "TH-123") as well as
// the UUID. Returns null when the ticket doesn't exist.
async function resolveIssue(identifier) {
  const data = await linearQuery(`
    query($id: String!) {
      issue(id: $id) {
        id
        identifier
        state { id name type }
        team { key states { nodes { id name type } } }
      }
    }
  `, { id: identifier })
  const issue = data?.issue
  if (!issue) return null
  const states = issue.team?.states?.nodes ?? []
  const done =
    states.find(s => s.type === 'completed' && s.name === 'Done') ??
    states.find(s => s.type === 'completed')
  return {
    id:          issue.id,
    identifier:  issue.identifier,
    stateName:   issue.state?.name ?? '(unknown)',
    stateType:   issue.state?.type ?? '(unknown)',
    doneStateId: done?.id ?? null,
    doneName:    done?.name ?? null,
  }
}

async function moveIssueToDone(issue) {
  if (DRY) { log(`  [dry] ${issue.identifier}: ${issue.stateName} → ${issue.doneName}`); return true }
  const data = await linearQuery(`
    mutation($id: String!, $stateId: String!) {
      issueUpdate(id: $id, input: { stateId: $stateId }) { success }
    }
  `, { id: issue.id, stateId: issue.doneStateId })
  return data?.issueUpdate?.success === true
}

async function commentOnIssue(issue, body) {
  if (DRY) { log(`  [dry] ${issue.identifier}: comment "${body}"`); return }
  await linearQuery(`
    mutation($issueId: String!, $body: String!) {
      commentCreate(input: { issueId: $issueId, body: $body }) { success }
    }
  `, { issueId: issue.id, body })
}

// ── GitHub API ────────────────────────────────────────────────────────────────

async function gh(path) {
  const res = await fetch(`https://api.github.com${path}`, {
    headers: {
      Authorization: `token ${GITHUB_TOKEN}`,
      Accept: 'application/vnd.github+json',
      'User-Agent': 'linear-release-done',
    },
  })
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`GitHub API ${res.status} on ${path}: ${await res.text()}`)
  return res.json()
}

async function getReleaseBody(version) {
  const rel = await gh(`/repos/${OWNER}/${REPO}/releases/tags/${encodeURIComponent(version)}`)
  return rel?.body ?? ''
}

async function getPrText(number) {
  const pr = await gh(`/repos/${OWNER}/${REPO}/pulls/${number}`)
  if (!pr) return ''
  return `${pr.title ?? ''}\n${pr.body ?? ''}`
}

// Fallback when the release body has no PR references: diff the previous stable
// tag against this one and read PR numbers out of the commit subjects.
async function previousStableTag(version) {
  const tags = await gh(`/repos/${OWNER}/${REPO}/tags?per_page=100`)
  const stable = (tags ?? [])
    .map(t => t.name)
    .filter(n => /^v\d+\.\d+\.\d+$/.test(n))
    .sort(cmpSemverDesc)
  const idx = stable.indexOf(version)
  return idx >= 0 && idx + 1 < stable.length ? stable[idx + 1] : null
}

async function prNumbersFromCompare(version) {
  const prev = await previousStableTag(version)
  if (!prev) return []
  const cmp = await gh(`/repos/${OWNER}/${REPO}/compare/${prev}...${version}`)
  const nums = new Set()
  for (const c of cmp?.commits ?? []) {
    for (const m of (c.commit?.message ?? '').matchAll(/\(#(\d+)\)/g)) nums.add(Number(m[1]))
  }
  return [...nums]
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function cmpSemverDesc(a, b) {
  const pa = a.slice(1).split('.').map(Number)
  const pb = b.slice(1).split('.').map(Number)
  for (let i = 0; i < 3; i++) if (pa[i] !== pb[i]) return pb[i] - pa[i]
  return 0
}

function extractIds(text) {
  const out = new Set()
  for (const m of (text ?? '').matchAll(ID_RE)) out.add(m[0].toUpperCase())
  return out
}

async function mapWithConcurrency(items, limit, fn) {
  const results = []
  for (let i = 0; i < items.length; i += limit) {
    results.push(...await Promise.all(items.slice(i, i + limit).map(fn)))
  }
  return results
}

function log(...a) { console.log(...a) }
function die(msg) { console.error(`[linear-release-done] ERROR: ${msg}`); process.exit(1) }

// ── Slack ─────────────────────────────────────────────────────────────────────

async function postSlack({ moved, skipped, notFound }) {
  if (!SLACK_WEBHOOK_URL) { log('SLACK_WEBHOOK_URL not set — skipping Slack summary'); return }
  const relLink = RELEASE_URL ? `<${RELEASE_URL}|${VERSION}>` : VERSION
  const lines = [
    `${DRY ? '🧪 *[DRY RUN]* ' : '🚀 '}*Release ${relLink}* — Linear sync`,
    moved.length ? `✅ *Moved to Done (${moved.length}):* ${moved.map(m => m.identifier).join(', ')}` : '✅ *Moved to Done:* none',
  ]
  if (skipped.length)  lines.push(`⏭️ *Skipped (${skipped.length}):* ${skipped.map(s => `${s.identifier} (${s.reason})`).join(', ')}`)
  if (notFound.length) lines.push(`❓ *Not found (${notFound.length}):* ${notFound.join(', ')}`)
  const text = lines.join('\n')
  if (DRY) { log('[dry] would post to Slack:\n' + text); return }
  const res = await fetch(SLACK_WEBHOOK_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
  if (!res.ok) log(`⚠  Slack post failed: ${res.status} ${await res.text()}`)
}

// ── Main ──────────────────────────────────────────────────────────────────────

async function main() {
  log(`[linear-release-done] ${VERSION}${DRY ? ' (dry run)' : ''} on ${GITHUB_REPOSITORY}`)

  const body = await getReleaseBody(VERSION)
  const prNumbers = new Set()
  for (const m of body.matchAll(/#(\d+)/g)) prNumbers.add(Number(m[1]))

  if (prNumbers.size === 0) {
    log('no PR references in release body — falling back to tag compare')
    for (const n of await prNumbersFromCompare(VERSION)) prNumbers.add(n)
  }
  log(`[linear-release-done] ${prNumbers.size} PR(s) in release`)

  // Union of ids found directly in the release body + each PR's title/body.
  const ids = extractIds(body)
  const prTexts = await mapWithConcurrency([...prNumbers], CONCURRENCY, getPrText)
  for (const t of prTexts) for (const id of extractIds(t)) ids.add(id)

  const identifiers = [...ids].sort()
  log(`[linear-release-done] ${identifiers.length} ticket reference(s): ${identifiers.join(', ') || '(none)'}`)

  const moved = [], skipped = [], notFound = []

  await mapWithConcurrency(identifiers, CONCURRENCY, async (identifier) => {
    const issue = await resolveIssue(identifier)
    if (!issue) { log(`  ❓ ${identifier}: not found in Linear`); notFound.push(identifier); return }
    if (SKIP_TYPES.has(issue.stateType)) {
      log(`  ⏭️  ${issue.identifier}: in "${issue.stateName}" — skipped`)
      skipped.push({ identifier: issue.identifier, reason: issue.stateName }); return
    }
    if (!issue.doneStateId) {
      log(`  ⚠  ${issue.identifier}: team has no completed/Done state — skipped`)
      skipped.push({ identifier: issue.identifier, reason: 'no Done state' }); return
    }
    const ok = await moveIssueToDone(issue)
    if (!ok) { skipped.push({ identifier: issue.identifier, reason: 'update failed' }); return }
    await commentOnIssue(issue, `Shipped in [${VERSION}](${RELEASE_URL || ''}).`)
    log(`  ✅ ${issue.identifier}: ${issue.stateName} → ${issue.doneName}`)
    moved.push({ identifier: issue.identifier })
  })

  log(`[linear-release-done] done — moved ${moved.length}, skipped ${skipped.length}, not-found ${notFound.length}`)
  await postSlack({ moved, skipped, notFound })
}

main().catch(err => { console.error('[linear-release-done] fatal:', err); process.exit(1) })
