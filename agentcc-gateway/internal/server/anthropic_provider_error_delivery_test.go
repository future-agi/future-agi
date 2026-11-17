package server

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/translation"
)

// Translation cannot start until the handler receives the provider error.
// This pins the losing interleaving without sleeps or scheduler assumptions:
// the stream-start gate has real output, but no translated event is ready yet.
type errorHandshakeTranslator struct {
	translation.InboundTranslator
	ready <-chan struct{}
}

func (t errorHandshakeTranslator) StreamEventsFromCanonical(ctx context.Context, chunks <-chan models.StreamChunk) (<-chan []byte, <-chan error) {
	events := make(chan []byte)
	errs := make(chan error, 1)
	go func() {
		defer close(errs)
		defer close(events)
		select {
		case <-t.ready:
		case <-ctx.Done():
			return
		}
		translated, failures := t.InboundTranslator.StreamEventsFromCanonical(ctx, chunks)
		for event := range translated {
			select {
			case events <- event:
			case <-ctx.Done():
				return
			}
		}
		for err := range failures {
			errs <- err
		}
	}()
	return events, errs
}

type deliveryErrorProvider struct {
	cacheUsageProvider
	received chan struct{}
	queued   bool
	failure  error
}

func (p deliveryErrorProvider) StreamChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	chunks := make(chan models.StreamChunk, 104)
	text := "delivered before terminal error"
	output := models.StreamChunk{ID: "msg_delivery", Model: req.Model, Choices: []models.StreamChoice{{Delta: models.Delta{Content: &text}}}}
	chunks <- models.StreamChunk{ID: output.ID, Model: req.Model}
	chunks <- output
	errBuffer := 0
	if p.queued {
		errBuffer = 1
	}
	errs := make(chan error, errBuffer)
	produce := func() {
		defer close(chunks)
		select {
		case errs <- p.failure:
		case <-ctx.Done():
			return
		}
		close(p.received)
		// More than a translator's event buffer, then a successful-looking
		// finish and late billing evidence. None may be stolen by a drainer.
		for range 100 {
			chunks <- output
		}
		stop := "stop"
		chunks <- models.StreamChunk{Choices: []models.StreamChoice{{FinishReason: &stop}}}
		cost := upstreamCost
		chunks <- models.StreamChunk{Model: req.Model, Usage: &models.Usage{PromptTokens: 8, CompletionTokens: 278, Cost: &cost}}
		// Deliberately leave the auxiliary error channel open after chunks
		// close; completion must not depend on a second terminal signal.
	}
	if p.queued {
		produce()
	} else {
		go produce()
	}
	return chunks, errs
}

func assertAnthropicOutputBeforeError(t *testing.T, body, text, errorType string, count int) {
	t.Helper()
	if got := strings.Count(body, `"text":"`+text+`"`); got != count {
		t.Errorf("translated output count=%d, want %d; body=%.600s", got, count, body)
	}
	if !strings.HasPrefix(body, "event: message_start\n") {
		t.Errorf("missing message_start before output: %.600s", body)
	}
	frames := strings.Split(strings.TrimSuffix(body, "\n\n"), "\n\n")
	last := frames[len(frames)-1]
	if strings.Count(body, "event: error\n") != 1 || !strings.HasPrefix(last, "event: error\n") || !strings.Contains(last, `"type":"`+errorType+`"`) {
		t.Errorf("want one terminal Anthropic %s error, last=%q", errorType, last)
	}
	if strings.Contains(body, "event: message_stop\n") || strings.Contains(body, "event: message_delta\n") {
		t.Error("failed stream advertised successful completion")
	}
}

func TestAnthropicProviderErrorWaitsForTranslatedOutput(t *testing.T) {
	translator, ok := translation.InboundFor("anthropic")
	if !ok {
		t.Fatal("anthropic translator not registered")
	}
	// Canonical Messages now uses the shared chat attempt executor, which
	// reads the node router even when this fixture configures no retries.
	registry, err := providers.NewRegistry(config.DefaultConfig())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = registry.Close() })
	for _, queued := range []bool{false, true} {
		name := "error-before-translation"
		if queued {
			name = "queued-error-and-EOF"
		}
		t.Run(name, func(t *testing.T) {
			for _, failure := range []struct {
				name, errorType string
				err             error
			}{
				{"overloaded", "overloaded_error", &models.APIError{Status: http.StatusServiceUnavailable, Type: models.ErrTypeUpstream, Message: "fixture overloaded"}},
				{"untyped", "api_error", errors.New("fixture failed")},
			} {
				t.Run(failure.name, func(t *testing.T) {
					ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
					defer cancel()
					rc := models.AcquireRequestContext()
					defer rc.Release()
					rc.IsStream = true
					rc.Model = "fixture"
					rc.Provider = "fixture"
					provider := deliveryErrorProvider{received: make(chan struct{}), queued: queued, failure: failure.err}
					wrapped := errorHandshakeTranslator{InboundTranslator: translator, ready: provider.received}
					h := &Handlers{engine: pipeline.NewEngine(), registry: registry}
					w := httptest.NewRecorder()
					h.handleAnthropicStreamViaCanonical(ctx, w, rc, provider, wrapped, &models.ChatCompletionRequest{Model: "fixture", Stream: true}, nil)
					if ctx.Err() != nil || w.Code != http.StatusOK {
						t.Fatalf("stream did not complete promptly: status=%d err=%v", w.Code, ctx.Err())
					}
					assertAnthropicOutputBeforeError(t, w.Body.String(), "delivered before terminal error", failure.errorType, 101)
					if rc.Response == nil || rc.Response.Usage == nil || rc.Response.Usage.Cost == nil || *rc.Response.Usage.Cost != upstreamCost || rc.Response.Usage.CompletionTokens != 278 {
						t.Fatalf("lost late provider evidence: %+v", rc.Response)
					}
				})
			}
		})
	}
}

// The actual HTTP route uses the same error handshake and proves the wire
// outcome together with the ledger, not just a unit-level usage accumulator.
func TestManagedMessagesProviderErrorDeliversOutputAndSettles(t *testing.T) {
	g := recheck3Gateway(t, "")
	real, ok := g.srv.handlers.registry.GetProvider("openrouter")
	if !ok {
		t.Fatal("no managed provider")
	}
	p := &messagesTerminalBackpressureStream{Provider: real, terminalSeen: make(chan struct{})}
	g.srv.handlers.registry.RegisterProvider("openrouter", p, nil)
	status, body, complete := sendManagedWithin(t, g, "/v1/messages", "acme/token", 5*time.Second)
	if !complete || status != http.StatusOK {
		t.Fatalf("stream did not finish: complete=%t status=%d body=%.600s", complete, status, body)
	}
	assertAnthropicOutputBeforeError(t, body, managedStreamText, "api_error", 101)
	spent, held := settledAccount(t, g)
	recs := g.outbox("org-m")
	if p.calls.Load() != 1 || spent != 7350 || held != 0 || len(recs) != 1 {
		t.Fatalf("want one provider-cost settlement: calls=%d spent=%d held=%d records=%v", p.calls.Load(), spent, held, recs)
	}
	if recs[0]["kind"] != "spend" || recs[0]["cost_source"] != "provider_reported" || recs[0]["usage_estimated"] == "1" {
		t.Fatalf("provider evidence replaced with estimate: %v", recs)
	}
}
