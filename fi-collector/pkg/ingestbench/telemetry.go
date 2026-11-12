package ingestbench

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"sync"
	"time"
)

// Each line is independently versioned. Counter deltas / elapsed-time deltas
// give achieved interval rates without retaining a day of samples in memory.
// Progress lines (1 s) carry the attempted and accepted stages and the read-
// visible sentinels; collector lines carry the backlog and the writer's insert
// counters (the durable stage as the collector sees it).
type telemetryEvent struct {
	SchemaVersion  string    `json:"schema_version"`
	RunID          string    `json:"run_id"`
	Kind           string    `json:"kind"`
	At             time.Time `json:"at"`
	ElapsedSeconds float64   `json:"elapsed_seconds"`
	Counters       *Counters `json:"counters,omitempty"`
	// InFlightSpans/InFlightRequests are counted when a request starts and
	// when it finishes, independently of the outcome classes.
	InFlightSpans    *int64            `json:"in_flight_spans,omitempty"`
	InFlightRequests *int64            `json:"in_flight_requests,omitempty"`
	ScheduledSpans   *int64            `json:"scheduled_spans,omitempty"`
	SentinelsSeen    *int64            `json:"sentinels_seen,omitempty"`
	Resource         *ResourceSnapshot `json:"resource,omitempty"`
	Collector        *CollectorSample  `json:"collector,omitempty"`
	Fault            *FaultEvent       `json:"fault,omitempty"`
}

// telemetrySink serialises telemetry lines; a nil writer discards them.
type telemetrySink struct {
	mu     sync.Mutex
	w      io.Writer
	runID  string
	start  time.Time
	failed bool
}

func newTelemetrySink(w io.Writer, runID string, start time.Time) *telemetrySink {
	return &telemetrySink{w: w, runID: runID, start: start}
}

func (t *telemetrySink) write(event telemetryEvent) error {
	return t.writeAt(event, time.Now())
}

func (t *telemetrySink) writeAt(event telemetryEvent, now time.Time) error {
	if t == nil || t.w == nil {
		return nil
	}
	event.SchemaVersion, event.RunID, event.At = SchemaVersion, t.runID, now.UTC()
	event.ElapsedSeconds = now.Sub(t.start).Seconds()
	t.mu.Lock()
	defer t.mu.Unlock()
	if err := json.NewEncoder(t.w).Encode(event); err != nil {
		t.failed = true
		return fmt.Errorf("telemetry artifact write failed")
	}
	return nil
}

func (t *telemetrySink) err() error {
	if t == nil {
		return nil
	}
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.failed {
		return fmt.Errorf("telemetry artifact write failed")
	}
	return nil
}

// progressSnapshot is one progress line's counts, taken under one lock.
type progressSnapshot struct {
	counters                        Counters
	inFlightSpans, inFlightRequests int64
	seen                            int64
}

// snapshot returns the counters and the requests in flight. In-flight is the
// independently tracked count (incremented by send, decremented by finish), NOT
// attempted minus the outcome classes, so the per-line identity can fail.
func (s *runState) snapshot() progressSnapshot {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.snapshotLocked()
}

func (s *runState) snapshotLocked() progressSnapshot {
	return progressSnapshot{counters: s.counters, inFlightSpans: s.inFlightSpans, inFlightRequests: s.inFlightRequests, seen: s.seen}
}

// progressLocked uses the same schedule as the final result, frozen at the
// producer's end rather than extending it while in-flight requests complete.
func (s *runState) progressLocked(c Config, kind string, elapsed time.Duration) telemetryEvent {
	if s.producerStopped {
		elapsed = min(elapsed, s.produceEnd)
	}
	scheduled := c.scheduledSpans(elapsed)
	return s.snapshotLocked().event(kind, &scheduled)
}

func (s *runState) writeProgress(c Config, sink *telemetrySink, kind string) error {
	s.mu.Lock()
	// Capture the clock with the counters, so a boundary-crossing send cannot
	// appear in a snapshot whose schedule was sampled before that due time.
	now := time.Now()
	event := s.progressLocked(c, kind, now.Sub(sink.start))
	s.mu.Unlock()
	return sink.writeAt(event, now)
}

func (p progressSnapshot) event(kind string, scheduled *int64) telemetryEvent {
	return telemetryEvent{Kind: kind, Counters: &p.counters, InFlightSpans: &p.inFlightSpans, InFlightRequests: &p.inFlightRequests, ScheduledSpans: scheduled, SentinelsSeen: &p.seen}
}

func startTelemetry(ctx context.Context, c Config, ch clickHouse, s *runState, sink *telemetrySink) (func(), func() error) {
	if sink == nil || sink.w == nil {
		return func() {}, func() error { return nil }
	}
	progressCtx, stopProgress := context.WithCancel(ctx)
	var wg sync.WaitGroup
	errors := make(chan error, 2)
	wg.Add(2)
	go func() {
		defer wg.Done()
		ticker := time.NewTicker(time.Second)
		defer ticker.Stop()
		for {
			if err := s.writeProgress(c, sink, "progress"); err != nil {
				errors <- err
				return
			}
			select {
			case <-progressCtx.Done():
				if err := s.writeProgress(c, sink, "progress-final"); err != nil {
					errors <- err
				}
				return
			case <-ticker.C:
			}
		}
	}()
	go func() {
		defer wg.Done()
		ticker := time.NewTicker(10 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				for _, snapshot := range resourceSnapshots(ctx, ch) {
					if err := sink.write(telemetryEvent{Kind: "resource", Resource: &snapshot}); err != nil {
						errors <- err
						return
					}
				}
			}
		}
	}()
	return stopProgress, func() error {
		wg.Wait()
		close(errors)
		for err := range errors {
			if err != nil {
				return err
			}
		}
		return sink.err()
	}
}
