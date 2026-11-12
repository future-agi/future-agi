package server

import (
	"context"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/batch"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestBatchEarlyRefusalCharge(t *testing.T) {
	h := &Handlers{maxBodySize: 1 << 20}
	adapter := batch.RunAsChatRequest(func() http.Handler { return http.HandlerFunc(h.ChatCompletion) })
	for _, request := range []*models.ChatCompletionRequest{{}, {Model: "missing-messages"}} {
		_, meta, err := adapter(context.Background(), request)
		if err == nil {
			t.Fatal("expected validation refusal")
		}
		if meta["cost_known"] != "true" || meta["cost"] != "0.000000" || meta["billable"] != "false" {
			t.Errorf("trusted validation refusal lost known zero: %+v", meta)
		}
	}
}
