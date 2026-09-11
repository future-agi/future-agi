#!/usr/bin/env node
// Reconcile Linear tickets against a platform release.
//
// When a stable release tag (vX.Y.Z) lands on main, find every Tech (TH-) ticket
// shipped in the release and act on it by its current state:
//   • "Ready to Merge"        → move to Done + comment the release link (auto-close)
//   • any other in-flight state (In Review / In Progress / Todo / …)
//                             → do NOT auto-close; comment on the ticket, and list
//                               it in a single #tech message asking owners to close
//   • terminal / not-in-flight (Done, Canceled, Backlog, Icebox, Triage, On hold)
//                             → skip
// The goal is that shipped tickets end up closed: what can be closed safely is
// closed automatically; the rest are surfaced in #tech so their owners close them.
//
// Ticket discovery: diff the previous stable tag against this one, harvest PR
// numbers from the commit messages (squash `(#123)` and `Merge pull request #123`),
// and union TH- ids from the release body, commit messages, and each PR's title +
// body — so a ticket referenced only in a PR description is still caught, and one
// PR can close several tickets. Branch names are NOT used (repo convention
// `type/short-description` carries no ticket id).
//
// Scope: Tech team only. Customer (CSR-) tickets are intentionally left alone.
//
// Env:
//   LINEAR_API_KEY     Linear service-account key (member of the Tech team)
//   GITHUB_TOKEN       default Actions token (read releases/PRs/commits)
//   GITHUB_REPOSITORY  owner/repo (e.g. future-agi/future-agi)
//   VERSION            release tag, e.g. v1.23.0
//   RELEASE_URL        link to the release (used in comments/messages)
//   SLACK_BOT_TOKEN    bot token (chat:write) — posts the #tech message + summary
//   SLACK_TECH_CHANNEL channel id for the nudge message + summary (e.g. C06...)
//   SLACK_WEBHOOK_URL  optional fallback when SLACK_BOT_TOKEN is unset
//   DRY_RUN            "true" → log intended writes only, change nothing

