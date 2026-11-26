package server

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

func TestProbeNoRouterFallbackPolicy(t *testing.T) {
	for _, enabled := range []bool{false, true} {
		for _, refused := range []bool{false, true} {
			t.Run(fmt.Sprintf("enabled=%t/refused=%t", enabled, refused), func(t *testing.T) {
				upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(500) }))
				defer upstream.Close()
				cfg := probeConfig(upstream.URL)
				cfg.Providers["other"] = config.ProviderConfig{BaseURL: upstream.URL, APIFormat: "openai", Models: []string{"fallback"}}
				cfg.Routing.Targets = nil
				cfg.Routing.CircuitBreaker.Enabled = enabled
				cfg.Routing.ModelFallbacks = map[string][]string{"primary": {"fallback"}}
				srv, _ := newProbeServer(t, cfg, nil)
				if srv.registry.Router() != nil || srv.handlers.effectiveFailover(nil) != nil {
					t.Fatal("no-router deployment acquired an ordinary failover policy")
				}
				p := &reconciliationProvider{id: "probe", chat: func(context.Context) error { return &models.APIError{Status: 500} }}
				other := &reconciliationProvider{id: "other"}
				srv.registry.RegisterProvider("probe", p, nil)
				srv.registry.RegisterProvider("other", other, nil)
				rc := reconciliationRC(t)
				rc.Model = "primary"
				rc.Request.Model = "primary"
				rc.Provider = "probe"
				if enabled && refused {
					cb := srv.handlers.healthMonitor.CircuitBreakerRegistry().Get("probe")
					openReconciliationBreaker(t, cb)
					time.Sleep(15 * time.Millisecond)
					held, _ := cb.Acquire()
					defer held.Abandon()
				}
				w := httptest.NewRecorder()
				srv.handlers.handleNonStream(context.Background(), w, rc, p, nil)
				wantCalls := 0
				wantStatus := 500
				if enabled && refused {
					wantCalls = 1
					wantStatus = 200
				}
				if other.calls != wantCalls || w.Code != wantStatus {
					t.Fatalf("no-router fallback policy changed: other_calls=%d status=%d", other.calls, w.Code)
				}
			})
		}
	}
}

type reconciliationPolicy struct{ phase string }

func (p reconciliationPolicy) Name() string  { return "synthetic-policy" }
func (p reconciliationPolicy) Priority() int { return 10 }
func (p reconciliationPolicy) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	if p.phase == "pre" {
		return pipeline.ResultError(&models.APIError{Status: 403})
	}
	return pipeline.ResultContinue()
}
func (p reconciliationPolicy) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	if p.phase == "post" {
		return pipeline.ResultError(&models.APIError{Status: 403})
	}
	return pipeline.ResultContinue()
}

func TestProbePipelineOutcomesAreNotProviderFailures(t *testing.T) {
	for _, phase := range []string{"pre", "post"} {
		for _, endpoint := range []string{"chat", "native", "translated"} {
			t.Run(phase+"/"+endpoint, func(t *testing.T) {
				h, _ := reconciliationHandler(t)
				h.engine = pipeline.NewEngine(reconciliationPolicy{phase})
				rc := reconciliationRC(t)
				rc.Metadata["org_id"] = "org-a"
				p := &reconciliationProvider{id: "synthetic-org-instance"}
				cb := h.healthMonitor.CircuitBreakerRegistry().GetScoped("org-a", "p")
				openReconciliationBreaker(t, cb)
				// Prime half-open; abandon does not count as success.
				prime, _ := cb.Acquire()
				prime.Abandon()
				w := httptest.NewRecorder()
				switch endpoint {
				case "chat":
					h.handleNonStream(context.Background(), w, rc, p, nil)
				case "native":
					h.handleCompletionNative(context.Background(), w, rc, p, &models.CompletionRequest{Model: "m"})
				case "translated":
					h.handleCompletionNonStream(context.Background(), w, rc, p)
				}
				want := 0
				if phase == "post" {
					want = 1
				}
				if w.Code != 403 || p.calls != want || cb.State() != routing.StateHalfOpen {
					t.Fatalf("pipeline 403 changed provider accounting: status=%d calls=%d state=%s", w.Code, p.calls, cb.State())
				}
				assertReconciliationCapacity(t, cb)
				next, _ := cb.Acquire()
				next.Complete(context.Background(), nil)
				wantState := routing.StateHalfOpen
				if phase == "post" {
					wantState = routing.StateClosed
				}
				if cb.State() != wantState {
					t.Fatal("pipeline result replaced the actual provider completion")
				}
			})
		}
	}
}

