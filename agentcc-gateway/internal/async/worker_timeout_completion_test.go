package async

import (
	"context"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestWorkerTimeoutRecordsTerminalState(t *testing.T) {
	s := NewStore()
	// The job runs the executor it carries until its timeout ends it.
	s.Put(&Job{
		ID: "timeout-job", Status: StatusQueued,
		Request:  &models.ChatCompletionRequest{Model: "test"},
		Metadata: map[string]string{"timeout_ms": "1"},
		Exec: func(ctx context.Context) (*Execution, error) {
			<-ctx.Done()
			return nil, ctx.Err()
		},
	})
	w := NewWorker(s, WorkerConfig{MaxWorkers: 1})
	w.processJob("timeout-job")
	got := s.Get("timeout-job")
	if got == nil || got.Status != StatusFailed || got.Error == nil || got.CompletedAt == nil {
		t.Fatalf("deadline did not produce terminal failure: %+v", got)
	}
	if !got.ExpiresAt.After(time.Now()) {
		t.Fatalf("deadline did not set result expiry: %v", got.ExpiresAt)
	}
}
