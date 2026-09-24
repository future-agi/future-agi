// §6's 202 body — {queued, skipped_existing, skipped_in_flight,
// skipped_pending, completed_calls} — in plain words for the person who
// just clicked Add.
//
// Zero buckets are dropped: the ordinary case ("everything eligible was
// queued") should read as one clause, not five zeros. The four buckets sum
// to at most `completed_calls`, and any shortfall is calls that failed to
// queue — which is why the sentence ends with the total rather than
// claiming the four add up to it.
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

  // The four buckets can sum to less than `completed_calls` — the gap is
  // calls that failed to queue. Without a clause for it, "Nothing new to
  // grade — of 16 calls that finished" reads as "there was nothing to do"
  // when up to 16 dispatches actually failed. Only shown when the total is
  // known and the buckets fall genuinely short of it.
  const accounted = queued + existing + pending + inFlight;
  const shortfall = completedKnown ? completed - accounted : 0;
  if (shortfall > 0) {
    parts.push(`${calls(shortfall)} couldn't be queued`);
  }

  const total = completedKnown
    ? `of ${calls(completed)} that finished in this run`
    : "how many calls finished in this run isn't known";

  return `${parts.join(", ")} — ${total}.`;
}