func TestProbeStreamStartupRetryTransfersOneLease(t *testing.T) {
	h, p := reconciliationHandler(t)
	rc := reconciliationRC(t)
	rc.Request.Stream = true
	cfg := config.DefaultConfig()
	cfg.Routing = config.RoutingConfig{
		DefaultStrategy: "round-robin", Targets: map[string][]config.RoutingTargetConfig{"m": {{Provider: "p"}, {Provider: "p"}}},
		Failover: config.FailoverConfig{Enabled: true, MaxAttempts: 1, PerAttemptTimeout: time.Second},
		Retry:    config.RetryConfig{Enabled: true, MaxRetries: 1, InitialDelay: time.Nanosecond}, CircuitBreaker: reconciliationConfig(),
	}
	if err := h.ReloadRouting(cfg, h.registry); err != nil {
		t.Fatal(err)
	}
	cb := h.healthMonitor.CircuitBreakerRegistry().Get("p")
	openReconciliationBreaker(t, cb)
	prime, allowed := cb.Acquire()
	if !allowed {
		t.Fatal("fixture recovery probe refused")
	}
	prime.Abandon()
	chunks := make(chan models.StreamChunk, 1)
	chunks <- models.StreamChunk{Model: "m", Choices: []models.StreamChoice{{}}}
	errs := make(chan error)
	close(errs)
	var failedCtx, liveCtx context.Context
	p.stream = func(ctx context.Context) (<-chan models.StreamChunk, <-chan error) {
		if _, ok := cb.Acquire(); ok {
			t.Error("startup retry lost shared lease")
		}
		if p.calls == 1 {
			failedCtx = ctx
			badChunks := make(chan models.StreamChunk)
			badErrs := make(chan error, 1)
			badErrs <- &models.APIError{Status: 500}
			close(badErrs)
			close(badChunks)
			return badChunks, badErrs
		}
		if failedCtx.Err() == nil {
			t.Error("failed startup not canceled before retry")
		}
		liveCtx = ctx
		return chunks, errs
	}
	w := &reconciliationWriter{ResponseRecorder: httptest.NewRecorder()}
	w.first = func() {
		if liveCtx.Err() != nil {
			t.Error("attempt cleanup canceled transferred stream")
		}
		if _, ok := cb.Acquire(); ok {
			t.Error("first chunk released transferred lease")
		}
		close(chunks)
	}
	h.handleStream(context.Background(), w, rc, p, nil)
	if w.Code != 200 || !strings.Contains(w.Body.String(), "[DONE]") || p.calls != 2 || cb.State() != routing.StateHalfOpen {
		t.Fatalf("stream retry accounting: status=%d calls=%d state=%s", w.Code, p.calls, cb.State())
	}
	if got := h.healthMonitor.GetHealth("p"); got.RequestCount != 2 || got.ErrorCount != 1 {
		t.Fatalf("startup retry physical health=%+v", got)
	}
	assertReconciliationCapacity(t, cb)
}

func TestProbeStartupFailureReleasesCapacity(t *testing.T) {
	for _, legacy := range []bool{false, true} {
		for _, mode := range []string{"nil", "empty", "transport"} {
			t.Run(fmt.Sprintf("legacy=%t/%s", legacy, mode), func(t *testing.T) {
				h, p := reconciliationHandler(t)
				rc := reconciliationRC(t)
				rc.Request.Stream = true
				cb := h.healthMonitor.CircuitBreakerRegistry().Get("p")
				openReconciliationBreaker(t, cb)
				p.stream = func(context.Context) (<-chan models.StreamChunk, <-chan error) {
					if mode == "nil" {
						return nil, nil
					}
					chunks := make(chan models.StreamChunk)
					errs := make(chan error, 1)
					if mode == "transport" {
						errs <- errors.New("synthetic read failure")
					}
					close(errs)
					close(chunks)
					return chunks, errs
				}
				w := httptest.NewRecorder()
				if legacy {
					h.handleCompletionStream(context.Background(), w, rc, p)
				} else {
					h.handleStream(context.Background(), w, rc, p, nil)
				}
				if w.Code == 200 || p.calls != 1 {
					t.Fatalf("startup failure response status=%d calls=%d", w.Code, p.calls)
				}
				// The fixture's custom [500] counts raw transport errors; empty-stream 502 is neutral.
				want := routing.StateHalfOpen
				if mode == "transport" || (legacy && mode == "nil") {
					want = routing.StateOpen
				}
				if cb.State() != want {
					t.Fatalf("startup outcome state=%s want=%s", cb.State(), want)
				}
				if want == routing.StateHalfOpen {
					assertReconciliationCapacity(t, cb)
				}
			})
		}
	}
}

func TestProbeOrgStatusAugmentationAtDirectBoundary(t *testing.T) {
	for _, status := range []int{401, 403} {
		for _, org := range []bool{false, true} {
			t.Run(fmt.Sprintf("%d/org=%t", status, org), func(t *testing.T) {
				h, p := reconciliationHandler(t)
				rc := reconciliationRC(t)
				reg := h.healthMonitor.CircuitBreakerRegistry()
				cb := reg.Get("p")
				if org {
					rc.Metadata["org_id"] = "org-a"
					p = &reconciliationProvider{id: "org-instance"}
					cb = reg.GetScoped("org-a", "p")
				}
				err := h.callProvider(context.Background(), reg, rc, p, func() error { return &models.APIError{Status: status} })
				if err == nil {
					t.Fatal("provider status lost")
				}
				want := routing.StateClosed
				if org {
					want = routing.StateOpen
				}
				if cb.State() != want {
					t.Fatalf("org status augmentation state=%s want=%s", cb.State(), want)
				}
			})
		}
	}
}
