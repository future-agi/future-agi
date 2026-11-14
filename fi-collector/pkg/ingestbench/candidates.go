package ingestbench

import "time"

const ackCandidateBuckets = 64

type ackCandidate struct {
	candidate
	at time.Duration // monotonic offset from load start, not a wall-clock bucket
}

// ackCandidates retains one EARLIEST acknowledgement per time bucket. The
// bucket width covers the configured minimum age in at most 63 buckets, so
// even at arbitrarily high request rates a sample survives until eligible.
// Keeping the earliest (not latest) in each bucket also makes the first sample
// eligible exactly at minAge, without waiting for a bucket boundary.
//
// A colliding bucket is at least 64 buckets newer: its displaced sample has
// already matured. Keep the newest displaced sample separately so an idle /
// resume cycle never evicts the last eligible candidate. Memory is always 65
// samples, independent of request rate, run length and polling frequency.
// Selection is the newest eligible retained sample, not the exact newest ack.
// With continuous ingestion its sampling granularity is ceil(minAge / 63).
// All operations run under runState.mu.
type ackCandidates struct {
	width    time.Duration
	buckets  [ackCandidateBuckets]ackCandidate
	previous ackCandidate
}

func newAckCandidates(minAge time.Duration) ackCandidates {
	return ackCandidates{width: max(1, (minAge+ackCandidateBuckets-2)/(ackCandidateBuckets-1))}
}

func (h *ackCandidates) add(seq int64, at time.Duration) {
	if at < 0 {
		return
	}
	width := max(1, h.width)
	bucket := at / width
	slot := &h.buckets[bucket%ackCandidateBuckets]
	a := ackCandidate{candidate: candidate{seq: seq, ok: true}, at: at}
	if !slot.ok {
		*slot = a
		return
	}
	oldBucket := slot.at / width
	if bucket == oldBucket {
		// finish calls may acquire the lock out of completion-time order.
		if at < slot.at {
			*slot = a
		}
		return
	}
	if bucket > oldBucket {
		a, *slot = *slot, a
	}
	// a is the older sample, including an out-of-order completion that maps
	// to an already newer slot. Never replace the slot with an older bucket.
	if !h.previous.ok || a.at > h.previous.at {
		h.previous = a
	}
}

func (h *ackCandidates) selectAt(minAge, now time.Duration) candidate {
	if now < 0 || minAge > now {
		return candidate{}
	}
	cutoff := now - max(0, minAge)
	best := ackCandidate{}
	consider := func(a ackCandidate) {
		if a.ok && a.at <= cutoff && (!best.ok || a.at > best.at) {
			best = a
		}
	}
	consider(h.previous)
	for _, a := range h.buckets {
		consider(a)
	}
	return best.candidate
}
