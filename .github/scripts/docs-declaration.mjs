#!/usr/bin/env node
// Docs declaration: every PR says where its docs are, the docs PR is real, and the docs ship
// with the feature.
//
// Every PR into dev/main carries exactly one `Docs:` line: a future-agi/docs PR
// (`future-agi/docs#<n>` or its URL), or `none (<reason>)` / `none needed (<reason>)`.
// On top of that line:
//   • the docs PR it names must exist and be open or merged
//   • every linked TH ticket must carry a type label (Bug, Feature, Improvement, a *-factory label)
//   • a qualifying ticket (Feature or Improvement; never security-factory) gets a
//     Docs sub-issue (`sdlc:docs`), is asked on Linear when its PR has no valid line, and the
//     sub-issue follows the docs PR: In Review when linked, Done when merged
//   • a docs PR still open 2 days after its feature merged to dev is nudged, and the dev→main
//     release PR lists every shipped feature whose docs PR has not merged
//
// The original line rules always fail. The newer rules (docs PR state, unlabelled tickets,
// release) warn until DOCS_GATE_MODE=enforce.
//
// Modes:
//   pr        the PR check (pull_request, merge_group)
//   release   the dev→main release PR check
//   sweep     hourly Linear/GitHub upkeep (B3 in the spec); --dry-run writes nothing
//   replay    read-only verdicts for merged PRs: --since YYYY-MM-DD [--base dev] [--tickets file.json]
//
// Env: GITHUB_TOKEN, GITHUB_REPOSITORY, GITHUB_EVENT_NAME, GITHUB_EVENT_PATH, GITHUB_STEP_SUMMARY,
//      LINEAR_API_KEY (optional: without it Linear is skipped), DOCS_GATE_MODE (warn|enforce)

import fs from 'node:fs'
import { pathToFileURL } from 'node:url'

export const DOCS_REPO = 'future-agi/docs'
const TEAM_KEY = 'TH'
const DOCS_LABEL = 'sdlc:docs'
export const FEATURE_LABELS = new Set(['Feature', 'Improvement'])
export const EXEMPT = new Set(['Bug', 'bug-factory', 'bug-resolution-factory', 'docs-factory', DOCS_LABEL])
// security-factory work is never asked; `no-docs` is the opt-out for internal work (CI, tooling)
// and for tickets that are themselves the docs.
const NEVER = new Set(['security-factory', 'no-docs'])
// A factory label says who builds the work, not whether users see it: alone it never asks for docs.
const FACTORY = /-factory$/
const SWEEP_WINDOW_HOURS = 48
const NUDGE_AFTER_DAYS = 2
const NUDGE_WINDOW_DAYS = 14
const PR_MARKER = '<!-- docs-declaration -->'
const RELEASE_MARKER = '<!-- docs-declaration:release -->'
const NUDGE_MARKER = '<!-- docs-declaration:nudge -->'

export const USAGE = 'Add one line to the PR body:\n' +
  '  Docs: future-agi/docs#<n>   (or the full https://github.com/future-agi/docs/pull/<n> URL)\n' +
  '  Docs: none (<why no docs change is needed>)'

// ── Rules (pure) ──────────────────────────────────────────────────────────────

