package ingestbench

import "time"

// batchDue is the open-loop due offset of the batch starting at seq. Divide
// before multiplying so even a 24-hour, million-spans/s plan cannot overflow.
// Integer nanoseconds make exact boundaries agree with the producer's timers;
// using the inverse floating-point rate can count a batch one nanosecond late.
func (c Config) batchDue(seq int64) time.Duration {
	return time.Duration(seq/c.Rate)*time.Second + time.Duration(seq%c.Rate)*time.Second/time.Duration(c.Rate)
}

// scheduledSpans counts whole batches due by elapsed, including the initial
// t=0 batch and the partial last batch. The existing finite plan (PlannedSpans)
// caps the count, even while requests drain beyond the load duration. It is
// independent of generated, attempted, acknowledged and in-flight counts.
func (c Config) scheduledSpans(elapsed time.Duration) int64 {
	planned := c.PlannedSpans()
	if elapsed < 0 || planned == 0 {
		return 0
	}
	batch := int64(c.BatchSize)
	lo, hi := int64(0), (planned-1)/batch+1
	// Find the first batch NOT yet due, using the very same due calculation
	// as dispatch and build-ahead. This takes O(log(number of requests)) and
	// avoids floating-point inverse/rounding or elapsed*rate overflow.
	for lo < hi {
		mid := lo + (hi-lo)/2
		if c.batchDue(mid*batch) <= elapsed {
			lo = mid + 1
		} else {
			hi = mid
		}
	}
	return min(planned, lo*batch)
}

func (s *runState) endProduction() time.Duration {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.produceEnd = time.Since(s.start)
	s.producerStopped = true
	return s.produceEnd
}
