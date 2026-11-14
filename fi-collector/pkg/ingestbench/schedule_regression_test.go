package ingestbench

import (
	"bytes"
	"context"
	"encoding/json"
	"math/big"
	"testing"
	"time"
)

func TestBatchScheduleBoundaries(t *testing.T) {
	c := testConfig()
	c.Rate, c.BatchSize, c.Duration = 100, 500, 11250*time.Millisecond
	start := time.Now()
	s := newRunState(c, start, false)
	for _, tc := range []struct {
		at   time.Duration
		want int64
	}{
		{-time.Nanosecond, 0}, {0, 500}, {time.Second, 500},
		{5*time.Second - time.Nanosecond, 500}, {5 * time.Second, 1000}, {5*time.Second + time.Nanosecond, 1000},
		{10*time.Second - time.Nanosecond, 1000}, {10 * time.Second, 1125}, {10*time.Second + time.Nanosecond, 1125},
		{c.Duration, 1125}, {24 * time.Hour, 1125},
	} {
		got := c.scheduledSpans(tc.at)
		result := scheduleResult(c, tc.at, 0, Percentiles{}, 0, 0)
		// Exercise the exact progress-event and serialization path with the
		// same explicit clock instant; writing must not resample wall time.
		var out bytes.Buffer
		event := s.progressLocked(c, "progress", tc.at)
		if err := newTelemetrySink(&out, c.RunID, start).writeAt(event, start.Add(tc.at)); err != nil {
			t.Fatal(err)
		}
		var decoded telemetryEvent
		if err := json.NewDecoder(&out).Decode(&decoded); err != nil {
			t.Fatal(err)
		}
		if got != tc.want || result.ScheduledSpans != tc.want || result.NotStartedSpans != tc.want || *decoded.ScheduledSpans != tc.want || decoded.ElapsedSeconds != tc.at.Seconds() {
			t.Fatalf("at %s schedule=%d final=%+v telemetry=%+v want=%d", tc.at, got, result, decoded, tc.want)
		}
	}
	// A plan smaller than one batch is all due at zero, never a full batch.
	c.Duration = 250 * time.Millisecond
	if got := c.scheduledSpans(0); got != 25 {
		t.Fatalf("short finite plan: %d", got)
	}
}

func TestBatchScheduleRationalAndLargeDueTimes(t *testing.T) {
	c := testConfig()
	c.Rate, c.BatchSize, c.Duration = 3, 1, time.Second
	for _, tc := range []struct {
		at   time.Duration
		want int64
	}{
		{0, 1}, {333333332, 1}, {333333333, 2}, {333333334, 2},
		{666666665, 2}, {666666666, 3}, {666666667, 3}, {time.Second, 3},
	} {
		if got := c.scheduledSpans(tc.at); got != tc.want {
			t.Fatalf("rational boundary at %s: got=%d want=%d", tc.at, got, tc.want)
		}
	}
	// Validate integer nanosecond due times against an overflow-free oracle,
	// including a virtual max-duration/rate plan. This sends no load.
	c.Rate, c.BatchSize, c.Duration = 1000000, 10000, 24*time.Hour
	for _, seq := range []int64{0, 10000, 12345678901, c.PlannedSpans() - 10000} {
		want := new(big.Int).Mul(big.NewInt(seq), big.NewInt(int64(time.Second)))
		want.Quo(want, big.NewInt(c.Rate))
		if got := c.batchDue(seq); int64(got) != want.Int64() {
			t.Fatalf("due(%d)=%s want=%dns", seq, got, want.Int64())
		}
	}
	last := c.batchDue(c.PlannedSpans() - 10000)
	if c.scheduledSpans(last-time.Nanosecond) != c.PlannedSpans()-10000 || c.scheduledSpans(last) != c.PlannedSpans() || c.scheduledSpans(48*time.Hour) != c.PlannedSpans() {
		t.Fatal("large plan boundary or cap is wrong")
	}
}

func TestBatchScheduleMatchesEnumeratedDueBatches(t *testing.T) {
	c := testConfig()
	for rate := int64(1); rate <= 17; rate++ {
		for batch := 1; batch <= 9; batch++ {
			c.Rate, c.BatchSize, c.Duration = rate, batch, 3125*time.Millisecond
			planned := c.PlannedSpans()
			for seq := int64(0); seq < planned; seq += int64(batch) {
				due := time.Duration(seq * int64(time.Second) / rate)
				for _, at := range []time.Duration{due - time.Nanosecond, due, due + time.Nanosecond} {
					var want int64
					for first := int64(0); first < planned; first += int64(batch) {
						if time.Duration(first*int64(time.Second)/rate) <= at {
							want += min(int64(batch), planned-first)
						}
					}
					if got := c.scheduledSpans(at); got != want {
						t.Fatalf("rate=%d batch=%d at=%s got=%d want=%d", rate, batch, at, got, want)
					}
				}
			}
		}
	}
}

