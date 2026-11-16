// The run-level add's 202 body — {queued, skipped_existing,
// skipped_in_flight, skipped_pending, completed_calls} — in plain words for
// the person who just clicked Add.
//
// Zero buckets are dropped: the ordinary case ("everything eligible was
// queued") should read as one clause, not five zeros. The four buckets
// partition `completed_calls`, so the sentence ends with that total rather
// than making the reader add the clauses up.
//
// An empty/no-body 202 makes every field here `undefined`. Treating
// `completed_calls` the same as the four buckets would print "of 0 calls
// that finished in this run" — a false claim, when the truth is only that
// the body didn't arrive. The four buckets stay 0 when missing (that IS
// "nothing queued, nothing skipped"); the total instead gets its own
// no-number wording rather than a manufactured 0.

const calls = (n) => `${n} ${n === 1 ? "call" : "calls"}`;
const count = (value) => (Number.isFinite(value) ? value : 0);

export function gradingCountsSentence(counts = {}) {
  const queued = count(counts.queued);
  const existing = count(counts.skipped_existing);
  const pending = count(counts.skipped_pending);
  const inFlight = count(counts.skipped_in_flight);
  const completedKnown = Number.isFinite(counts.completed_calls);
  const completed = count(counts.completed_calls);

  const parts = [
    queued > 0 ? `${calls(queued)} queued for grading` : "Nothing new to grade",
  ];
  if (existing > 0) parts.push(`${existing} already graded`);
  if (pending > 0) parts.push(`${pending} still being processed`);
  if (inFlight > 0) parts.push(`${inFlight} queued a few minutes ago`);

  // The four buckets are meant to partition the total, so a gap between them
  // and `completed_calls` means the two sides disagree — say that the calls
  // are unaccounted for, and nothing about why, which this body never states.
  //
  // Subtracting requires every term, not just the total: `count()` reads an
  // absent bucket as 0, so on a partial body the "gap" would be the whole
  // total and the sentence would report a shortfall invented out of the
  // missing fields. Gate the arithmetic on all five numbers being present,
  // the same way the total is gated on its own.
  const bucketsKnown = [
    counts.queued,
    counts.skipped_existing,
    counts.skipped_pending,
    counts.skipped_in_flight,
  ].every(Number.isFinite);
  const accounted = queued + existing + pending + inFlight;
  const shortfall = completedKnown && bucketsKnown ? completed - accounted : 0;
  if (shortfall > 0) {
    parts.push(`${calls(shortfall)} unaccounted for`);
  }

  const total = completedKnown
    ? `of ${calls(completed)} that finished in this run`
    : "how many calls finished in this run isn't known";

  return `${parts.join(", ")} (${total}).`;
}
