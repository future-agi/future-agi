package server

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// The writer admits the first output before releasing the provider's terminal
// error. For the non-Flusher case, headers acknowledge stream admission instead.
// No sleep or deadline establishes event order; the deadline only bounds a bug.
type messagesTerminalWriter struct {
	recorder *httptest.ResponseRecorder
	mode     string
	output   chan struct{}
	once     sync.Once
}

func (w *messagesTerminalWriter) Header() http.Header { return w.recorder.Header() }
func (w *messagesTerminalWriter) WriteHeader(code int) {
	w.recorder.WriteHeader(code)
	if w.mode == "non-flusher" {
		w.once.Do(func() { close(w.output) })
	}
}
func (w *messagesTerminalWriter) Write(b []byte) (int, error) {
	n, err := w.recorder.Write(b)
	if strings.Contains(string(b), managedStreamText) {
		w.once.Do(func() { close(w.output) })
		if w.mode == "write-error" {
			return 0, errors.New("synthetic client write failure")
		}
	}
	return n, err
}

type flushingMessagesTerminalWriter struct{ *messagesTerminalWriter }

func (w flushingMessagesTerminalWriter) Flush() { w.recorder.Flush() }

type messagesTerminalDrainProvider struct {
	providers.Provider
	mode     string
	output   <-chan struct{}
	done     chan struct{}
	calls    atomic.Int32
	reported atomic.Bool
}

func (p *messagesTerminalDrainProvider) StreamChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	p.calls.Add(1)
	models.MarkStreamAccepted(ctx)
	chunks := make(chan models.StreamChunk)
	errs := make(chan error)
	go func() {
		defer close(p.done)
		defer close(errs)
		defer close(chunks)
		text := managedStreamText
		chunk := models.StreamChunk{ID: "terminal_drain", Model: req.Model, Choices: []models.StreamChoice{{Delta: models.Delta{Content: &text}}}}
		send := func(chunk models.StreamChunk) bool {
			// Once canceled, an adapter cannot be relied on to return its bill.
			if ctx.Err() != nil {
				return false
			}
			select {
			case chunks <- chunk:
				return true
			case <-ctx.Done():
				return false
			}
		}
		if !send(chunk) {
			return
		}
		select {
		case <-p.output:
		case <-ctx.Done():
			return
		}
		if p.mode == "provider-error" {
			// Unbuffered: the handler has selected failStream before the tail.
			select {
			case errs <- models.ErrUpstreamProvider(http.StatusBadGateway, "synthetic failure after output"):
			case <-ctx.Done():
				return
			}
		}
		// More than the translator's 64-event buffer. Without an independent
		// drain these cannot all pass the tee while the handler is finalizing.
		// Canceling to unstick it drops the bill deterministically, not by luck.
		for range 100 {
			if !send(chunk) {
				return
			}
		}
		cost := upstreamCost
		if send(models.StreamChunk{Model: req.Model, Usage: &models.Usage{PromptTokens: 8, CompletionTokens: 278, Cost: &cost}}) {
			p.reported.Store(true)
		}
	}()
	return chunks, errs
}

func TestManagedMessagesTerminalDrainsBeforeCancel(t *testing.T) {
	for _, mode := range []string{"provider-error", "write-error", "non-flusher"} {
		t.Run(mode, func(t *testing.T) {
			g := newManagedTestGateway(t, managedOptIn())
			installBillabilityModels(billabilityModels(t))(t, g)
			g.ledger.SetMicros("org-m", 1_000_000)
			real, ok := g.srv.handlers.registry.GetProvider("openrouter")
			if !ok {
				t.Fatal("no managed provider")
			}
			output := make(chan struct{})
			p := &messagesTerminalDrainProvider{Provider: real, mode: mode, output: output, done: make(chan struct{})}
			g.srv.handlers.registry.RegisterProvider("openrouter", p, nil)
			writer := &messagesTerminalWriter{recorder: httptest.NewRecorder(), mode: mode, output: output}
			var w http.ResponseWriter = writer
			if mode != "non-flusher" {
				w = flushingMessagesTerminalWriter{writer}
			}
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			req := httptest.NewRequest(http.MethodPost, "/v1/messages", strings.NewReader(managedMessagesBody("acme/token", true, ""))).WithContext(ctx)
			req.Header.Set("Authorization", "Bearer "+managedOrgKey)
			req.Header.Set("Content-Type", "application/json")
			req.Header.Set("anthropic-version", "2023-06-01")
			if mode == "non-flusher" {
				// HTTP middleware advertises Flush even when its underlying writer
				// cannot flush. Exercise this handler-only exit without that wrapper.
				g.srv.handlers.AnthropicMessages(w, req)
			} else {
				g.srv.httpServer.Handler.ServeHTTP(w, req)
			}
			<-p.done
			a := g.account(t, "org-m")
			recs := g.outbox("org-m")
			if ctx.Err() != nil || writer.recorder.Code != http.StatusOK || p.calls.Load() != 1 || !p.reported.Load() || a.SpentMicros != 7350 || a.HeldMicros != 0 {
				t.Fatalf("terminal drain: deadline=%v status=%d calls=%d reported=%t spent=%d held=%d; want completion before deadline, one dispatch, full reported cost=7350, held=0", ctx.Err(), writer.recorder.Code, p.calls.Load(), p.reported.Load(), a.SpentMicros, a.HeldMicros)
			}
			if len(recs) != 1 || recs[0]["kind"] != "spend" || recs[0]["cost_source"] != "provider_reported" || recs[0]["usage_estimated"] == "1" {
				t.Fatalf("want one non-estimated provider-reported settlement: %v", recs)
			}
			if mode == "provider-error" {
				body := writer.recorder.Body.String()
				if !strings.Contains(body, managedStreamText) || !strings.Contains(body, "event: error\n") || strings.Contains(body, "event: message_stop\n") {
					t.Fatalf("want first output followed by error, never synthetic success: %s", body)
				}
			}
		})
	}
}