const {
  LINEAR_API_KEY,
  GITHUB_TOKEN,
  GITHUB_REPOSITORY,
  VERSION,
  RELEASE_URL,
  SLACK_BOT_TOKEN,
  SLACK_TECH_CHANNEL,
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

// State whose tickets are auto-closed to Done (clearly shipped + approved).
const CLOSE_STATES = new Set(['Ready to Merge'])
// Never act on these — terminal, or explicitly not in-flight. Everything that is
// neither a CLOSE_STATE nor skipped is treated as "in flight" → surfaced in #tech.
const SKIP_TYPES = new Set(['completed', 'canceled', 'duplicate', 'triage', 'backlog'])
const SKIP_STATE_NAMES = new Set(['On hold'])

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
        title
        url
        state { id name type }
        assignee { displayName }
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
    id:           issue.id,
    identifier:   issue.identifier,
    title:        issue.title ?? '',
    url:          issue.url ?? '',
    stateName:    issue.state?.name ?? '(unknown)',
    stateType:    issue.state?.type ?? '(unknown)',
    assigneeName: issue.assignee?.displayName ?? null,
    doneStateId:  done?.id ?? null,
    doneName:     done?.name ?? null,
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

async function previousStableTag(version) {
  const tags = await gh(`/repos/${OWNER}/${REPO}/tags?per_page=100`)
  const stable = (tags ?? [])
    .map(t => t.name)
    .filter(n => /^v\d+\.\d+\.\d+$/.test(n))
    .sort(cmpSemverDesc)
  const idx = stable.indexOf(version)
  return idx >= 0 && idx + 1 < stable.length ? stable[idx + 1] : null
}

// Diff the previous stable tag against this one and harvest PR numbers and ticket
// ids from every commit message. release-please writes commit-SHA links (not PR
// refs) into the release body, so this compare — not the body — is the reliable
// source of PRs.
async function compareData(version) {
  const prev = await previousStableTag(version)
  if (!prev) return { prev: null, prNumbers: [], ids: new Set() }
  const cmp = await gh(`/repos/${OWNER}/${REPO}/compare/${prev}...${version}`)
  const prNumbers = new Set()
  const ids = new Set()
  for (const c of cmp?.commits ?? []) {
    const msg = c.commit?.message ?? ''
    // Only the two real merge formats — squash `(#123)` and merge-commit
    // `Merge pull request #123` — so stray `#123` mentions in prose don't drag
    // unrelated PRs (and their tickets) into the release.
    for (const m of msg.matchAll(/(?:\(#|Merge pull request #)(\d+)/g)) prNumbers.add(Number(m[1]))
    for (const id of extractIds(msg)) ids.add(id)
  }
  return { prev, prNumbers: [...prNumbers], ids }
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

async function slackPost(text) {
  if (DRY) { log('[dry] would post to Slack:\n' + text); return }
  if (SLACK_BOT_TOKEN && SLACK_TECH_CHANNEL) {
    const res = await fetch('https://slack.com/api/chat.postMessage', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8', Authorization: `Bearer ${SLACK_BOT_TOKEN}` },
      body: JSON.stringify({ channel: SLACK_TECH_CHANNEL, text, unfurl_links: false }),
    })
    const json = await res.json()
    if (!json.ok) log(`⚠  Slack chat.postMessage failed: ${json.error}`)
    return
  }
  if (SLACK_WEBHOOK_URL) {
    const res = await fetch(SLACK_WEBHOOK_URL, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) })
    if (!res.ok) log(`⚠  Slack webhook failed: ${res.status} ${await res.text()}`)
    return
  }
  log('No Slack target configured — skipping message')
}

// A single #tech message: what was auto-closed, and — the important part — what
// shipped but still needs its owner to close it.
async function postSlack({ moved, nudged, skipped, notFound }) {
  const relLink = RELEASE_URL ? `<${RELEASE_URL}|${VERSION}>` : VERSION
  const lines = [`${DRY ? '🧪 *[DRY RUN]* ' : '🚀 '}*Release ${relLink}* — Linear sync`]
  lines.push(moved.length ? `✅ *Auto-closed (${moved.length}):* ${moved.map(m => m.identifier).join(', ')}` : '✅ *Auto-closed:* none')
  if (nudged.length) {
    lines.push(`📣 *Please close if complete (${nudged.length}):*`)
    for (const n of nudged) {
      lines.push(`   • <${n.url}|${n.identifier}> — ${n.stateName}${n.assigneeName ? ` — ${n.assigneeName}` : ' — unassigned'}`)
    }
  }
  if (skipped.length)  lines.push(`⏭️ *Skipped (${skipped.length}):* already Done/terminal`)
  if (notFound.length) lines.push(`❓ *Not found (${notFound.length}):* ${notFound.join(', ')}`)
  await slackPost(lines.join('\n'))
}

// ── Main ──────────────────────────────────────────────────────────────────────

async function main() {
  log(`[linear-release-done] ${VERSION}${DRY ? ' (dry run)' : ''} on ${GITHUB_REPOSITORY}`)

  // PR numbers from the tag compare (reliable) plus any written into the release
  // body. Ids unioned from release body, commit messages, and each PR's title +
  // body so a ticket referenced in ANY of them is caught.
  const body = await getReleaseBody(VERSION)
  const { prev, prNumbers: cmpPrNumbers, ids: cmpIds } = await compareData(VERSION)

  const prNumbers = new Set(cmpPrNumbers)
  for (const m of body.matchAll(/#(\d+)/g)) prNumbers.add(Number(m[1]))
  log(`[linear-release-done] ${prNumbers.size} PR(s) since ${prev ?? '(no previous tag)'}`)

  const ids = new Set([...extractIds(body), ...cmpIds])
  const prTexts = await mapWithConcurrency([...prNumbers], CONCURRENCY, getPrText)
  for (const t of prTexts) for (const id of extractIds(t)) ids.add(id)

  const identifiers = [...ids].sort()
  log(`[linear-release-done] ${identifiers.length} ticket reference(s): ${identifiers.join(', ') || '(none)'}`)

  const moved = [], nudged = [], skipped = [], notFound = []

  await mapWithConcurrency(identifiers, CONCURRENCY, async (identifier) => {
    const issue = await resolveIssue(identifier)
    if (!issue) { log(`  ❓ ${identifier}: not found in Linear`); notFound.push(identifier); return }

    // Skip terminal / not-in-flight states.
    if (SKIP_TYPES.has(issue.stateType) || SKIP_STATE_NAMES.has(issue.stateName)) {
      log(`  ⏭️  ${issue.identifier}: in "${issue.stateName}" — skipped`)
      skipped.push({ identifier: issue.identifier, reason: issue.stateName }); return
    }

    // Auto-close the states that mean "shipped + approved".
    if (CLOSE_STATES.has(issue.stateName)) {
      if (!issue.doneStateId) {
        log(`  ⚠  ${issue.identifier}: team has no Done state — skipped`)
        skipped.push({ identifier: issue.identifier, reason: 'no Done state' }); return
      }
      const ok = await moveIssueToDone(issue)
      if (!ok) { skipped.push({ identifier: issue.identifier, reason: 'update failed' }); return }
      await commentOnIssue(issue, `Shipped in [${VERSION}](${RELEASE_URL || ''}). Auto-closed by the release ticket-sync.`)
      log(`  ✅ ${issue.identifier}: ${issue.stateName} → ${issue.doneName}`)
      moved.push({ identifier: issue.identifier }); return
    }

    // In-flight but not "Ready to Merge": comment on the ticket and collect it for
    // the #tech round-up so its owner closes it.
    await commentOnIssue(issue, `Shipped in [${VERSION}](${RELEASE_URL || ''}) but this ticket is still in "${issue.stateName}". If the work is complete, please move it to Done — it wasn't auto-closed because it wasn't in "Ready to Merge".`)
    log(`  📣 ${issue.identifier}: in "${issue.stateName}" — flagged for #tech`)
    nudged.push({ identifier: issue.identifier, stateName: issue.stateName, assigneeName: issue.assigneeName, url: issue.url })
  })

  log(`[linear-release-done] done — closed ${moved.length}, flagged ${nudged.length}, skipped ${skipped.length}, not-found ${notFound.length}`)
  await postSlack({ moved, nudged, skipped, notFound })
}

main().catch(err => { console.error('[linear-release-done] fatal:', err); process.exit(1) })