// Same matching as the original inline check: comments stripped, case-sensitive `Docs:`.
export function parseDocsLine(body) {
  const text = (body || '').replace(/<!--[\s\S]*?-->/g, '')
  const values = [...text.matchAll(/^[^\S\r\n]*Docs:[^\S\r\n]*(.*)$/gm)].map(m => m[1].trim())
  if (values.length > 1) return { kind: 'duplicate', value: values.join(' | ') }
  const value = values[0] || ''
  if (!value) return { kind: 'missing', value }
  if (/(https:\/\/github\.com\/future-agi\/docs\/pull\/\d+|future-agi\/docs#\d+)/i.test(value)) {
    const numbers = [...value.matchAll(/(?:https:\/\/github\.com\/future-agi\/docs\/pull\/|future-agi\/docs#)(\d+)/gi)].map(m => Number(m[1]))
    return { kind: 'docs-pr', value, numbers: [...new Set(numbers)] }
  }
  if (/^none(\s+needed)?\s*\(\s*[^)\s][^)]*\)/i.test(value)) return { kind: 'none', value }
  return { kind: 'invalid', value }
}

export function ticketIds(...texts) {
  const ids = new Set()
  for (const t of texts) for (const m of (t || '').matchAll(new RegExp(`\\b${TEAM_KEY}-(\\d+)\\b`, 'gi'))) ids.add(`${TEAM_KEY}-${m[1]}`)
  return [...ids]
}

// Precedence: security-factory and no-docs are never asked; Feature/Improvement are asked even when also a
// Bug or a factory ticket; Bug-type work and factory-only tickets are labelled but not asked.
export function classifyTicket(labels) {
  if (labels.some(l => NEVER.has(l))) return 'exempt'
  if (labels.some(l => FEATURE_LABELS.has(l))) return 'qualifying'
  if (labels.some(l => EXEMPT.has(l) || FACTORY.test(l))) return 'exempt'
  return 'unlabelled'
}

export const isDocsSkipped = pr =>
  pr.head.startsWith('release-please--') || pr.head === 'dev' || pr.head === 'main'

// gh-readonly-queue/dev/pr-4321-<sha>
export function prNumberFromQueueRef(ref) {
  const m = /\/pr-(\d+)-[0-9a-f]+$/.exec(ref || '')
  return m ? Number(m[1]) : null
}

// Squash `(#123)` at the end of a subject, and `Merge pull request #123`.
export function prNumbersFromCommits(messages) {
  const out = new Set()
  for (const msg of messages) {
    const subject = (msg || '').split('\n')[0]
    const merge = /^Merge pull request #(\d+)/.exec(subject)
    if (merge) out.add(Number(merge[1]))
    const squash = /\(#(\d+)\)\s*$/.exec(subject)
    if (squash) out.add(Number(squash[1]))
  }
  return [...out]
}

// docsPrs: [{ number, found, state: 'open'|'closed', merged }]
// tickets: [{ id, found, cls }]
export function evaluate({ decl, docsPrs = [], tickets = [] }) {
  const errors = []
  const warnings = []
  if (decl.kind === 'duplicate') errors.push('More than one `Docs:` line. Keep one.')
  else if (decl.kind === 'missing') errors.push('`Docs:` line is missing or empty.')
  else if (decl.kind === 'invalid') errors.push(`\`Docs: ${decl.value}\` is not a docs PR link or \`none (<reason>)\`.`)
  for (const d of docsPrs) {
    if (d.found === false) warnings.push(`${DOCS_REPO}#${d.number} does not exist. Link the docs PR for this change.`)
    else if (d.state === 'closed' && !d.merged) warnings.push(`${DOCS_REPO}#${d.number} is closed without merging. Link an open or merged docs PR.`)
  }
  for (const t of tickets.filter(t => t.found && t.cls === 'unlabelled')) {
    warnings.push(`${t.id} has no type label. Label it \`Bug\`, \`Feature\` or \`Improvement\` in Linear, then re-run this check.`)
  }
  return { errors, warnings }
}

export function shouldFail({ errors, warnings }, mode) {
  return errors.length > 0 || (mode === 'enforce' && warnings.length > 0)
}

export function renderPrComment({ errors, warnings }, mode) {
  if (!errors.length && !warnings.length) return `${PR_MARKER}\n✅ **Docs declaration:** all good.`
  const failing = shouldFail({ errors, warnings }, mode)
  const lines = [PR_MARKER, failing ? '❌ **Docs declaration needs a fix**' : '⚠️ **Docs declaration: warnings** (these will block merges once `DOCS_GATE_MODE=enforce`)', '']
  for (const e of errors) lines.push(`- ${e}`)
  for (const w of warnings) lines.push(`- ${w}`)
  if (errors.length) lines.push('', '```', USAGE, '```')
  return lines.join('\n')
}

// ── IO ────────────────────────────────────────────────────────────────────────

const env = process.env
const MODE = env.DOCS_GATE_MODE === 'enforce' ? 'enforce' : 'warn'
const REPO = env.GITHUB_REPOSITORY || 'future-agi/future-agi'
let DRY = false
const log = (...a) => console.log(...a)

async function gh(path, { method = 'GET', body } = {}) {
  const res = await fetch(path.startsWith('http') ? path : `https://api.github.com${path}`, {
    method,
    headers: { Authorization: `Bearer ${env.GITHUB_TOKEN}`, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28', ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`GitHub ${method} ${path}: ${res.status} ${(await res.text()).slice(0, 300)}`)
  return res.status === 204 ? {} : res.json()
}

async function write(desc, fn) {
  if (DRY) { log(`  [dry] ${desc}`); return null }
  log(`  ${desc}`)
  return fn()
}

const toPr = p => ({ number: p.number, title: p.title || '', body: p.body || '', head: p.head.ref, base: p.base.ref, author: p.user?.login || '', mergedAt: p.merged_at, url: p.html_url })

async function docsPrStates(numbers) {
  return Promise.all(numbers.map(async number => {
    const p = await gh(`/repos/${DOCS_REPO}/pulls/${number}`)
    return p ? { number, found: true, state: p.state, merged: !!p.merged_at, url: p.html_url } : { number, found: false }
  }))
}

// Every page: bots can push a marker past the first 100 comments.
async function allComments(issueNumber) {
  const out = []
  for (let page = 1; ; page++) {
    const batch = await gh(`/repos/${REPO}/issues/${issueNumber}/comments?per_page=100&page=${page}`) || []
    out.push(...batch)
    if (batch.length < 100) return out
  }
}

async function upsertComment(issueNumber, marker, body) {
  const mine = (await allComments(issueNumber)).find(c => c.body?.includes(marker))
  if (mine) {
    if (mine.body === body) return
    return write(`update comment on #${issueNumber}`, () => gh(`/repos/${REPO}/issues/comments/${mine.id}`, { method: 'PATCH', body: { body } }))
  }
  return write(`comment on #${issueNumber}`, () => gh(`/repos/${REPO}/issues/${issueNumber}/comments`, { method: 'POST', body: { body } }))
}

async function hasComment(issueNumber, marker) {
  return (await allComments(issueNumber)).some(c => c.body?.includes(marker))
}

// ── Linear ────────────────────────────────────────────────────────────────────

const isNotFound = e => e.extensions?.code === 'INPUT_ERROR' && /^Entity not found/.test(e.message ?? '')

async function linear(query, variables = {}) {
  const res = await fetch('https://api.linear.app/graphql', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: env.LINEAR_API_KEY },
    body: JSON.stringify({ query, variables }),
  })
  const json = await res.json()
  if (json.errors?.length) {
    if (json.errors.every(isNotFound)) return null
    throw new Error(`Linear: ${JSON.stringify(json.errors).slice(0, 300)}`)
  }
  return json.data
}

const ISSUE_FIELDS = `
  id identifier title url createdAt
  state { type name }
  assignee { id }
  team { id }
  labels(first: 20) { nodes { name } }
  children(first: 5, filter: { labels: { name: { eq: "${DOCS_LABEL}" } } }) { nodes { id identifier state { type name } attachments(first: 10) { nodes { url } } } }
`

const toTicket = i => ({
  uuid: i.id, id: i.identifier, title: i.title, url: i.url, createdAt: i.createdAt, found: true,
  stateType: i.state?.type, assigneeId: i.assignee?.id ?? null, teamId: i.team?.id,
  labels: i.labels.nodes.map(l => l.name), cls: classifyTicket(i.labels.nodes.map(l => l.name)),
  docsChild: i.children.nodes[0] ? { uuid: i.children.nodes[0].id, id: i.children.nodes[0].identifier, stateType: i.children.nodes[0].state.type, stateName: i.children.nodes[0].state.name, attachments: i.children.nodes[0].attachments.nodes.map(a => a.url) } : null,
})

async function getTicket(id) {
  const data = await linear(`query($id: String!) { issue(id: $id) { ${ISSUE_FIELDS} } }`, { id })
  return data?.issue ? toTicket(data.issue) : { id, found: false }
}

const teamCache = new Map()
async function teamMeta(teamId) {
  if (!teamCache.has(teamId)) {
    const data = await linear(`query($id: String!) { team(id: $id) { states { nodes { id name type } } labels(first: 250) { nodes { id name } } } }`, { id: teamId })
    const states = data.team.states.nodes
    const pick = (name, type) => (states.find(s => s.name === name) ?? states.find(s => s.type === type))?.id
    let docsLabelId = data.team.labels.nodes.find(l => l.name === DOCS_LABEL)?.id
    if (!docsLabelId) {
      const ws = await linear(`query { issueLabels(first: 1, filter: { name: { eq: "${DOCS_LABEL}" }, team: { null: true } }) { nodes { id } } }`)
      docsLabelId = ws.issueLabels.nodes[0]?.id
    }
    teamCache.set(teamId, { todo: pick('Todo', 'unstarted'), inReview: pick('In Review', 'started'), done: pick('Done', 'completed'), canceled: pick('Canceled', 'canceled'), docsLabelId })
  }
  return teamCache.get(teamId)
}

async function linearCommentOnce(issueUuid, issueId, key, text) {
  const tag = `docs-declaration:${key}`
  const data = await linear(`query($id: String!, $tag: String!) { issue(id: $id) { comments(first: 1, filter: { body: { contains: $tag } }) { nodes { id } } } }`, { id: issueUuid, tag })
  if (data?.issue?.comments.nodes.length) return
  return write(`Linear ${issueId}: comment (${key})`, () =>
    linear(`mutation($issueId: String!, $body: String!) { commentCreate(input: { issueId: $issueId, body: $body }) { success } }`, { issueId: issueUuid, body: `${text}\n\n\`${tag}\`` }))
}

async function setState(issueUuid, issueId, stateId, name) {
  return write(`Linear ${issueId}: → ${name}`, () =>
    linear(`mutation($id: String!, $stateId: String!) { issueUpdate(id: $id, input: { stateId: $stateId }) { success } }`, { id: issueUuid, stateId }))
}

async function createDocsSubIssue(t) {
  // The PR check and the sweep can both reach a ticket in the same minute: look again first.
  const fresh = await getTicket(t.id)
  if (fresh.docsChild) return fresh.docsChild
  const meta = await teamMeta(t.teamId)
  const description = [
    `Docs for ${t.id}.`,
    '',
    'Link the docs PR in the feature PR body (`Docs: future-agi/docs#<n>`): this moves to In Review, and to Done when the docs PR merges.',
    'If no docs are needed, write `Docs: none (<reason>)` on the PR. This closes on its own when the parent closes without a docs PR.',
  ].join('\n')
  const input = { teamId: t.teamId, parentId: t.uuid, title: `Docs: ${t.title}`, description, stateId: meta.todo, labelIds: meta.docsLabelId ? [meta.docsLabelId] : [], ...(t.assigneeId ? { assigneeId: t.assigneeId } : {}) }
  const data = await write(`Linear ${t.id}: create Docs sub-issue [${t.labels.filter(l => FEATURE_LABELS.has(l) || FACTORY.test(l) || EXEMPT.has(l)).join(',')}] (${t.stateType})`, () =>
    linear(`mutation($input: IssueCreateInput!) { issueCreate(input: $input) { issue { id identifier } } }`, { input }))
  const issue = data?.issueCreate?.issue
  return issue ? { uuid: issue.id, id: issue.identifier, stateType: 'unstarted', attachments: [] } : null
}

// B2: what the PR's Docs line means for each qualifying ticket.
async function applyLinear(pr, decl, docsPrs, tickets) {
  const prRef = `${REPO}#${pr.number}`
  for (const t of tickets.filter(t => t.found)) {
    if (t.cls === 'unlabelled') {
      await linearCommentOnce(t.uuid, t.id, `label:${prRef}`, `This ticket has no type label, and [PR #${pr.number}](${pr.url}) can't pass its docs check until it does. Add \`Bug\`, \`Feature\` or \`Improvement\`.`)
      continue
    }
    if (t.cls !== 'qualifying') continue
    const validDocs = docsPrs.filter(d => d.found && !(d.state === 'closed' && !d.merged))
    if (decl.kind === 'docs-pr' && validDocs.length) {
      const child = t.docsChild ?? await createDocsSubIssue(t)
      if (!child) continue
      for (const d of validDocs) {
        if (child.attachments.includes(d.url)) continue
        await write(`Linear ${child.id}: attach ${d.url}`, () =>
          linear(`mutation($issueId: String!, $url: String!, $title: String!) { attachmentCreate(input: { issueId: $issueId, url: $url, title: $title }) { success } }`, { issueId: child.uuid, url: d.url, title: `${DOCS_REPO}#${d.number}` }))
      }
      if (['unstarted', 'backlog', 'triage'].includes(child.stateType)) {
        const meta = await teamMeta(t.teamId)
        await setState(child.uuid, child.id, meta.inReview, 'In Review')
      }
    } else if (decl.kind === 'none') {
      if (t.docsChild) await linearCommentOnce(t.docsChild.uuid, t.docsChild.id, `none:${prRef}`, `[PR #${pr.number}](${pr.url}) declares \`Docs: ${decl.value}\`.`)
    } else if (decl.kind !== 'docs-pr' || docsPrs.every(d => d.found === false || (d.found && d.state === 'closed' && !d.merged))) {
      // No line, a bad line, or a docs PR that is missing/closed. A docs PR we couldn't look up
      // (API error) is not treated as missing.
      await linearCommentOnce(t.uuid, t.id, `missing:${prRef}`, `[PR #${pr.number}](${pr.url}) needs a docs PR (\`Docs: future-agi/docs#<n>\`) or \`Docs: none (<reason>)\` before it can merge to dev.`)
    }
  }
}

// ── Shared evaluation ─────────────────────────────────────────────────────────

// A GitHub or Linear outage never fails the check: lookups that error become notes, and only
// the line rules (and, under enforce, a confirmed missing docs PR or unlabelled ticket) fail.
async function assess(pr, { ticketLabels } = {}) {
  const notes = []
  const decl = parseDocsLine(pr.body)
  let docsPrs = []
  if (decl.kind === 'docs-pr') {
    docsPrs = await docsPrStates(decl.numbers).catch(e => {
      notes.push(`Couldn't look up the docs PR (${e.message.slice(0, 120)}); not checked this run.`)
      return decl.numbers.map(number => ({ number, found: null }))
    })
  }
  const ids = ticketIds(pr.title, pr.body, pr.head)
  let tickets = []
  if (ticketLabels) tickets = ids.map(id => ticketLabels[id] ? { id, found: true, labels: ticketLabels[id], cls: classifyTicket(ticketLabels[id]) } : { id, found: false })
  else if (env.LINEAR_API_KEY) {
    tickets = await Promise.all(ids.map(id => getTicket(id).catch(e => {
      notes.push(`Couldn't read ${id} from Linear (${e.message.slice(0, 120)}); not checked this run.`)
      return { id, found: false, error: true }
    })))
  }
  return { decl, docsPrs, tickets, ids, notes, result: evaluate({ decl, docsPrs, tickets }) }
}

function summary(text) {
  if (env.GITHUB_STEP_SUMMARY) fs.appendFileSync(env.GITHUB_STEP_SUMMARY, `${text}\n`)
}

function report({ errors, warnings }) {
  for (const e of errors) console.log(`::error::${e}`)
  for (const w of warnings) console.log(`::warning::${w}`)
}

// ── Modes ─────────────────────────────────────────────────────────────────────

async function modePr() {
  const event = JSON.parse(fs.readFileSync(env.GITHUB_EVENT_PATH, 'utf8'))
  const isQueue = env.GITHUB_EVENT_NAME === 'merge_group'
  const number = isQueue ? prNumberFromQueueRef(event.merge_group?.head_ref) : event.pull_request?.number
  if (!number) throw new Error('no pull request number in the event')
  // The event payload has the body as it was at the event; re-read so a queue run sees edits.
  // If GitHub can't be read in the queue, let the PR through: it passed this check to get queued.
  let raw = event.pull_request
  if (isQueue) {
    raw = await gh(`/repos/${REPO}/pulls/${number}`).catch(e => { console.log(`::notice::Couldn't re-read PR #${number} (${e.message.slice(0, 120)}); passing on its review-time check.`); return null })
    if (!raw) return
  }
  const pr = toPr(raw)
  if (isDocsSkipped(pr)) return log(`skip: head ${pr.head}`)

  const { decl, docsPrs, tickets, notes, result } = await assess(pr)
  log(`PR #${pr.number}: Docs ${decl.kind}${decl.value ? ` "${decl.value}"` : ''}; tickets ${tickets.map(t => `${t.id}=${t.found ? t.cls : 'not-found'}`).join(', ') || 'none'}; mode ${MODE}`)
  report(result)
  for (const n of notes) console.log(`::notice::${n}`)
  summary(renderPrComment(result, MODE).replace(PR_MARKER, ''))

  if (!isQueue) {
    const hadComment = await hasComment(pr.number, PR_MARKER).catch(() => false)
    if (result.errors.length || result.warnings.length || hadComment) {
      await upsertComment(pr.number, PR_MARKER, renderPrComment(result, MODE)).catch(e => log(`comment skipped: ${e.message}`))
    }
    if (env.LINEAR_API_KEY) await applyLinear(pr, decl, docsPrs, tickets).catch(e => console.log(`::notice::Linear update skipped: ${e.message.slice(0, 200)}`))
  }
  if (shouldFail(result, MODE)) process.exitCode = 1
}

async function releasePrs(base, head) {
  const messages = []
  for (let page = 1; ; page++) {
    const cmp = await gh(`/repos/${REPO}/compare/${base}...${head}?per_page=100&page=${page}`)
    const commits = cmp?.commits ?? []
    messages.push(...commits.map(c => c.commit.message))
    if (commits.length < 100) break
  }
  return prNumbersFromCommits(messages)
}

export function renderRelease(rows, mode) {
  const pending = rows.filter(r => r.docs.some(d => !d.merged))
  if (!pending.length) return `${RELEASE_MARKER}\n✅ **Docs ready for release:** every docs PR linked from this release has merged.`
  const head = mode === 'enforce' ? '❌ **Docs not ready for release**' : '⚠️ **Docs not ready for release** (this will block once `DOCS_GATE_MODE=enforce`)'
  const lines = [RELEASE_MARKER, head, '', 'Merge these docs PRs, then re-run this check:', '']
  for (const r of pending) lines.push(`- #${r.number} ${r.title}: ${r.docs.filter(d => !d.merged).map(d => `${DOCS_REPO}#${d.number} (${d.found ? d.state : 'not found'})`).join(', ')}`)
  return lines.join('\n')
}

async function modeRelease() {
  const event = JSON.parse(fs.readFileSync(env.GITHUB_EVENT_PATH, 'utf8'))
  const pr = toPr(event.pull_request)
  const rows = await collectRelease(pr.base, pr.head)
  const body = renderRelease(rows, MODE)
  log(body)
  summary(body.replace(RELEASE_MARKER, ''))
  await upsertComment(pr.number, RELEASE_MARKER, body).catch(e => log(`comment skipped: ${e.message}`))
  if (MODE === 'enforce' && rows.some(r => r.docs.some(d => !d.merged))) process.exitCode = 1
}

async function collectRelease(base, head) {
  const numbers = await releasePrs(base, head)
  const rows = []
  for (const n of numbers) {
    const p = await gh(`/repos/${REPO}/pulls/${n}`)
    if (!p) continue
    const decl = parseDocsLine(p.body)
    if (decl.kind !== 'docs-pr') continue
    rows.push({ number: n, title: p.title, docs: await docsPrStates(decl.numbers) })
  }
  return rows
}

// Pages of 50 with every nested connection bounded, to stay under Linear's query complexity limit.
async function linearIssues(filter, fields = ISSUE_FIELDS) {
  const out = []
  let after = null
  do {
    const data = await linear(`query($filter: IssueFilter!, $after: String) { issues(filter: $filter, first: 50, after: $after) { nodes { ${fields} } pageInfo { hasNextPage endCursor } } }`, { filter, after })
    out.push(...data.issues.nodes)
    after = data.issues.pageInfo.hasNextPage ? data.issues.pageInfo.endCursor : null
  } while (after)
  return out
}

// One bad ticket or PR doesn't stop the rest of the sweep; the run still fails at the end.
let sweepFailures = 0
async function each(items, fn) {
  for (const item of items) {
    try { await fn(item) } catch (e) { sweepFailures++; console.log(`::warning::${e.message.slice(0, 300)}`) }
  }
}

// Search results already carry title, body, author and merge time. Only `full` (replay, which
// needs the branch) fetches each PR, one at a time: a burst of parallel GETs trips GitHub's
// secondary rate limit.
async function mergedPrs(base, sinceIso, { full = false } = {}) {
  const items = []
  for (let page = 1; ; page++) {
    const q = encodeURIComponent(`repo:${REPO} is:pr is:merged base:${base} merged:>=${sinceIso}`)
    const res = await gh(`/search/issues?q=${q}&per_page=100&page=${page}`)
    items.push(...res.items)
    if (res.items.length < 100) break
  }
  if (!full) {
    return items.map(i => ({ number: i.number, title: i.title || '', body: i.body || '', head: '', base, author: i.user?.login || '', mergedAt: i.pull_request?.merged_at, url: i.html_url }))
  }
  const out = []
  for (const i of items) out.push(toPr(await gh(`/repos/${REPO}/pulls/${i.number}`)))
  return out
}

async function modeSweep() {
  if (!env.LINEAR_API_KEY) throw new Error('LINEAR_API_KEY is required for sweep')
  const now = Date.now()
  const since = new Date(now - SWEEP_WINDOW_HOURS * 3600e3).toISOString()
  const open = { type: { nin: ['completed', 'canceled', 'duplicate'] } }

  log('1–2. qualifying tickets without a Docs sub-issue; new tickets without a type label')
  const recent = (await linearIssues({ team: { key: { eq: TEAM_KEY } }, updatedAt: { gte: since }, state: open })).map(toTicket)
  await each(recent, async t => {
    // Planned or in-flight work only: backlog, icebox and triage ideas don't get docs work yet.
    if (t.cls === 'qualifying' && !t.docsChild && ['unstarted', 'started'].includes(t.stateType)) await createDocsSubIssue(t)
    if (t.cls === 'unlabelled' && t.createdAt >= since) {
      await linearCommentOnce(t.uuid, t.id, 'label:new', 'This ticket has no type label. Add `Bug`, `Feature` or `Improvement` so its PRs know whether docs are expected.')
    }
  })

  log('3–4. Docs sub-issues whose docs merged, or whose parent closed without docs')
  const subs = await linearIssues(
    { team: { key: { eq: TEAM_KEY } }, labels: { name: { eq: DOCS_LABEL } }, parent: { null: false }, state: { type: { in: ['triage', 'backlog', 'unstarted', 'started'] } } },
    'id identifier team { id } state { type name } parent { identifier state { type } } attachments(first: 10) { nodes { url } }',
  )
  await each(subs, async s => {
    const meta = await teamMeta(s.team.id)
    const docsUrls = s.attachments.nodes.map(a => a.url).filter(u => u.startsWith(`https://github.com/${DOCS_REPO}/pull/`))
    if (docsUrls.length && s.state.type === 'started') {
      const states = await docsPrStates(docsUrls.map(u => Number(u.split('/').pop())))
      if (states.every(d => d.merged)) await setState(s.id, s.identifier, meta.done, 'Done')
    } else if (!docsUrls.length && s.state.type !== 'started' && ['completed', 'canceled'].includes(s.parent.state.type)) {
      await linearCommentOnce(s.id, s.identifier, 'orphan', `${s.parent.identifier} closed without a docs PR linked, so no docs are expected.`)
      await setState(s.id, s.identifier, meta.canceled, 'Canceled')
    }
  })

  log(`5. docs PRs still open ${NUDGE_AFTER_DAYS}+ days after their feature merged to dev`)
  const prs = await mergedPrs('dev', new Date(now - NUDGE_WINDOW_DAYS * 864e5).toISOString().slice(0, 10))
  await each(prs, async pr => {
    if (now - Date.parse(pr.mergedAt) < NUDGE_AFTER_DAYS * 864e5) return
    const decl = parseDocsLine(pr.body)
    if (decl.kind !== 'docs-pr') return
    const stalled = (await docsPrStates(decl.numbers)).filter(d => d.found && d.state === 'open')
    if (!stalled.length) return
    const refs = stalled.map(d => `${DOCS_REPO}#${d.number}`).join(', ')
    if (!(await hasComment(pr.number, NUDGE_MARKER))) {
      await write(`nudge #${pr.number} (${refs})`, () => gh(`/repos/${REPO}/issues/${pr.number}/comments`, { method: 'POST', body: { body: `${NUDGE_MARKER}\n@${pr.author} this merged to dev ${Math.floor((now - Date.parse(pr.mergedAt)) / 864e5)} days ago and its docs PR is still open: ${refs}. It has to merge before the next release to main. Please get it reviewed.` } }))
    }
    for (const id of ticketIds(pr.title, pr.body, pr.head)) {
      const t = await getTicket(id)
      if (t.found && t.cls === 'qualifying') await linearCommentOnce(t.uuid, t.id, `nudge:${REPO}#${pr.number}`, `[PR #${pr.number}](${pr.url}) is on dev, but its docs PR (${refs}) is still open. It has to merge before the next release to main.`)
    }
  })
  if (sweepFailures) throw new Error(`${sweepFailures} sweep item(s) failed; see warnings above`)
}

async function modeReplay(args) {
  const since = args.since ?? new Date(Date.now() - 7 * 864e5).toISOString().slice(0, 10)
  const ticketLabels = args.tickets ? JSON.parse(fs.readFileSync(args.tickets, 'utf8')) : undefined
  const prs = (await mergedPrs(args.base ?? 'dev', since, { full: true })).filter(pr => !isDocsSkipped(pr)).sort((a, b) => b.number - a.number)
  console.log(['pr', 'docs', 'line-rules', 'new-rules', 'tickets', 'detail'].join('\t'))
  for (const pr of prs) {
    const { decl, docsPrs, tickets, result } = await assess(pr, { ticketLabels })
    console.log([
      `#${pr.number}`,
      decl.kind,
      result.errors.length ? 'FAIL' : 'pass',
      result.warnings.length ? 'WARN' : 'ok',
      tickets.map(t => `${t.id}=${t.found ? t.cls : 'not-found'}`).join(',') || '-',
      [...docsPrs.map(d => `docs#${d.number}:${d.found ? (d.merged ? 'merged' : d.state) : 'not-found'}`), ...result.errors, ...result.warnings].join(' | '),
    ].join('\t'))
  }
}

function parseArgs(argv) {
  const args = { _: [] }
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--dry-run') args.dryRun = true
    else if (argv[i].startsWith('--')) args[argv[i].slice(2)] = argv[++i]
    else args._.push(argv[i])
  }
  return args
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  const args = parseArgs(process.argv.slice(2))
  DRY = !!args.dryRun || env.DRY_RUN === 'true'
  const modes = { pr: modePr, release: modeRelease, sweep: modeSweep, replay: () => modeReplay(args), 'release-preview': async () => {
    const rows = await collectRelease(args.base ?? 'main', args.head ?? 'dev')
    console.log(renderRelease(rows, MODE).replace(RELEASE_MARKER, ''))
  } }
  const run = modes[args._[0]]
  if (!run) { console.error(`usage: docs-declaration.mjs ${Object.keys(modes).join('|')}`); process.exit(2) }
  if (!env.GITHUB_TOKEN) { console.error('GITHUB_TOKEN is required'); process.exit(2) }
  run().catch(e => { console.error(e); process.exit(1) })
}
