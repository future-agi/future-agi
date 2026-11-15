// Plain-language summaries for Skills views. Each derives only from server data; when data is
// missing or partial they say so instead of inferring a result.
import { isActiveSkillJob } from 'src/api/skills/testing';
import { DESCRIPTOR_PATH, linkChanges } from 'src/api/skills/j6-recovery';

export const passedAll = (variant) => Boolean(variant?.attempts?.length) && variant.attempts.every((item) => item.answer_verdict === 'pass');

// Attempts that have not begun. A running job whose every attempt is still waiting reads as queued.
const WAITING = new Set(['queued', 'pending', 'not_started']);
const allWaiting = (rows = []) => {
  const attempts = rows.flatMap((row) => (row.variants || []).flatMap((variant) => variant.attempts || []));
  return attempts.length > 0 && attempts.every((attempt) => WAITING.has(attempt.status));
};
// One sentence that says what the answer check found, before any identifiers.
// It follows the reconciled job state: queued, running, stopping, finished or stopped.
export function answerConclusion({ status, subject, rows = [], complete }) {
  if (status === 'queued' || (status === 'running' && allWaiting(rows))) return `Answer check queued for ${subject}.`;
  if (status === 'cancelling') return `Answer check stopping for ${subject}.`;
  if (isActiveSkillJob(status)) return `Answer check running for ${subject}.`;
  if (status !== 'completed') return `Answer check ${String(status || 'stopped').replaceAll('_', ' ')} for ${subject}. No result is inferred.`;
  if (!complete || !rows.length) return `Answer check finished for ${subject}.`;
  const passed = rows.filter((row) => passedAll(row.variants.find((variant) => variant.variant === 'candidate'))).length;
  const baseline = rows.every((row) => passedAll(row.variants.find((variant) => variant.variant !== 'candidate')));
  const lead = passed === rows.length ? `Answer check passed for ${subject} on ${passed} of ${rows.length} case${rows.length === 1 ? '' : 's'}` : `Answer check failed for ${subject} on ${rows.length - passed} of ${rows.length} case${rows.length === 1 ? '' : 's'}`;
  return `${lead}${baseline && rows.some((row) => row.variants.length > 1) ? ' (also passes without the context)' : ''}.`;
}
export const mountName = (mount) => String(mount || "").replace(/^skills\//, "").replace(/\/$/, "");
const ROOT_INSTRUCTIONS = 'AGENTS.md';
const plural = (count, one, many) => `${count} ${count === 1 ? one : many}`;
// "Publishing creates support-agent v2: refund-policy v1 → v2; AGENTS.md is unchanged."
// Linked-skill moves come from the descriptor diff; the clause after ";" is always about the
// context's own AGENTS.md (and any other context files), never a blanket "no instruction changes".
export function publishSentence(name, review) {
  const next = (review.expectedHeadVersion ?? 0) + 1;
  const changes = review.changes || [];
  const paths = changes.map((file) => file.path);
  const descriptor = changes.find((file) => file.path === DESCRIPTOR_PATH);
  const moves = descriptor ? linkChanges(descriptor.fromText ?? null, descriptor.toText ?? null) : null;
  const locked = new Map((review.acceptedLock?.links || []).map((link) => [mountName(link.mount), link.version]));
  const moved = new Set((moves || []).map((row) => row.name));
  const skills = [
    ...(moves || []).map((row) => (row.from == null ? `${row.name} v${row.to} added` : row.to == null ? `${row.name} removed` : `${row.name} v${row.from} → v${row.to}`)),
    // Skill files that changed without a readable descriptor move: name the locked version when known.
    ...[...new Set(paths.filter((path) => path.startsWith('skills/')).map((path) => path.split('/')[1]))]
      .filter((skill) => !moved.has(skill))
      .map((skill) => (locked.has(skill) ? `${skill} v${locked.get(skill)}` : `${skill} changes`)),
  ];
  const others = paths.filter((path) => path !== ROOT_INSTRUCTIONS && path !== DESCRIPTOR_PATH && !path.startsWith('skills/') && !path.startsWith('.futureagi/')).length;
  const own = [
    paths.includes(ROOT_INSTRUCTIONS) ? `${ROOT_INSTRUCTIONS} changes` : `${ROOT_INSTRUCTIONS} is unchanged`,
    ...(others ? [`${plural(others, 'other context file changes', 'other context files change')}`] : []),
  ].join(', ');
  return `Publishing creates ${name || 'this context'} v${next}: ${skills.length ? skills.join(', ') : 'no linked-skill changes'}; ${own}.`;
}
// "Preview expires in 30 min": relative to the review clock; the exact UTC time is in details.
export function expiresIn(expiresAt, now) {
  const left = Date.parse(expiresAt) - now;
  if (!Number.isFinite(left)) return "Preview expiry not reported";
  const minutes = Math.max(1, Math.round(left / 60000));
  return minutes >= 120 ? `Preview expires in ${Math.round(minutes / 60)} hours` : `Preview expires in ${minutes} min`;
}
// Readable money: two decimals for people; the exact decimal strings stay in Details.
export const usd = (value) => {
  const number = Number(value);
  return value == null || value === '' || !Number.isFinite(number) ? null : `$${number.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};
// "Nov 1–30": the end of a period is exclusive, so the last covered day is the day before it.
export function periodLabel(startsAt, endsAt) {
  const start = new Date(startsAt);
  const end = new Date(new Date(endsAt).getTime() - 1);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return null;
  const month = (date) => date.toLocaleString('en-US', { month: 'short', timeZone: 'UTC' });
  return month(start) === month(end) && start.getUTCFullYear() === end.getUTCFullYear()
    ? `${month(start)} ${start.getUTCDate()}–${end.getUTCDate()}`
    : `${month(start)} ${start.getUTCDate()} – ${month(end)} ${end.getUTCDate()}`;
}
export const recipientsOf = (options) => (options?.recipients ?? []).filter((person) => person?.id).map((person) => ({ id: String(person.id), name: person.name || person.email || 'Workspace member', ...(person.email ? { email: person.email } : {}) }));
export const httpStatus = (error) => error?.response?.status ?? error?.statusCode ?? error?.status;
// A finished run whose review record does not exist yet has no human reviews; only a real failure is an error.
export const noReviewsYet = (query) => (query.isError && httpStatus(query.error) === 404 ? { ...query, isError: false, error: null, data: { results: [] } } : query);
// Application test headline. A plain regression run (no recorded failure selected) says
// what passed; only a run that carried a recorded failure speaks about a repair.
export function nativeConclusion({ candidate, reference, passed, total, result, decision }) {
  if (!result) return decision === 'release_ready' ? 'Application checks passed for these cases.' : 'Hold this release.';
  const lead = `v${candidate} passed ${passed} of ${total} cases`;
  if (result.reproduction === 'not_requested') return `${lead}.`;
  const reproduction = result.reproduction === 'reproduced'
    ? `the recorded failure reproduced${reference ? ` on v${reference}` : ''}`
    : `the recorded failure ${result.reproduction === 'not_reproduced' ? 'did not reproduce' : 'was not verified'}`;
  return `${result.repair_verified ? 'Repair verified' : 'Repair not verified'}: ${lead}; ${reproduction}.`;
}
const CHECK_NAMES = { selection: 'skill selection', delivery: 'file delivery', tools: 'business tool', answer: 'answer' };
const GRADED = new Set(['pass', 'fail', 'not_applicable']);
// One line per reviewed case, per context version: "v1 failed business tool". Ungraded work says so.
export function nativeCaseLines(cases = []) {
  const byCase = new Map();
  cases.forEach((cell) => byCase.set(cell.caseId, [...(byCase.get(cell.caseId) || []), cell]));
  const order = { candidate: 0, reference: 1, production: 2 };
  return [...byCase.values()].map((cells) => ({
    caseId: cells[0].caseId,
    title: cells[0].title || 'Reviewed case',
    text: [...cells].sort((a, b) => (order[a.variant] ?? 3) - (order[b.variant] ?? 3) || a.repeatIndex - b.repeatIndex).map((cell) => {
      const failed = Object.keys(CHECK_NAMES).filter((key) => cell[key] === 'fail');
      const ungraded = cell.status !== 'completed' || Object.keys(CHECK_NAMES).some((key) => !GRADED.has(cell[key]));
      const name = `${cell.version ? `v${cell.version}` : cell.variant}${cells.filter((other) => other.variant === cell.variant).length > 1 ? ` (run ${cell.repeatIndex + 1})` : ''}`;
      return `${name} ${failed.length ? `failed ${failed.map((key) => CHECK_NAMES[key]).join(', ')}` : ungraded ? 'not fully graded' : 'passed every check'}`;
    }).join(' · '),
  }));
}
// The same headline for a run carried into a review packet (inspection summary rows).
export function nativeSummaryConclusion(summary) {
  if (!summary?.result) return null;
  const cases = summary.cases || [];
  const ids = [...new Set(cases.map((cell) => cell.caseId))];
  const candidate = cases.filter((cell) => cell.variant === 'candidate');
  const passed = ids.filter((id) => {
    const attempts = candidate.filter((cell) => cell.caseId === id);
    return attempts.length > 0 && attempts.every((cell) => cell.status === 'completed' && Object.keys(CHECK_NAMES).every((key) => cell[key] === 'pass'));
  }).length;
  return nativeConclusion({ candidate: candidate[0]?.version, reference: cases.find((cell) => cell.variant === 'reference')?.version, passed, total: ids.length, result: summary.result });
}
// Release impact in one sentence: "1 connected consumer follows this label and moves with it; 1 is pinned and stays put."
export function impactSentence({ following = 0, pinned = 0, unobserved = 0 }) {
  const plural = (count, one, many) => `${count} ${count === 1 ? one : many}`;
  const parts = [
    following ? `${plural(following, 'connected consumer follows this label and moves', 'connected consumers follow this label and move')} with it` : 'No connected consumer follows this label',
    pinned ? `${plural(pinned, 'is', 'are')} pinned and stays put` : null,
    unobserved ? `${plural(unobserved, 'registered application has', 'registered applications have')} not reported a version yet` : null,
  ].filter(Boolean);
  return `${parts.join('; ')}.`;
}
