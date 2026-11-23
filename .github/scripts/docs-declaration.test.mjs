// node --test .github/scripts/docs-declaration.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  parseDocsLine, ticketIds, classifyTicket, isDocsSkipped, prNumberFromQueueRef,
  prNumbersFromCommits, evaluate, shouldFail, renderPrComment, renderRelease, gateMode,
} from './docs-declaration.mjs'

// The inline check this script replaces, kept verbatim as the oracle for the line rules.
function legacy(bodyIn) {
  const body = (bodyIn || '').replace(/<!--[\s\S]*?-->/g, '')
  const values = [...body.matchAll(/^[^\S\r\n]*Docs:[^\S\r\n]*(.*)$/gm)].map(m => m[1].trim())
  if (values.length > 1) return false
  const value = values[0] || ''
  if (/(https:\/\/github\.com\/future-agi\/docs\/pull\/\d+|future-agi\/docs#\d+)/i.test(value)) return true
  if (/^none(\s+needed)?\s*\(\s*[^)\s][^)]*\)/i.test(value)) return true
  return false
}

const TEMPLATE = `## Docs

<!-- Required, one line; CI fails without it. Does a user see or do anything differently?
     Then raise the docs PR now and link it:
       Docs: future-agi/docs#<n>    the docs PR for this change (or its full URL)
       Docs: none (<reason>)        e.g. none (internal refactor), none (tests only) -->

Docs:
`

const bodies = {
  shorthand: 'Docs: future-agi/docs#1137',
  url: 'Docs: https://github.com/future-agi/docs/pull/1146',
  two: 'Docs: future-agi/docs#1136 and future-agi/docs#1140',
  none: 'Docs: none (internal fix; no user-facing change)',
  noneNeeded: 'Docs: none needed (test-only)',
  indented: '   Docs: none (CI-only change)',
  untouchedTemplate: TEMPLATE,
  filledTemplate: TEMPLATE.replace(/\nDocs:\n$/, '\nDocs: future-agi/docs#12\n'),
  empty: '',
  noneNoReason: 'Docs: none',
  noneEmptyParens: 'Docs: none ( )',
  wrongRepo: 'Docs: future-agi/future-agi#4321',
  issueLink: 'Docs: https://github.com/future-agi/docs/issues/12',
  duplicate: 'Docs: none (a)\nDocs: future-agi/docs#3',
  lowercase: 'docs: rebuild the README',
  todo: 'Docs: TODO',
}

test('line rules match the original inline check on every case', () => {
  for (const [name, body] of Object.entries(bodies)) {
    const kind = parseDocsLine(body).kind
    assert.equal(['docs-pr', 'none'].includes(kind), legacy(body), name)
  }
})

test('parseDocsLine kinds and numbers', () => {
  assert.deepEqual(parseDocsLine(bodies.shorthand).numbers, [1137])
  assert.deepEqual(parseDocsLine(bodies.url).numbers, [1146])
  assert.deepEqual(parseDocsLine(bodies.two).numbers, [1136, 1140])
  assert.deepEqual(parseDocsLine(bodies.filledTemplate).numbers, [12])
  assert.equal(parseDocsLine(bodies.none).kind, 'none')
  assert.equal(parseDocsLine(bodies.noneNeeded).kind, 'none')
  assert.equal(parseDocsLine(bodies.untouchedTemplate).kind, 'missing')
  assert.equal(parseDocsLine(bodies.lowercase).kind, 'missing')
  assert.equal(parseDocsLine(bodies.duplicate).kind, 'duplicate')
  for (const k of ['noneNoReason', 'noneEmptyParens', 'wrongRepo', 'issueLink', 'todo']) assert.equal(parseDocsLine(bodies[k]).kind, 'invalid', k)
})

test('ticketIds reads title, body and branch, case-insensitive, deduped', () => {
  assert.deepEqual(ticketIds('fix(x): thing (TH-10622)', 'Linear: TH-10622, th-9', 'fix/th-123-foo'), ['TH-10622', 'TH-9', 'TH-123'])
  assert.deepEqual(ticketIds('feat: no ticket', '', 'feat/x'), [])
  assert.deepEqual(ticketIds('CSR-12 MATH-1'), [])
})

test('classifyTicket', () => {
  const cases = [
    [['Simulate', 'Feature'], 'qualifying'],
    [['Improvement'], 'qualifying'],
    [['oss-factory'], 'exempt'],
    [['software-factory'], 'exempt'],
    [['review-factory'], 'exempt'],
    [['general-factory'], 'exempt'],
    [['brand-new-factory'], 'exempt'],
    [['software-factory', 'Feature'], 'qualifying'],
    [['bug-factory'], 'exempt'],
    [['Simulate', 'Bug', 'Feature'], 'qualifying'],
    [['Bug'], 'exempt'],
    [['Bug', 'software-factory'], 'exempt'],
    [['Small', 'Simulate', 'FE+BE', 'Bug', 'review-factory'], 'exempt'],
    [['Small', 'Simulate', 'FE+BE', 'Improvement', 'review-factory'], 'qualifying'],
    [['security-factory'], 'exempt'],
    [['Improvement', 'no-docs'], 'exempt'],
    [['Feature', 'no-docs', 'review-factory'], 'exempt'],
    [['Improvement', 'security-factory'], 'exempt'],
    [['security-factory', 'Bug'], 'exempt'],
    [['bug-resolution-factory', 'Bug'], 'exempt'],
    [['docs-factory'], 'exempt'],
    [['sdlc:docs', 'Small', 'Bug'], 'exempt'],
    [[], 'unlabelled'],
    [['Simulate', 'FE+BE'], 'unlabelled'],
    [['sdlc:discovery', 'BE'], 'unlabelled'],
    [['PMM'], 'unlabelled'],
  ]
  for (const [labels, want] of cases) assert.equal(classifyTicket(labels), want, labels.join(','))
})

