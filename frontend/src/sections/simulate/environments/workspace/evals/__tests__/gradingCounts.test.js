import { describe, it, expect } from "vitest";
import { gradingCountsSentence } from "../gradingCounts";

describe("gradingCountsSentence (the run-level add's 202 counts)", () => {
  it("says what was queued and what was skipped, in plain words", () => {
    expect(
      gradingCountsSentence({
        queued: 13,
        skipped_existing: 2,
        skipped_pending: 1,
        skipped_in_flight: 0,
        completed_calls: 16,
      }),
    ).toBe(
      "13 calls queued for grading, 2 already graded, 1 still being processed — of 16 calls that finished in this run.",
    );
  });

  it("names the in-flight bucket when a second click hits the 10-minute stamp", () => {
    expect(
      gradingCountsSentence({
        queued: 0,
        skipped_existing: 0,
        skipped_pending: 0,
        skipped_in_flight: 12,
        completed_calls: 12,
      }),
    ).toBe(
      "Nothing new to grade, 12 queued a few minutes ago — of 12 calls that finished in this run.",
    );
  });

  it("drops every zero bucket and gets the singular right", () => {
    expect(
      gradingCountsSentence({
        queued: 1,
        skipped_existing: 0,
        skipped_pending: 0,
        skipped_in_flight: 0,
        completed_calls: 1,
      }),
    ).toBe("1 call queued for grading — of 1 call that finished in this run.");
  });

  it("treats a missing body's buckets as zero, but never invents a finished-call count", () => {
    // Every field is undefined here — the four buckets read as "nothing
    // queued, nothing skipped" (genuinely true of an empty body), but
    // `completed_calls` is not a bucket: printing "of 0 calls that finished
    // in this run" would be a false claim that no call finished, when the
    // truth is only that the count wasn't sent.
    expect(gradingCountsSentence()).toBe(
      "Nothing new to grade — how many calls finished in this run isn't known.",
    );
    expect(gradingCountsSentence()).not.toMatch(/of 0 calls/);
  });

  it("says 'of 0 calls' when the run genuinely finished none — a real, known zero, distinct from an absent body", () => {
    // A response for a run with no completed call: `completed_calls` IS
    // known here, and known to be 0 — the one case where "of 0 calls that
    // finished in this run" is the correct, honest sentence, distinct from
    // an absent body where the count isn't known at all.
    expect(
      gradingCountsSentence({
        queued: 0,
        skipped_existing: 0,
        skipped_pending: 0,
        skipped_in_flight: 0,
        completed_calls: 0,
      }),
    ).toBe("Nothing new to grade — of 0 calls that finished in this run.");
  });

  // The four buckets are meant to partition `completed_calls`, so a gap
  // between them and the total means the body disagrees with itself. Name the
  // calls it does not account for — and name no cause, because the body gives
  // none: a call whose grading job failed to queue is already counted inside
  // `queued`.
  it("names the calls the four buckets don't account for, without claiming a cause", () => {
    const sentence = gradingCountsSentence({
      queued: 0,
      skipped_existing: 0,
      skipped_pending: 0,
      skipped_in_flight: 0,
      completed_calls: 16,
    });
    expect(sentence).toBe(
      "Nothing new to grade, 16 calls unaccounted for — of 16 calls that finished in this run.",
    );
    expect(sentence).not.toMatch(/queue/);
  });

  it("gets the gap's singular right and only counts what the four buckets don't already explain", () => {
    expect(
      gradingCountsSentence({
        queued: 3,
        skipped_existing: 2,
        skipped_pending: 1,
        skipped_in_flight: 0,
        completed_calls: 7,
      }),
    ).toBe(
      "3 calls queued for grading, 2 already graded, 1 still being processed, 1 call unaccounted for — of 7 calls that finished in this run.",
    );
  });

  // `count()` reads an absent bucket as 0, so subtracting on a partial body
  // would charge the whole total to a gap that only the missing fields
  // created. A body that carries the total and nothing else prints the total
  // and nothing else.
  it("never reports a gap out of a body that carries the total but no buckets", () => {
    const sentence = gradingCountsSentence({ completed_calls: 16 });
    expect(sentence).toBe("Nothing new to grade — of 16 calls that finished in this run.");
    expect(sentence).not.toMatch(/unaccounted for/);
  });

  it("never reports a gap out of a body missing any single bucket", () => {
    // Three buckets present, one absent — still not enough to subtract with.
    expect(
      gradingCountsSentence({
        queued: 3,
        skipped_existing: 2,
        skipped_pending: 1,
        completed_calls: 16,
      }),
    ).not.toMatch(/unaccounted for/);
  });

  it("never names a gap when the total isn't known, or when the buckets already account for it", () => {
    // Unknown total: no gap can be computed, so none is claimed.
    expect(gradingCountsSentence()).not.toMatch(/unaccounted for/);
    // Buckets sum to exactly the total: no gap to name.
    expect(
      gradingCountsSentence({
        queued: 3,
        skipped_existing: 2,
        skipped_pending: 1,
        skipped_in_flight: 0,
        completed_calls: 6,
      }),
    ).not.toMatch(/unaccounted for/);
  });
});
