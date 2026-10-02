package server

import (
	"context"
	"testing"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

func TestCountDistinctTracesEmpty(t *testing.T) {
	traces := ptrace.NewTraces()
	if n := countDistinctTraces(traces); n != 0 {
		t.Errorf("empty traces: got %d, want 0", n)
	}
}

func TestCountDistinctTracesSingle(t *testing.T) {
	traces := ptrace.NewTraces()
	rs := traces.ResourceSpans().AppendEmpty()
	ss := rs.ScopeSpans().AppendEmpty()
	span := ss.Spans().AppendEmpty()
	span.SetTraceID(pcommon.TraceID([16]byte{1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16}))

	if n := countDistinctTraces(traces); n != 1 {
		t.Errorf("single span: got %d, want 1", n)
	}
}

func TestCountDistinctTracesMultipleSpansSameTrace(t *testing.T) {
	traces := ptrace.NewTraces()
	rs := traces.ResourceSpans().AppendEmpty()
	ss := rs.ScopeSpans().AppendEmpty()
	tid := pcommon.TraceID([16]byte{1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16})

	for i := 0; i < 5; i++ {
		span := ss.Spans().AppendEmpty()
		span.SetTraceID(tid)
		span.SetSpanID(pcommon.SpanID([8]byte{byte(i), 0, 0, 0, 0, 0, 0, 0}))
	}

	if n := countDistinctTraces(traces); n != 1 {
		t.Errorf("5 spans same trace: got %d, want 1", n)
	}
}

func TestCountDistinctTracesMultipleTraces(t *testing.T) {
	traces := ptrace.NewTraces()

	for i := 0; i < 3; i++ {
		rs := traces.ResourceSpans().AppendEmpty()
		ss := rs.ScopeSpans().AppendEmpty()
		span := ss.Spans().AppendEmpty()
		tid := [16]byte{}
		tid[0] = byte(i + 1)
		span.SetTraceID(pcommon.TraceID(tid))
	}

	if n := countDistinctTraces(traces); n != 3 {
		t.Errorf("3 distinct traces: got %d, want 3", n)
	}
}

func TestCountDistinctTracesDuplicatesAcrossResourceSpans(t *testing.T) {
	traces := ptrace.NewTraces()
	tid := pcommon.TraceID([16]byte{0xAA, 0xBB})

	// Same trace ID in two different ResourceSpans
	for i := 0; i < 2; i++ {
		rs := traces.ResourceSpans().AppendEmpty()
		ss := rs.ScopeSpans().AppendEmpty()
		span := ss.Spans().AppendEmpty()
		span.SetTraceID(tid)
		span.SetSpanID(pcommon.SpanID([8]byte{byte(i)}))
	}

	if n := countDistinctTraces(traces); n != 1 {
		t.Errorf("same trace across resource spans: got %d, want 1", n)
	}
}

func TestCheckUsageNilMetering(t *testing.T) {
	s := &Server{metering: nil}
	check, ok := s.checkUsage(context.Background())
	if !ok || !check.Allowed {
		t.Fatal("nil metering must return allowed=true")
	}
}

func TestCheckUsageNoAuthContext(t *testing.T) {
	s := &Server{metering: &auth.Metering{}}
	check, ok := s.checkUsage(context.Background())
	if !ok || !check.Allowed {
		t.Fatal("no auth context must return allowed=true")
	}
}

func TestEmitUsageNilUsage(t *testing.T) {
	s := &Server{usage: nil}
	// Must not panic
	s.emitUsage(context.Background(), ptrace.NewTraces(), 1024)
}

func TestEmitUsageNoAuthContext(t *testing.T) {
	s := &Server{usage: &auth.UsageEmitter{}}
	// Must not panic — no auth result in context
	s.emitUsage(context.Background(), ptrace.NewTraces(), 1024)
}

func TestCountDistinctTracesMultipleScopeSpans(t *testing.T) {
	traces := ptrace.NewTraces()
	rs := traces.ResourceSpans().AppendEmpty()

	tid1 := pcommon.TraceID([16]byte{1})
	tid2 := pcommon.TraceID([16]byte{2})

	ss1 := rs.ScopeSpans().AppendEmpty()
	ss1.Spans().AppendEmpty().SetTraceID(tid1)

	ss2 := rs.ScopeSpans().AppendEmpty()
	ss2.Spans().AppendEmpty().SetTraceID(tid2)

	ss3 := rs.ScopeSpans().AppendEmpty()
	ss3.Spans().AppendEmpty().SetTraceID(tid1) // duplicate

	if n := countDistinctTraces(traces); n != 2 {
		t.Errorf("2 traces across 3 scope spans: got %d, want 2", n)
	}
}

func TestDistinctTraceIDsReturnsSingleID(t *testing.T) {
	traces := ptrace.NewTraces()
	rs := traces.ResourceSpans().AppendEmpty()
	ss := rs.ScopeSpans().AppendEmpty()
	tid := [16]byte{1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16}
	// two spans, same trace -> one distinct id (the billing dedup key for a call)
	ss.Spans().AppendEmpty().SetTraceID(pcommon.TraceID(tid))
	ss.Spans().AppendEmpty().SetTraceID(pcommon.TraceID(tid))

	ids := distinctTraceIDs(traces)
	if len(ids) != 1 {
		t.Fatalf("got %d distinct ids, want 1", len(ids))
	}
	if ids[0] != tid {
		t.Errorf("distinct id = %x, want %x", ids[0], tid)
	}
}

func TestDistinctTraceIDsMultiple(t *testing.T) {
	traces := ptrace.NewTraces()
	for i := 0; i < 3; i++ {
		rs := traces.ResourceSpans().AppendEmpty()
		ss := rs.ScopeSpans().AppendEmpty()
		tid := [16]byte{}
		tid[0] = byte(i + 1)
		ss.Spans().AppendEmpty().SetTraceID(pcommon.TraceID(tid))
	}
	if got := len(distinctTraceIDs(traces)); got != 3 {
		t.Errorf("got %d distinct ids, want 3", got)
	}
}