test('skips release-please and dev/main heads only', () => {
  assert.equal(isDocsSkipped({ head: 'release-please--branches--main' }), true)
  assert.equal(isDocsSkipped({ head: 'dev' }), true)
  assert.equal(isDocsSkipped({ head: 'main' }), true)
  assert.equal(isDocsSkipped({ head: 'feat/x' }), false)
  assert.equal(isDocsSkipped({ head: 'release/20261116' }), false)
})

test('merge queue ref → PR number', () => {
  assert.equal(prNumberFromQueueRef('refs/heads/gh-readonly-queue/dev/pr-4321-0123abcdef0123abcdef0123abcdef0123abcdef'), 4321)
  assert.equal(prNumberFromQueueRef('refs/heads/dev'), null)
  assert.equal(prNumberFromQueueRef(undefined), null)
})

test('PR numbers from release commits', () => {
  assert.deepEqual(prNumbersFromCommits([
    'Merge pull request #4327 from future-agi/fix/th-10530\n\nfix(gateway): …',
    'feat(simulate): run an epoch (#4249)',
    'fix: thing (#12) and more\n\nbody (#99)',
    'chore: no number',
    'Merge branch dev into main',
  ]), [4327, 4249])
})

const decl = b => parseDocsLine(b)

test('evaluate: line errors always, new-rule warnings', () => {
  assert.deepEqual(evaluate({ decl: decl(bodies.none) }), { errors: [], warnings: [] })
  assert.equal(evaluate({ decl: decl('') }).errors.length, 1)
  assert.equal(evaluate({ decl: decl(bodies.duplicate) }).errors.length, 1)
  assert.equal(evaluate({ decl: decl(bodies.todo) }).errors.length, 1)

  const open = evaluate({ decl: decl(bodies.shorthand), docsPrs: [{ number: 1137, found: true, state: 'open', merged: false }] })
  assert.deepEqual(open, { errors: [], warnings: [] })
  const merged = evaluate({ decl: decl(bodies.shorthand), docsPrs: [{ number: 1137, found: true, state: 'closed', merged: true }] })
  assert.deepEqual(merged, { errors: [], warnings: [] })
  const closed = evaluate({ decl: decl(bodies.shorthand), docsPrs: [{ number: 1137, found: true, state: 'closed', merged: false }] })
  assert.equal(closed.warnings.length, 1)
  assert.match(closed.warnings[0], /closed without merging/)
  const missing = evaluate({ decl: decl(bodies.shorthand), docsPrs: [{ number: 99999, found: false }] })
  assert.match(missing.warnings[0], /does not exist/)
  // A lookup that errored (found: null) is a note, never a warning or failure.
  assert.deepEqual(evaluate({ decl: decl(bodies.shorthand), docsPrs: [{ number: 1137, found: null }] }), { errors: [], warnings: [] })

  const tickets = [
    { id: 'TH-1', found: true, cls: 'unlabelled' },
    { id: 'TH-2', found: true, cls: 'qualifying' },
    { id: 'TH-3', found: false },
  ]
  const t = evaluate({ decl: decl(bodies.none), tickets })
  assert.equal(t.warnings.length, 1)
  assert.match(t.warnings[0], /^TH-1 has no type label/)
})

test('warn vs enforce', () => {
  const warnOnly = { errors: [], warnings: ['x'] }
  const err = { errors: ['x'], warnings: [] }
  assert.equal(shouldFail(warnOnly, 'warn'), false)
  assert.equal(shouldFail(warnOnly, 'enforce'), true)
  assert.equal(shouldFail(err, 'warn'), true)
  assert.equal(shouldFail({ errors: [], warnings: [] }, 'enforce'), false)
  assert.match(renderPrComment(warnOnly, 'warn'), /block merges from Tue 1 Dec/)
  assert.match(renderPrComment(err, 'warn'), /needs a fix/)
  assert.match(renderPrComment({ errors: [], warnings: [] }, 'warn'), /all good/)
})

test('release comment lists only unmerged docs PRs', () => {
  const rows = [
    { number: 1, title: 'feat: a', docs: [{ number: 10, found: true, state: 'open', merged: false }] },
    { number: 2, title: 'feat: b', docs: [{ number: 11, found: true, state: 'closed', merged: true }] },
  ]
  const body = renderRelease(rows, 'warn')
  assert.match(body, /#1 feat: a: future-agi\/docs#10 \(open\)/)
  assert.doesNotMatch(body, /#2 feat: b/)
  assert.match(renderRelease([rows[1]], 'enforce'), /every docs PR linked from this release has merged/)
})

test('gate turns hard at the start of Tue 1 Dec IST unless overridden', () => {
  assert.equal(gateMode(undefined, new Date('2026-11-30T18:29:59Z')), 'warn')
  assert.equal(gateMode('', new Date('2026-11-30T18:30:00Z')), 'enforce')
  assert.equal(gateMode(undefined, new Date('2027-01-15T00:00:00Z')), 'enforce')
  assert.equal(gateMode('warn', new Date('2027-01-15T00:00:00Z')), 'warn')
  assert.equal(gateMode('enforce', new Date('2026-11-24T00:00:00Z')), 'enforce')
  assert.equal(gateMode('bogus', new Date('2026-11-24T00:00:00Z')), 'warn')
})
