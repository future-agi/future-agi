// Plain-language summaries for Skills views. Each derives only from server data; when data is
// missing or partial they say so instead of inferring a result.
import { isActiveSkillJob } from 'src/api/skills/testing';

export const passedAll = (variant) => Boolean(variant?.attempts?.length) && variant.attempts.every((item) => item.answer_verdict === 'pass');

// One sentence that says what the answer check found, before any identifiers.
export function answerConclusion({ status, subject, rows, complete }) {
  if (isActiveSkillJob(status)) return `Answer check running for ${subject}.`;
  if (status !== 'completed') return `Answer check ${String(status || 'stopped').replaceAll('_', ' ')} for ${subject}. No result is inferred.`;
  if (!complete || !rows.length) return `Answer check finished for ${subject}.`;
  const passed = rows.filter((row) => passedAll(row.variants.find((variant) => variant.variant === 'candidate'))).length;
  const baseline = rows.every((row) => passedAll(row.variants.find((variant) => variant.variant !== 'candidate')));
  const lead = passed === rows.length ? `Answer check passed for ${subject} on ${passed} of ${rows.length} case${rows.length === 1 ? '' : 's'}` : `Answer check failed for ${subject} on ${rows.length - passed} of ${rows.length} case${rows.length === 1 ? '' : 's'}`;
  return `${lead}${baseline && rows.some((row) => row.variants.length > 1) ? ' (also passes without the context)' : ''}.`;
}
export const mountName = (mount) => String(mount || "").replace(/^skills\//, "").replace(/\/$/, "");
// "Publishing creates billing-agent v2: refund-policy v2, no instruction changes".
export function publishSentence(name, review) {
  const next = (review.expectedHeadVersion ?? 0) + 1;
  const paths = (review.changes || []).map((file) => file.path);
  const instructions = paths.filter((path) => !path.startsWith("skills/") && !path.startsWith(".futureagi/")).length;
  const skills = [...new Set(paths.filter((path) => path.startsWith("skills/")).map((path) => path.split("/")[1]))];
  const locked = new Map((review.acceptedLock?.links || []).map((link) => [mountName(link.mount), link.version]));
  const parts = [
    ...skills.map((skill) => (locked.has(skill) ? `${skill} v${locked.get(skill)}` : `${skill} changes`)),
    instructions ? `${instructions} instruction file${instructions === 1 ? "" : "s"} change${instructions === 1 ? "s" : ""}` : "no instruction changes",
  ];
  return `Publishing creates ${name || "this context"} v${next}: ${parts.join(", ")}`;
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