func TestScheduleStopsWithProducerWhileRequestsDrain(t *testing.T) {
	c := testConfig()
	c.Rate, c.BatchSize, c.Duration = 100, 500, 11250*time.Millisecond
	start := time.Now()
	s := newRunState(c, start, false)
	s.counters.Attempted, s.counters.Requests = 500, 1
	s.inFlightSpans, s.inFlightRequests = 500, 1
	// The byte budget/cancellation stopped production at t=1s, while the
	// first request is still in flight. Later nominal batches must not be
	// added to telemetry while that request drains.
	s.produceEnd, s.producerStopped = time.Second, true
	var out bytes.Buffer
	sink := newTelemetrySink(&out, c.RunID, start)
	for _, at := range []time.Duration{time.Second, 5 * time.Second, 20 * time.Second} {
		e := s.progressLocked(c, "progress", at)
		if *e.ScheduledSpans != 500 || *e.InFlightSpans != 500 || *e.InFlightRequests != 1 || e.Counters.Acknowledged != 0 {
			t.Fatalf("invalid in-flight event at %s: %+v", at, e)
		}
		if err := sink.writeAt(e, start.Add(at)); err != nil {
			t.Fatal(err)
		}
	}
	s.finish(0, 500, 0, 0, exportOutcome{class: outcomeAcknowledged}, 0, 0, start.Add(20*time.Second))
	final := s.progressLocked(c, "progress-final", 20*time.Second)
	want := scheduleResult(c, s.produceEnd, s.counters.Attempted, Percentiles{}, 0, 0)
	if *final.ScheduledSpans != want.ScheduledSpans || want.NotStartedSpans != 0 || *final.InFlightSpans != 0 || final.Counters.Acknowledged != 500 {
		t.Fatalf("wrong final schedule/accounting: %+v %+v", final, want)
	}
	if err := sink.writeAt(final, start.Add(20*time.Second)); err != nil {
		t.Fatal(err)
	}
	check, err := checkTelemetry(&out, c.RunID)
	if err != nil || check.IdentityHeld != 4 || check.IdentityViolations != 0 {
		t.Fatalf("in-flight and final accounting: %+v err=%v", check, err)
	}
}

func TestEndProductionFreezesScheduleHorizon(t *testing.T) {
	s := newRunState(testConfig(), time.Now().Add(-time.Second), false)
	before := time.Since(s.start)
	end := s.endProduction()
	after := time.Since(s.start)
	if !s.producerStopped || end != s.produceEnd || end < before || end > after {
		t.Fatal("producer stop failed to capture its monotonic schedule horizon")
	}
}

// R5: this uses the actual telemetry loop, with no listener or database.
func TestScheduledTelemetryUsesBatchSchedule(t *testing.T) {
	c := testConfig()
	c.Rate, c.BatchSize, c.Duration = 100, 500, 10*time.Second
	start := time.Now().Add(-time.Second)
	s := newRunState(c, start, false)
	s.counters.Attempted, s.counters.Requests = 500, 1
	s.inFlightSpans, s.inFlightRequests = 500, 1
	var out bytes.Buffer
	ctx, cancel := context.WithCancel(context.Background())
	stop, wait := startTelemetry(ctx, c, clickHouse{}, s, newTelemetrySink(&out, c.RunID, start))
	stop()
	cancel()
	if err := wait(); err != nil {
		t.Fatal(err)
	}
	var event telemetryEvent
	if err := json.NewDecoder(&out).Decode(&event); err != nil {
		t.Fatal(err)
	}
	want := scheduleResult(c, time.Duration(event.ElapsedSeconds*float64(time.Second)), 500, Percentiles{}, 0, 0).ScheduledSpans
	if event.ScheduledSpans == nil {
		t.Fatal("missing schedule")
	}
	if *event.ScheduledSpans != want {
		t.Fatalf("at %fs telemetry scheduled=%d, final schedule=%d, attempted=%d", event.ElapsedSeconds, *event.ScheduledSpans, want, event.Counters.Attempted)
	}
}
