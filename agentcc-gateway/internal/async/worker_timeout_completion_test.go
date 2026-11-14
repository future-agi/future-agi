package async

import (
	"context"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestWorkerTimeoutRecordsTerminalState(t *testing.T) {
	s := NewStore()
	s.Put(&Job{
		ID: "timeout-job", Status: StatusQueued,
		Request:  &models.ChatCompletionRequest{Model: "test"},
		Metadata: map[string]string{"timeout_ms": "1"},
	})
	w := NewWorker(s, func(ctx context.Context, _ *models.ChatCompletionRequest, _ map[string]string) (*models.ChatCompletionResponse, map[string]string, error) {
		<-ctx.Done()
		return nil, nil, ctx.Err()
	}, 1)
	w.processJob("timeout-job")
	got := s.Get("timeout-job")
	if got == nil || got.Status != StatusFailed || got.Error == nil || got.CompletedAt == nil {
		t.Fatalf("deadline did not produce terminal failure: %+v", got)
	}
	if !got.ExpiresAt.After(time.Now()) {
		t.Fatalf("deadline did not set result expiry: %v", got.ExpiresAt)
	}
}
