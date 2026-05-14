package otel

import (
	"bytes"
	"encoding/json"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestNewSpan(t *testing.T) {
	s := NewSpan("test-op", "test-svc")

	if s.TraceID == "" {
		t.Fatal("TraceID should not be empty")
	}
	if len(s.TraceID) != 32 {
		t.Fatalf("TraceID should be 32 hex chars, got %d", len(s.TraceID))
	}
	if s.SpanID == "" {
		t.Fatal("SpanID should not be empty")
	}
	if len(s.SpanID) != 16 {
		t.Fatalf("SpanID should be 16 hex chars, got %d", len(s.SpanID))
	}
	if s.ParentID != "" {
		t.Fatal("root span should have empty ParentID")
	}
	if s.Name != "test-op" {
		t.Fatalf("Name = %q, want %q", s.Name, "test-op")
	}
	if s.ServiceName != "test-svc" {
		t.Fatalf("ServiceName = %q, want %q", s.ServiceName, "test-svc")
	}
	if s.Status != "OK" {
		t.Fatalf("Status = %q, want OK", s.Status)
	}
	if s.StartTime.IsZero() {
		t.Fatal("StartTime should be set")
	}
	if s.Attributes == nil {
		t.Fatal("Attributes should be initialized")
	}
}

func TestNewChildSpan(t *testing.T) {
	parent := NewSpan("parent-op", "svc")
	child := NewChildSpan("child-op", parent.TraceID, parent.SpanID)

	if child.TraceID != parent.TraceID {
		t.Fatalf("child TraceID = %q, want %q", child.TraceID, parent.TraceID)
	}
	if child.ParentID != parent.SpanID {
		t.Fatalf("child ParentID = %q, want %q", child.ParentID, parent.SpanID)
	}
	if child.SpanID == parent.SpanID {
		t.Fatal("child SpanID should differ from parent")
	}
	if child.Name != "child-op" {
		t.Fatalf("Name = %q, want %q", child.Name, "child-op")
	}
}

func TestSpan_End(t *testing.T) {
	s := NewSpan("op", "svc")
	time.Sleep(5 * time.Millisecond)
	s.End()

	if s.EndTime.IsZero() {
		t.Fatal("EndTime should be set after End()")
	}
	if s.Duration <= 0 {
		t.Fatalf("Duration should be positive, got %v", s.Duration)
	}
	if s.Duration < 5*time.Millisecond {
		t.Fatalf("Duration should be >= 5ms, got %v", s.Duration)
	}
}

func TestSpan_SetAttribute(t *testing.T) {
	s := NewSpan("op", "svc")
	s.SetAttribute("gen_ai.request.model", "gpt-4")
	s.SetAttribute("gen_ai.usage.input_tokens", 100)
	s.SetAttribute("agentcc.is_stream", true)
	s.SetAttribute("agentcc.cost", 0.05)

	if v := s.Attributes["gen_ai.request.model"]; v != "gpt-4" {
		t.Fatalf("model = %v, want gpt-4", v)
	}
	if v := s.Attributes["gen_ai.usage.input_tokens"]; v != 100 {
		t.Fatalf("input_tokens = %v, want 100", v)
	}
	if v := s.Attributes["agentcc.is_stream"]; v != true {
		t.Fatalf("is_stream = %v, want true", v)
	}
	if v := s.Attributes["agentcc.cost"]; v != 0.05 {
		t.Fatalf("cost = %v, want 0.05", v)
	}
}

func TestSpan_SetError(t *testing.T) {
	s := NewSpan("op", "svc")
	s.SetError("connection refused")

	if s.Status != "ERROR" {
		t.Fatalf("Status = %q, want ERROR", s.Status)
	}
	if v := s.Attributes["error.message"]; v != "connection refused" {
		t.Fatalf("error.message = %v, want 'connection refused'", v)
	}
}

func TestSpanStatus_String(t *testing.T) {
	if StatusOK.String() != "OK" {
		t.Fatalf("StatusOK.String() = %q, want OK", StatusOK.String())
	}
	if StatusError.String() != "ERROR" {
		t.Fatalf("StatusError.String() = %q, want ERROR", StatusError.String())
	}
}

func TestGenerateID_Uniqueness(t *testing.T) {
	seen := make(map[string]bool, 1000)
	for i := 0; i < 1000; i++ {
		id := generateID(16)
		if seen[id] {
			t.Fatalf("duplicate ID generated: %s", id)
		}
		seen[id] = true
	}
}

func TestGenerateID_Length(t *testing.T) {
	id8 := generateID(8)
	if len(id8) != 16 {
		t.Fatalf("generateID(8) = %d chars, want 16", len(id8))
	}

	id16 := generateID(16)
	if len(id16) != 32 {
		t.Fatalf("generateID(16) = %d chars, want 32", len(id16))
	}
}

func TestStdoutExporter_Export(t *testing.T) {
	var buf bytes.Buffer
	exp := NewStdoutExporter(&buf)

	s := NewSpan("test-op", "test-svc")
	s.SetAttribute("model", "gpt-4")
	s.End()

	if err := exp.Export([]*Span{s}); err != nil {
		t.Fatalf("Export error: %v", err)
	}

	output := buf.String()
	if output == "" {
		t.Fatal("export should produce output")
	}

	// Verify it's valid JSON.
	var decoded map[string]interface{}
	if err := json.Unmarshal([]byte(strings.TrimSpace(output)), &decoded); err != nil {
		t.Fatalf("output is not valid JSON: %v\noutput: %s", err, output)
	}

	if decoded["trace_id"] != s.TraceID {
		t.Fatalf("trace_id = %v, want %s", decoded["trace_id"], s.TraceID)
	}
	if decoded["name"] != "test-op" {
		t.Fatalf("name = %v, want test-op", decoded["name"])
	}
	if decoded["status"] != "OK" {
		t.Fatalf("status = %v, want OK", decoded["status"])
	}
}

func TestStdoutExporter_MultipleSpans(t *testing.T) {
	var buf bytes.Buffer
	exp := NewStdoutExporter(&buf)

	spans := []*Span{
		NewSpan("op-1", "svc"),
		NewSpan("op-2", "svc"),
		NewSpan("op-3", "svc"),
	}
	for _, s := range spans {
		s.End()
	}

	if err := exp.Export(spans); err != nil {
		t.Fatalf("Export error: %v", err)
	}

	lines := strings.Split(strings.TrimSpace(buf.String()), "\n")
	if len(lines) != 3 {
		t.Fatalf("expected 3 JSON lines, got %d", len(lines))
	}

	for i, line := range lines {
		var decoded map[string]interface{}
		if err := json.Unmarshal([]byte(line), &decoded); err != nil {
			t.Fatalf("line %d is not valid JSON: %v", i, err)
		}
	}
}

func TestStdoutExporter_Shutdown(t *testing.T) {
	exp := NewStdoutExporter(&bytes.Buffer{})
	if err := exp.Shutdown(); err != nil {
		t.Fatalf("Shutdown error: %v", err)
	}
}

func TestNoopExporter(t *testing.T) {
	exp := NoopExporter{}
	if err := exp.Export([]*Span{NewSpan("op", "svc")}); err != nil {
		t.Fatalf("Noop Export error: %v", err)
	}
	if err := exp.Shutdown(); err != nil {
		t.Fatalf("Noop Shutdown error: %v", err)
	}
}

func TestMetrics_Increment(t *testing.T) {
	m := &Metrics{}
	m.RequestCount.Add(10)
	m.ErrorCount.Add(2)
	m.InputTokens.Add(500)
	m.OutputTokens.Add(200)
	m.CacheHits.Add(3)
	m.CacheMisses.Add(7)

	snap := m.Snapshot()
	if snap.RequestCount != 10 {
		t.Fatalf("RequestCount = %d, want 10", snap.RequestCount)
	}
	if snap.ErrorCount != 2 {
		t.Fatalf("ErrorCount = %d, want 2", snap.ErrorCount)
	}
	if snap.InputTokens != 500 {
		t.Fatalf("InputTokens = %d, want 500", snap.InputTokens)
	}
	if snap.OutputTokens != 200 {
		t.Fatalf("OutputTokens = %d, want 200", snap.OutputTokens)
	}
	if snap.CacheHits != 3 {
		t.Fatalf("CacheHits = %d, want 3", snap.CacheHits)
	}
	if snap.CacheMisses != 7 {
		t.Fatalf("CacheMisses = %d, want 7", snap.CacheMisses)
	}
}

func TestMetrics_Concurrent(t *testing.T) {
	m := &Metrics{}
	var wg sync.WaitGroup

	for i := 0; i < 100; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			m.RequestCount.Add(1)
			m.InputTokens.Add(10)
			m.OutputTokens.Add(5)
		}()
	}

	wg.Wait()

	snap := m.Snapshot()
	if snap.RequestCount != 100 {
		t.Fatalf("RequestCount = %d, want 100", snap.RequestCount)
	}
	if snap.InputTokens != 1000 {
		t.Fatalf("InputTokens = %d, want 1000", snap.InputTokens)
	}
	if snap.OutputTokens != 500 {
		t.Fatalf("OutputTokens = %d, want 500", snap.OutputTokens)
	}
}

func TestStdoutExporter_ConcurrentExport(t *testing.T) {
	var buf bytes.Buffer
	exp := NewStdoutExporter(&buf)
	var wg sync.WaitGroup

	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func(n int) {
			defer wg.Done()
			s := NewSpan("concurrent-op", "svc")
			s.End()
			_ = exp.Export([]*Span{s})
		}(i)
	}

	wg.Wait()

	lines := strings.Split(strings.TrimSpace(buf.String()), "\n")
	if len(lines) != 50 {
		t.Fatalf("expected 50 JSON lines, got %d", len(lines))
	}
}
