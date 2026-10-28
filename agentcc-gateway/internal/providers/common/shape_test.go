package common

import (
	"context"
	"strings"
	"sync"
	"testing"
)

func TestShapeReporter_NilIsNoOp(t *testing.T) {
	var r *ShapeReporter
	r.Report(ShapeUnknownBlock, "anything") // must not panic
}

func TestShapeReporter_RecordsOncePerDistinctShape(t *testing.T) {
	ctx, rec := WithShapeRecorder(context.Background())
	r := NewShapeReporter(ctx, "anthropic", "my-anthropic")

	// A stream repeats an unknown delta on every event; it counts once.
	for i := 0; i < 5; i++ {
		r.Report(ShapeUnknownDelta, "thinking_delta")
	}
	r.Report(ShapeUnknownDelta, "signature_delta")
	r.Report(ShapeUnknownBlock, "thinking_delta") // same value, different kind

	got := rec.Shapes()
	want := []UnknownShape{
		{Provider: "anthropic", Kind: ShapeUnknownDelta, Value: "thinking_delta"},
		{Provider: "anthropic", Kind: ShapeUnknownDelta, Value: "signature_delta"},
		{Provider: "anthropic", Kind: ShapeUnknownBlock, Value: "thinking_delta"},
	}
	if len(got) != len(want) {
		t.Fatalf("got %d reports %+v, want %d", len(got), got, len(want))
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("report %d = %+v, want %+v", i, got[i], want[i])
		}
	}
}

func TestShapeReporter_SeparateResponsesReportSeparately(t *testing.T) {
	ctx, rec := WithShapeRecorder(context.Background())
	NewShapeReporter(ctx, "anthropic", "a").Report(ShapeUnknownBlock, "x")
	NewShapeReporter(ctx, "anthropic", "a").Report(ShapeUnknownBlock, "x")
	if n := len(rec.Shapes()); n != 2 {
		t.Fatalf("got %d reports, want 2 (one per response)", n)
	}
}

func TestShapeReporter_NoRecorderOnContext(t *testing.T) {
	r := NewShapeReporter(context.Background(), "anthropic", "a")
	r.Report(ShapeUnknownEvent, "new_event") // logs only; must not panic
}

func TestShapeReporter_TruncatesValue(t *testing.T) {
	ctx, rec := WithShapeRecorder(context.Background())
	NewShapeReporter(ctx, "anthropic", "a").Report(ShapeMalformed, strings.Repeat("x", 1000))
	if got := len(rec.Shapes()[0].Value); got != maxShapeValueLen {
		t.Fatalf("value length = %d, want %d", got, maxShapeValueLen)
	}
}

func TestSetUnknownShapeHook(t *testing.T) {
	var mu sync.Mutex
	var seen []UnknownShape
	SetUnknownShapeHook(func(s UnknownShape) {
		mu.Lock()
		seen = append(seen, s)
		mu.Unlock()
	})
	t.Cleanup(func() { SetUnknownShapeHook(nil) })

	r := NewShapeReporter(context.Background(), "gemini", "g")
	r.Report(ShapeUnmappedFinishReason, "NEW_REASON")
	r.Report(ShapeUnmappedFinishReason, "NEW_REASON")

	mu.Lock()
	defer mu.Unlock()
	if len(seen) != 1 || seen[0].Provider != "gemini" || seen[0].Kind != ShapeUnmappedFinishReason {
		t.Fatalf("hook saw %+v, want one gemini unmapped_finish_reason report", seen)
	}
}

// Recorders on different contexts must not see each other's reports: the
// contract tests run in parallel and each asserts its own.
func TestShapeRecorder_IsolatedPerContext(t *testing.T) {
	var wg sync.WaitGroup
	recs := make([]*ShapeRecorder, 8)
	for i := range recs {
		ctx, rec := WithShapeRecorder(context.Background())
		recs[i] = rec
		wg.Add(1)
		go func() {
			defer wg.Done()
			NewShapeReporter(ctx, "anthropic", "a").Report(ShapeUnknownBlock, "x")
		}()
	}
	wg.Wait()
	for i, rec := range recs {
		if n := len(rec.Shapes()); n != 1 {
			t.Errorf("recorder %d has %d reports, want 1", i, n)
		}
	}
}
