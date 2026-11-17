package batch

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Each item's handler gets the batch request's trace id on its context, and
// each result names the request id the handler reports for the item's run
// (MetaRequestID), on success and on error alike. An item that never runs
// (cancelled) names none.
func TestSubmitWithTraceIDGivesEveryItemTheTraceAndRecordsItsRequestID(t *testing.T) {
	var mu sync.Mutex
	traces := map[string]bool{}
	n := 0
	handler := func(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, map[string]string, error) {
		mu.Lock()
		traces[models.GetTraceID(ctx)] = true
		n++
		id := fmt.Sprintf("run-%s", req.Model)
		mu.Unlock()
		meta := map[string]string{MetaRequestID: id}
		if req.Model == "fails" {
			return nil, meta, errors.New("guardrail refused it")
		}
		return &models.ChatCompletionResponse{Model: req.Model}, meta, nil
	}
	p := NewProcessor(handler)
	defer p.Stop()
	b := p.SubmitWithTraceID([]*models.ChatCompletionRequest{{Model: "a"}, {Model: "b"}, {Model: "fails"}}, 3, "trace-of-the-batch")

	deadline := time.Now().Add(5 * time.Second)
	for b.GetStatus() != "completed" {
		if time.Now().After(deadline) {
			t.Fatalf("batch still %s", b.GetStatus())
		}
		time.Sleep(5 * time.Millisecond)
	}
	if len(traces) != 1 || !traces["trace-of-the-batch"] {
		t.Errorf("the items' handlers got trace ids %v, want only the batch request's", traces)
	}
	want := []string{"run-a", "run-b", "run-fails"}
	for i, r := range b.GetResults() {
		if r.RequestID != want[i] {
			t.Errorf("result %d (%s) names request id %q, want %q", i, r.Status, r.RequestID, want[i])
		}
	}

	// Submit, which has no trace id, gives the handler none.
	traces = map[string]bool{}
	b = p.Submit([]*models.ChatCompletionRequest{{Model: "a"}}, 1)
	for b.GetStatus() != "completed" {
		time.Sleep(5 * time.Millisecond)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(traces) != 1 || !traces[""] {
		t.Errorf("an item of a batch submitted without a trace id got trace ids %v", traces)
	}
}
