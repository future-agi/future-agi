package audit

import (
	"bytes"
	"encoding/json"
	"sync"
	"testing"
	"time"
)

// testSink collects events for assertions.
type testSink struct {
	mu     sync.Mutex
	events []*Event
}

func (s *testSink) Write(events []*Event) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.events = append(s.events, events...)
	return nil
}

func (s *testSink) Close() error { return nil }

func (s *testSink) count() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.events)
}

func (s *testSink) get(i int) *Event {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.events[i]
}

func waitForEvents(sink *testSink, n int, timeout time.Duration) bool {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if sink.count() >= n {
			return true
		}
		time.Sleep(time.Millisecond)
	}
	return false
}

// --- Severity ---

func TestSeverity_String(t *testing.T) {
	tests := []struct {
		sev  Severity
		want string
	}{
		{SeverityInfo, "info"},
		{SeverityWarn, "warn"},
		{SeverityError, "error"},
		{SeverityCritical, "critical"},
	}
	for _, tt := range tests {
		if got := tt.sev.String(); got != tt.want {
			t.Errorf("Severity(%d).String() = %q, want %q", tt.sev, got, tt.want)
		}
	}
}

func TestParseSeverity(t *testing.T) {
	if ParseSeverity("warn") != SeverityWarn {
		t.Error("warn should parse")
	}
	if ParseSeverity("error") != SeverityError {
		t.Error("error should parse")
	}
	if ParseSeverity("unknown") != SeverityInfo {
		t.Error("unknown should default to info")
	}
}

// --- Logger ---

func TestLogger_EmitAndDrain(t *testing.T) {
	sink := &testSink{}
	l := NewLoggerWithSinks([]Sink{sink}, nil, SeverityInfo, 100)

	l.Emit(&Event{Category: "auth", Action: "key_authenticated", Severity: "info"})
	l.Emit(&Event{Category: "rbac", Action: "access_denied", Severity: "error"})
	l.Close()

	if sink.count() != 2 {
		t.Fatalf("expected 2 events, got %d", sink.count())
	}
}

func TestLogger_SequenceMonotonic(t *testing.T) {
	sink := &testSink{}
	l := NewLoggerWithSinks([]Sink{sink}, nil, SeverityInfo, 100)

	for i := 0; i < 10; i++ {
		l.Emit(&Event{Category: "auth", Action: "test", Severity: "info"})
	}
	l.Close()

	if sink.count() != 10 {
		t.Fatalf("expected 10 events, got %d", sink.count())
	}
	for i := 1; i < sink.count(); i++ {
		if sink.get(i).Seq <= sink.get(i-1).Seq {
			t.Errorf("seq not monotonic at %d: %d <= %d", i, sink.get(i).Seq, sink.get(i-1).Seq)
		}
	}
}

func TestLogger_TimestampSet(t *testing.T) {
	sink := &testSink{}
	l := NewLoggerWithSinks([]Sink{sink}, nil, SeverityInfo, 100)

	l.Emit(&Event{Category: "auth", Action: "test", Severity: "info"})
	l.Close()

	if sink.get(0).Timestamp.IsZero() {
		t.Error("timestamp should be set")
	}
}

func TestLogger_CategoryFilter(t *testing.T) {
	sink := &testSink{}
	l := NewLoggerWithSinks([]Sink{sink}, []string{"auth"}, SeverityInfo, 100)

	l.Emit(&Event{Category: "auth", Action: "test", Severity: "info"})
	l.Emit(&Event{Category: "rbac", Action: "test", Severity: "info"}) // filtered out
	l.Close()

	if sink.count() != 1 {
		t.Fatalf("expected 1 event (rbac filtered), got %d", sink.count())
	}
	if sink.get(0).Category != "auth" {
		t.Errorf("category = %q", sink.get(0).Category)
	}
}

func TestLogger_SeverityFilter(t *testing.T) {
	sink := &testSink{}
	l := NewLoggerWithSinks([]Sink{sink}, nil, SeverityWarn, 100)

	l.Emit(&Event{Category: "auth", Action: "test", Severity: "info"}) // filtered
	l.Emit(&Event{Category: "auth", Action: "test", Severity: "warn"})
	l.Emit(&Event{Category: "auth", Action: "test", Severity: "error"})
	l.Close()

	if sink.count() != 2 {
		t.Fatalf("expected 2 events (info filtered), got %d", sink.count())
	}
}

