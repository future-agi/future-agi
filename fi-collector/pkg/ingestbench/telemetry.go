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
type telemetryEvent struct {
	SchemaVersion  string            `json:"schema_version"`
	RunID          string            `json:"run_id"`
	Kind           string            `json:"kind"`
	At             time.Time         `json:"at"`
	ElapsedSeconds float64           `json:"elapsed_seconds"`
	Counters       *Counters         `json:"counters,omitempty"`
	InFlightSpans  *int64            `json:"in_flight_spans,omitempty"`
	Resource       *ResourceSnapshot `json:"resource,omitempty"`
}

// snapshot returns the counters and the attempted spans whose request has not
// finished yet (attempted minus every outcome class).
func (s *runState) snapshot() (Counters, int64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	c := s.counters
	return c, c.Attempted - c.Acknowledged - c.PartialRejected - c.WholeRequestRejected - c.Ambiguous
}

func startTelemetry(ctx context.Context, c Config, ch clickHouse, s *runState, writer io.Writer) (func(), func() error) {
	if writer == nil {
		return func() {}, func() error { return nil }
	}
	progressCtx, stopProgress := context.WithCancel(ctx)
	start := time.Now()
	var mu sync.Mutex
	write := func(event telemetryEvent) error {
		event.SchemaVersion, event.RunID, event.At = SchemaVersion, c.RunID, time.Now().UTC()
		event.ElapsedSeconds = time.Since(start).Seconds()
		mu.Lock()
		defer mu.Unlock()
		if err := json.NewEncoder(writer).Encode(event); err != nil {
			return fmt.Errorf("telemetry artifact write failed")
		}
		return nil
	}
	var wg sync.WaitGroup
	errors := make(chan error, 2)
	wg.Add(2)
	go func() {
		defer wg.Done()
		ticker := time.NewTicker(time.Second)
		defer ticker.Stop()
		for {
			counters, inFlight := s.snapshot()
			if err := write(telemetryEvent{Kind: "progress", Counters: &counters, InFlightSpans: &inFlight}); err != nil {
				errors <- err
				return
			}
			select {
			case <-progressCtx.Done():
				counters, inFlight = s.snapshot()
				if err := write(telemetryEvent{Kind: "progress-final", Counters: &counters, InFlightSpans: &inFlight}); err != nil {
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
					if err := write(telemetryEvent{Kind: "resource", Resource: &snapshot}); err != nil {
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
		return nil
	}
}
