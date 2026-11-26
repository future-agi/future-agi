package ingestbench

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"testing"
	"time"
)

type failingWriter struct{}

func (failingWriter) Write([]byte) (int, error) { return 0, errors.New("fixture disk full") }

func TestTelemetryIsVersionedAndCarriesFinalCounts(t *testing.T) {
	var out bytes.Buffer
	ctx, cancel := context.WithCancel(context.Background())
	s := &runState{counters: Counters{Generated: 12, Attempted: 12, Acknowledged: 8, PartialRejected: 1, WholeRequestRejected: 1, RejectedRetryable: 1, Unacknowledged: 1}, seen: 3, inFlightSpans: 2, inFlightRequests: 1}
	stop, wait := startTelemetry(ctx, testConfig(), clickHouse{}, s, newTelemetrySink(&out, testConfig().RunID, time.Now()))
	stop()
	cancel()
	if err := wait(); err != nil {
		t.Fatal(err)
	}
	d := json.NewDecoder(&out)
	foundFinal := false
	for {
		var event telemetryEvent
		err := d.Decode(&event)
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		if event.SchemaVersion != SchemaVersion || event.RunID != testConfig().RunID || event.ElapsedSeconds < 0 {
			t.Fatal("unversioned/invalid telemetry")
		}
		if event.Kind == "progress-final" {
			foundFinal = true
			if event.Counters.Attempted != 12 || event.Counters.Unacknowledged != 1 || event.Counters.WholeRequestRejected != 1 || event.InFlightSpans == nil || *event.InFlightSpans != 2 || event.InFlightRequests == nil || *event.InFlightRequests != 1 {
				t.Fatal("incorrect final counter snapshot")
			}
			// The scheduled and read-visible stages ride the same 1 s series.
			if event.ScheduledSpans == nil || event.SentinelsSeen == nil || *event.SentinelsSeen != 3 {
				t.Fatal("scheduled or read-visible stage missing from the progress series")
			}
		}
	}
	if !foundFinal {
		t.Fatal("missing final interval")
	}
}

func TestTelemetryReportsArtifactWriteFailure(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	stop, wait := startTelemetry(ctx, testConfig(), clickHouse{}, &runState{}, newTelemetrySink(failingWriter{}, testConfig().RunID, time.Now()))
	stop()
	cancel()
	if err := wait(); err == nil {
		t.Fatal("telemetry write failure hidden")
	}
}

// scriptedExporter answers every request with one outcome; while hold is set it
// blocks until the channel is closed.
type scriptedExporter struct {
	outcome exportOutcome
	hold    chan struct{}
}

func (e scriptedExporter) export(ctx context.Context, _ encodedBatch, _ int) (exportOutcome, int) {
	if e.hold != nil {
		<-e.hold
	}
	return e.outcome, 10
}

func (scriptedExporter) close() {}

// F4: the per-line identity compares the outcome classes with an in-flight
// count tracked on its own (send adds, finish subtracts). It must hold for an
// honest run, with a request in flight, and FAIL when a finished request was
// not counted in its class; the old in-flight (attempted minus the classes)
// absorbed any such error, so every line held.
func TestTelemetryIdentityCatchesAFinishedRequestMissingFromItsClass(t *testing.T) {
	c := testConfig()
	acked := exportOutcome{class: outcomeAcknowledged, statusLabel: "grpc:OK", successStatus: true}
	lines := func(s *runState, kinds ...string) TelemetryCheck {
		t.Helper()
		var buf bytes.Buffer
		sink := newTelemetrySink(&buf, c.RunID, time.Now())
		for _, kind := range kinds {
			if err := sink.write(s.snapshot().event(kind, nil)); err != nil {
				t.Fatal(err)
			}
		}
		check, err := checkTelemetry(&buf, c.RunID)
		if err != nil {
			t.Fatal(err)
		}
		return check
	}
	now := time.Now()
	s := newRunState(c, now, false)
	for seq := int64(0); seq < 12; seq += 4 {
		s.send(context.Background(), scriptedExporter{outcome: acked}, seq, 4, encodedBatch{}, false, now, now)
	}
	if check := lines(s, "progress", "progress-final"); check.IdentityHeld != 2 || check.IdentityViolations != 0 {
		t.Fatalf("honest run: %+v", check)
	}
	// A request in flight: held on a progress line, a violation on the final
	// line (the final line is written after every request finished).
	hold := make(chan struct{})
	done := make(chan struct{})
	go func() {
		s.send(context.Background(), scriptedExporter{outcome: acked, hold: hold}, 12, 4, encodedBatch{}, false, now, now)
		close(done)
	}()
	deadline := time.Now().Add(5 * time.Second)
	for s.snapshot().inFlightRequests != 1 && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if check := lines(s, "progress", "progress-final"); check.IdentityHeld != 1 || check.IdentityViolations != 1 {
		t.Fatalf("one request in flight: %+v", check)
	}
	close(hold)
	<-done
	// The reviewer's mutation, as data: a finished acknowledged request that
	// was not counted in its class. Attempted, requests and in-flight are right.
	s.mu.Lock()
	s.counters.Acknowledged -= 4
	s.counters.Accepted -= 4
	s.counters.RequestOutcomes.Acknowledged--
	s.mu.Unlock()
	if check := lines(s, "progress", "progress-final"); check.IdentityHeld != 0 || check.IdentityViolations != 2 {
		t.Fatalf("an uncounted request went unnoticed: %+v", check)
	}
}