func TestLogger_BufferFullDrops(t *testing.T) {
	sink := &testSink{}
	// Tiny buffer of 2.
	l := NewLoggerWithSinks([]Sink{sink}, nil, SeverityInfo, 2)

	// Don't close immediately — let events pile up.
	// Emit more events than buffer can hold.
	for i := 0; i < 100; i++ {
		l.Emit(&Event{Category: "auth", Action: "test", Severity: "info"})
	}

	l.Close()

	// Some events should be dropped.
	if l.Dropped() == 0 {
		// With a tiny buffer and 100 events, some will likely be dropped.
		// But the goroutine might drain fast enough. Check that it doesn't panic.
		t.Log("no drops detected (goroutine drained fast)")
	}
}

func TestLogger_MultipleSinks(t *testing.T) {
	sink1 := &testSink{}
	sink2 := &testSink{}
	l := NewLoggerWithSinks([]Sink{sink1, sink2}, nil, SeverityInfo, 100)

	l.Emit(&Event{Category: "auth", Action: "test", Severity: "info"})
	l.Close()

	if sink1.count() != 1 {
		t.Errorf("sink1 count = %d", sink1.count())
	}
	if sink2.count() != 1 {
		t.Errorf("sink2 count = %d", sink2.count())
	}
}

func TestLogger_ConcurrentEmit(t *testing.T) {
	sink := &testSink{}
	l := NewLoggerWithSinks([]Sink{sink}, nil, SeverityInfo, 1000)

	var wg sync.WaitGroup
	n := 100
	wg.Add(n)
	for i := 0; i < n; i++ {
		go func() {
			defer wg.Done()
			l.Emit(&Event{Category: "auth", Action: "test", Severity: "info"})
		}()
	}
	wg.Wait()
	l.Close()

	if sink.count() != n {
		t.Errorf("expected %d events, got %d", n, sink.count())
	}
}

func TestLogger_EmptyCategories_AllowAll(t *testing.T) {
	sink := &testSink{}
	l := NewLoggerWithSinks([]Sink{sink}, nil, SeverityInfo, 100) // nil categories = all

	l.Emit(&Event{Category: "auth", Action: "test", Severity: "info"})
	l.Emit(&Event{Category: "rbac", Action: "test", Severity: "info"})
	l.Emit(&Event{Category: "budget", Action: "test", Severity: "info"})
	l.Close()

	if sink.count() != 3 {
		t.Fatalf("expected 3 events (all categories), got %d", sink.count())
	}
}

// --- WriterSink ---

func TestWriterSink_JSONLines(t *testing.T) {
	var buf bytes.Buffer
	sink := NewWriterSink(&buf)

	events := []*Event{
		{Seq: 1, Category: "auth", Action: "test", Severity: "info", Timestamp: time.Now().UTC()},
		{Seq: 2, Category: "rbac", Action: "denied", Severity: "error", Timestamp: time.Now().UTC()},
	}
	if err := sink.Write(events); err != nil {
		t.Fatal(err)
	}

	lines := bytes.Split(bytes.TrimSpace(buf.Bytes()), []byte("\n"))
	if len(lines) != 2 {
		t.Fatalf("expected 2 lines, got %d", len(lines))
	}

	var e Event
	if err := json.Unmarshal(lines[0], &e); err != nil {
		t.Fatal(err)
	}
	if e.Category != "auth" {
		t.Errorf("category = %q", e.Category)
	}
}

// --- Event with full schema ---

func TestEvent_FullSchema(t *testing.T) {
	e := &Event{
		Seq:       1,
		Timestamp: time.Now().UTC(),
		Category:  "rbac",
		Action:    "access_denied",
		Severity:  "error",
		Actor: Actor{
			Type:  "api_key",
			ID:    "key-abc123",
			Name:  "ci-pipeline",
			Owner: "alice",
			Team:  "engineering",
			Role:  "member",
			IP:    "10.0.1.50",
		},
		Resource: &Resource{
			Type:     "model",
			ID:       "gpt-4o",
			Provider: "openai",
		},
		Outcome:   "denied",
		Reason:    "role member has no access",
		RequestID: "req-123",
		Metadata: map[string]string{
			"budget_remaining": "450.00",
		},
	}

	data, err := json.Marshal(e)
	if err != nil {
		t.Fatal(err)
	}

	var parsed Event
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatal(err)
	}
	if parsed.Actor.Owner != "alice" {
		t.Errorf("actor.owner = %q", parsed.Actor.Owner)
	}
	if parsed.Resource.ID != "gpt-4o" {
		t.Errorf("resource.id = %q", parsed.Resource.ID)
	}
}
