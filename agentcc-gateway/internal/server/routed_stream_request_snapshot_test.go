package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

type g1DeferredStreamProvider struct {
	providers.Provider
	stream func(context.Context, *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error)
}

func (p *g1DeferredStreamProvider) StreamChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	return p.stream(ctx, req)
}

// An unsuccessful stream start does not join the adapter's goroutine. Keep a
// deliberately late encoder alive after the start failure, as can happen when
// the start context expires while an adapter is still waiting for admission.
// The fallback itself releases it, AFTER rewriting rc.Request.Model. Channels
// make the wrong-model observation deterministic, not a scheduler/sleep bet.
func TestG1RoutedStreamRequestSnapshot(t *testing.T) {
	for _, exhausted := range []bool{false, true} {
		t.Run(fmt.Sprintf("exhausted=%t", exhausted), func(t *testing.T) {
			g := newAttemptsGateway(t, attemptsGatewayOpts{})
			defer func() {
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				if err := g.srv.Shutdown(ctx); err != nil {
					t.Error(err)
				}
				g.srv.batchProcessor.Stop()
			}()
			h := g.srv.handlers
			const firstModel, fallbackModel = "gpt-4o", "gpt-4o-mini"
			prices := map[string]models.PriceCard{
				firstModel:    {Prompt: 4e-6, Completion: 20e-6, Source: models.PriceSourceCatalog, SnapshotID: "g1-first"},
				fallbackModel: {Prompt: 1e-6, Completion: 5e-6, Source: models.PriceSourceCatalog, SnapshotID: "g1-fallback"},
			}
			h.hookObserver = func(hook, _ string, rc *models.RequestContext) {
				if hook == hookBeginAttempt {
					price := prices[rc.Request.Model]
					rc.Billing.ReservePrice = &price
				}
			}
			wire := make(chan string, 2)
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				var request models.ChatCompletionRequest
				if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
					t.Error(err)
					w.WriteHeader(http.StatusBadRequest)
					return
				}
				wire <- request.Model
				w.WriteHeader(http.StatusNoContent)
			}))
			defer upstream.Close()
			send := func(request *models.ChatCompletionRequest) error {
				body, err := json.Marshal(request)
				if err != nil {
					return err
				}
				resp, err := upstream.Client().Post(upstream.URL, "application/json", bytes.NewReader(body))
				if err != nil {
					return err
				}
				_, err = io.Copy(io.Discard, resp.Body)
				resp.Body.Close()
				return err
			}
			failed := func() (<-chan models.StreamChunk, <-chan error) {
				chunks, errs := make(chan models.StreamChunk), make(chan error, 1)
				errs <- models.ErrUpstreamProvider(http.StatusServiceUnavailable, "synthetic start failure")
				close(errs)
				close(chunks)
				return chunks, errs
			}
			fallbackEntered := make(chan struct{})
			firstSent := make(chan error, 1)
			readAfterFallback := make(chan struct{})
			workerDone := make(chan string, 1)
			abortWorker := make(chan struct{})
			workerExited := make(chan struct{})
			defer func() {
				close(abortWorker)
				select {
				case <-workerExited:
				case <-time.After(5 * time.Second):
					t.Error("late encoder cleanup timed out")
				}
			}()
			primaryBase, _ := h.registry.GetProvider("first")
			fallbackBase, _ := h.registry.GetProvider("second")
			primary := &g1DeferredStreamProvider{Provider: primaryBase}
			primary.stream = func(_ context.Context, request *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
				go func() {
					defer close(workerExited)
					select {
					case <-fallbackEntered:
					case <-abortWorker:
						return
					}
					firstSent <- send(request)
					select {
					case <-readAfterFallback:
					case <-abortWorker:
						return
					}
					// This read and the exhausted chain's restoration are unordered.
					// It exposes the real caller write to -race independently of the
					// synchronized, deterministic wire-model assertion above.
					workerDone <- request.Model
				}()
				return failed()
			}
			fallback := &g1DeferredStreamProvider{Provider: fallbackBase}
			fallback.stream = func(_ context.Context, request *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
				close(fallbackEntered)
				if err := <-firstSent; err != nil {
					t.Error(err)
				}
				if err := send(request); err != nil {
					t.Error(err)
				}
				close(readAfterFallback)
				if exhausted {
					return failed()
				}
				chunks, errs := make(chan models.StreamChunk, 1), make(chan error)
				text := "ok"
				chunks <- models.StreamChunk{Model: request.Model, Choices: []models.StreamChoice{{Delta: models.Delta{Content: &text}}}}
				close(chunks)
				close(errs)
				return chunks, errs
			}
			h.registry.RegisterProvider("second", fallback, []string{fallbackModel})
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.RequestID, rc.Model, rc.Provider = "g1-snapshot", firstModel, "first"
			// This direct dispatch skips the HTTP auth plugin used by g.chat.
			// Carry the fixture's internal-key identity through fallback admission.
			rc.Metadata["key_type"] = "internal"
			rc.Request = &models.ChatCompletionRequest{Model: firstModel, Stream: true}
			original := rc.Request
			rc.Routing.Models = []string{fallbackModel}
			stream, err := h.openRoutedStream(context.Background(), rc, primary, nil, false)
			if stream != nil {
				stream.cancel()
			}
			if (err != nil) != exhausted {
				t.Fatalf("exhausted=%t: stream error %v", exhausted, err)
			}
			select {
			case <-workerDone:
			case <-time.After(5 * time.Second):
				t.Fatal("late encoder did not finish")
			}
			attempts := rc.AttemptsSnapshot()
			if len(attempts) != 2 {
				t.Fatalf("attempts=%+v; want primary and fallback", attempts)
			}
			for i, want := range []string{firstModel, fallbackModel} {
				got := <-wire
				a := attempts[i]
				if a.Model != want || a.Pricing == nil || a.Pricing.ReservePrice == nil || *a.Pricing.ReservePrice != prices[want] {
					t.Errorf("hop %d admission/pricing=%+v; want model %s price %+v", i, a, want, prices[want])
				}
				if got != want {
					t.Errorf("hop %d sent %q but was admitted and priced for %q", i, got, want)
				}
			}
			wantModel := fallbackModel
			if exhausted {
				wantModel = firstModel
			} else if !rc.Flags.FallbackUsed || rc.Metadata["original_model"] != firstModel || rc.Metadata["fallback_model"] != fallbackModel {
				t.Errorf("fallback metadata changed: %+v", rc.Metadata)
			}
			if rc.Request != original || rc.Request.Model != wantModel {
				t.Errorf("request logging identity changed: same pointer=%t model=%s", rc.Request == original, rc.Request.Model)
			}
		})
	}
}

// The exact cell attributed the original -race report. Calling the existing
// cell directly avoids generating/running the rest of the large matrix.
func TestG1CAT2RaceCell(t *testing.T) {
	cell := newCat2PlanCell(cat2SourceNode, cat2MatrixOR+cat2MatrixMini,
		cat2MatrixOR+cat2MatrixGem, cat2MatrixOR+cat2MatrixMini, "runner-upstream",
		cat2MatrixToken, cat2MatrixToken, cat2MatrixGem, true, false, true)
	cell.check(t)
}
