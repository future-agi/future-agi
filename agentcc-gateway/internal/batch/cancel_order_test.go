package batch

import (
	"context"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Force processBatch to finish inside the cancellation signal, before Cancel
// returns. There is no timing lottery: channels establish every ordering edge.
// The old Cancel marks state only after this signal, so completion wins.
func TestCancelMarksStateBeforeWorkerCompletion(t *testing.T) {
	ctx, signal := context.WithCancel(context.Background())
	defer signal()
	started, done := make(chan struct{}), make(chan struct{})
	p := NewProcessor(func(ctx context.Context, _ *models.ChatCompletionRequest) (*models.ChatCompletionResponse, map[string]string, error) {
		close(started)
		<-ctx.Done()
		return nil, nil, ctx.Err()
	})
	defer p.Stop()
	b := &Batch{ID: "ordered-cancel", Status: "processing", Total: 1, MaxConc: 1,
		Results: []*Result{{Index: 0, Status: "pending"}}}
	b.cancel = func() {
		signal()
		select {
		case <-done:
		case <-time.After(5 * time.Second):
			t.Fatal("workers could not finish during Cancel; cancellation must signal outside b.mu")
		}
	}
	p.batches.Store(b.ID, b)
	go func() {
		p.processBatch(ctx, b, []*models.ChatCompletionRequest{{Model: "synthetic"}})
		close(done)
	}()
	select {
	case <-started:
	case <-time.After(5 * time.Second):
		t.Fatal("worker did not start")
	}
	got, ok := p.Cancel(b.ID)
	if !ok || got != b || b.GetStatus() != "cancelled" || b.GetCompletedAt() == nil {
		t.Fatalf("Cancel returned found=%v status=%q completed_at=%v; want cancelled after worker completion", ok, b.GetStatus(), b.GetCompletedAt())
	}
	if _, ok := p.Cancel(b.ID); !ok || b.GetStatus() != "cancelled" {
		t.Fatal("repeated cancellation changed terminal state")
	}
}

func TestCancelDoesNotRewriteCompletedBatch(t *testing.T) {
	p := NewProcessor(nil)
	defer p.Stop()
	b := &Batch{ID: "already-completed", Status: "completed", cancel: func() {}}
	p.batches.Store(b.ID, b)
	if got, ok := p.Cancel(b.ID); !ok || got != b || got.GetStatus() != "completed" {
		t.Fatal("cancellation rewrote a batch completed before Cancel")
	}
}
