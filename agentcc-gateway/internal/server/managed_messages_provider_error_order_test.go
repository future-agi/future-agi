package server

import (
	"context"
	"net/http"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// An unbuffered error send queues the tail only after the handler receives
// the provider error. That fixes the event order without sleeps or log hooks:
// initial output admits the stream, the provider-error arm wins, then more than
// a translator buffer of output and its final bill arrive. The error arm must
// not wait on usage while leaving the translator's events unread.
type messagesTerminalBackpressureStream struct {
	providers.Provider
	calls        atomic.Int32
	terminalSeen chan struct{}
}

func (p *messagesTerminalBackpressureStream) StreamChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	p.calls.Add(1)
	models.MarkStreamAccepted(ctx)
	chunks := make(chan models.StreamChunk, 128)
	text := managedStreamText
	chunk := models.StreamChunk{ID: "error_tail", Model: req.Model, Choices: []models.StreamChoice{{Delta: models.Delta{Content: &text}}}}
	chunks <- chunk
	errs := make(chan error)
	go func() {
		defer close(errs)
		defer close(chunks)
		select {
		case errs <- models.ErrUpstreamProvider(http.StatusBadGateway, "fixture failure"):
		case <-ctx.Done():
			return
		}
		// The tail fits entirely in the adapter's buffer, so only the
		// downstream translator can apply backpressure to the usage tee.
		for range 100 {
			chunks <- chunk
		}
		cost := upstreamCost
		usage := models.MeasuredUsage(8, 278)
		usage.Cost = &cost
		chunks <- models.StreamChunk{Model: req.Model, Usage: usage}
		close(p.terminalSeen)
	}()
	return chunks, errs
}

func TestManagedMessagesProviderErrorDrainsBackpressure(t *testing.T) {
	g := recheck3Gateway(t, "")
	real, ok := g.srv.handlers.registry.GetProvider("openrouter")
	if !ok {
		t.Fatal("no managed provider")
	}
	p := &messagesTerminalBackpressureStream{Provider: real, terminalSeen: make(chan struct{})}
	g.srv.handlers.registry.RegisterProvider("openrouter", p, nil)

	status, body, complete := sendManagedWithin(t, g, "/v1/messages", "acme/token", 5*time.Second)
	select {
	case <-p.terminalSeen:
		t.Log("event order: admitted first output -> selected provider error -> queued 100 output chunks and final reported cost -> closed chunks")
	default:
		t.Fatal("provider error was not selected")
	}
	spent, held := settledAccount(t, g)
	recs := g.outbox("org-m")
	t.Logf("provider-error complete=%t status=%d calls=%d spent=%d held=%d records=%v", complete, status, p.calls.Load(), spent, held, recs)
	if !complete || status != http.StatusOK || p.calls.Load() != 1 || spent != 7350 || held != 0 {
		t.Fatalf("provider-error complete=%t status=%d calls=%d spent=%d held=%d; want prompt completion, one dispatch, spent=7350 held=0; body=%.400s", complete, status, p.calls.Load(), spent, held, body)
	}
	if len(recs) != 1 || recs[0]["kind"] != "spend" || recs[0]["cost_source"] != "provider_reported" || recs[0]["usage_estimated"] == "1" {
		t.Fatalf("want one settlement from the final reported cost: records=%v", recs)
	}
}
