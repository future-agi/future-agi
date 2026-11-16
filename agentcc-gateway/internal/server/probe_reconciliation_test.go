package server

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type reconciliationProvider struct {
	id     string
	calls  int
	chat   func(context.Context) error
	stream func(context.Context) (<-chan models.StreamChunk, <-chan error)
}

func (p *reconciliationProvider) ID() string   { return p.id }
func (p *reconciliationProvider) Close() error { return nil }
func (p *reconciliationProvider) ListModels(context.Context) ([]models.ModelObject, error) {
	return nil, nil
}
func (p *reconciliationProvider) ChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	p.calls++
	if p.chat != nil {
		if err := p.chat(ctx); err != nil {
			return nil, err
		}
	}
	return &models.ChatCompletionResponse{Model: req.Model}, nil
}
func (p *reconciliationProvider) TextCompletion(ctx context.Context, req *models.CompletionRequest) (*models.CompletionResponse, error) {
	p.calls++
	return &models.CompletionResponse{Model: req.Model}, nil
}
func (p *reconciliationProvider) StreamChatCompletion(ctx context.Context, _ *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	p.calls++
	return p.stream(ctx)
}

func reconciliationConfig() config.CircuitBreakerConfig {
	return config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 1, SuccessThreshold: 2, HalfOpenMaxProbes: 1, Cooldown: time.Nanosecond, OnStatusCodes: []int{500}}
}
func reconciliationHandler(t *testing.T) (*Handlers, *reconciliationProvider) {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Providers = map[string]config.ProviderConfig{"p": {BaseURL: "http://127.0.0.1:1", APIFormat: "openai", Models: []string{"m", "fallback"}}}
	cfg.Routing = config.RoutingConfig{}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	p := &reconciliationProvider{id: "p"}
	registry.RegisterProvider("p", p, nil)
	t.Cleanup(func() { registry.Close() })
	reg := routing.NewCircuitBreakerRegistry(reconciliationConfig(), nil)
	return &Handlers{registry: registry, engine: pipeline.NewEngine(), healthMonitor: routing.NewHealthMonitor(routing.NewLatencyTracker(0.3), reg)}, p
}
func reconciliationRC(t *testing.T) *models.RequestContext {
	t.Helper()
	rc := models.AcquireRequestContext()
	t.Cleanup(rc.Release)
	rc.Provider = "p"
	rc.Model = "m"
	rc.Request = &models.ChatCompletionRequest{Model: "m"}
	rc.RequestHeaders = http.Header{}
	return rc
}
func openReconciliationBreaker(t *testing.T, cb *routing.CircuitBreaker) {
	t.Helper()
	l, ok := cb.Acquire()
	if !ok {
		t.Fatal("fixture seed refused")
	}
	l.Complete(context.Background(), errors.New("synthetic transport failure"))
}
func assertReconciliationCapacity(t *testing.T, cb *routing.CircuitBreaker) {
	t.Helper()
	l, ok := cb.Acquire()
	if !ok {
		t.Fatal("terminal/neutral completion leaked probe")
	}
	defer l.Abandon()
	if _, ok := cb.Acquire(); ok {
		t.Fatal("completion released another live probe")
	}
}

func TestProbeStableCanonicalScopeAcrossPaths(t *testing.T) {
	for _, endpoint := range []string{"chat", "native", "translated", "legacy-stream"} {
		t.Run(endpoint, func(t *testing.T) {
			h, operator := reconciliationHandler(t)
			rc := reconciliationRC(t)
			rc.Metadata["org_id"] = "org-a"
			reg := h.healthMonitor.CircuitBreakerRegistry()
			org := &reconciliationProvider{id: "synthetic-adapter-id"}
			cb := reg.GetScoped("org-a", "p")
			openReconciliationBreaker(t, cb)
			held, ok := cb.Acquire()
			if !ok {
				t.Fatal("fixture probe refused")
			}
			defer held.Abandon()
			// Reconstructing the adapter and using another model retains the same scope.
			replacement := &reconciliationProvider{id: "different-instance-id"}
			rc.Request.Model = "fallback"
			w := httptest.NewRecorder()
			switch endpoint {
			case "chat":
				h.handleNonStream(context.Background(), w, rc, replacement, nil)
			case "native":
				h.handleCompletionNative(context.Background(), w, rc, replacement, &models.CompletionRequest{Model: "fallback"})
			case "translated":
				h.handleCompletionNonStream(context.Background(), w, rc, replacement)
			case "legacy-stream":
				h.handleCompletionStream(context.Background(), w, rc, replacement)
			}
			if w.Code != 503 || !strings.Contains(w.Body.String(), `"code":"circuit_open"`) || replacement.calls != 0 {
				t.Fatalf("%s escaped canonical org scope: status=%d calls=%d body=%s", endpoint, w.Code, replacement.calls, w.Body.String())
			}
			for _, other := range []struct {
				org string
				p   providers.Provider
			}{{"org-b", org}, {"org-a", operator}, {"", org}} {
				rc.Metadata["org_id"] = other.org
				l, err := h.admitProvider(context.Background(), reg, rc, "p", other.p)
				if err != nil {
					t.Fatalf("unrelated scope refused: %v", err)
				}
				l.Abandon()
			}
			// An open operator breaker is not a universal gate on a direct org call.
			shared := reg.Get("p")
			openReconciliationBreaker(t, shared)
			sharedHeld, _ := shared.Acquire()
			defer sharedHeld.Abandon()
			rc.Metadata["org_id"] = "org-b"
			l, err := h.admitProvider(context.Background(), reg, rc, "p", org)
			if err != nil {
				t.Fatal("operator saturation gated independent org direct call")
			}
			l.Abandon()
			if h.healthMonitor.GetHealth("p").RequestCount != 0 {
				t.Fatal("local refusal or org call sampled operator health")
			}
		})
	}
}

func TestProbeTranslatedCompletionAdmitsExactlyOnce(t *testing.T) {
	h, p := reconciliationHandler(t)
	rc := reconciliationRC(t)
	cb := h.healthMonitor.CircuitBreakerRegistry().Get("p")
	openReconciliationBreaker(t, cb)
	p.chat = func(context.Context) error {
		if _, ok := cb.Acquire(); ok {
			t.Error("translated completion did not reserve its probe")
		}
		return nil
	}
	w := httptest.NewRecorder()
	h.handleCompletionNonStream(context.Background(), w, rc, p)
	if w.Code != 200 || p.calls != 1 {
		t.Fatalf("translated completion double-admitted: status=%d calls=%d", w.Code, p.calls)
	}
	if cb.State() != routing.StateHalfOpen {
		t.Fatal("one completion recorded more than one recovery success")
	}
	assertReconciliationCapacity(t, cb)
}

// The first response write gives deterministic control of terminal channel order.
type reconciliationWriter struct {
	*httptest.ResponseRecorder
	once      sync.Once
	first     func()
	fail      bool
	writes    int
	failAfter int
}

func (w *reconciliationWriter) Write(b []byte) (int, error) {
	w.once.Do(w.first)
	w.writes++
	if w.fail || (w.failAfter > 0 && w.writes > w.failAfter) {
		return 0, errors.New("synthetic writer closed")
	}
	return w.ResponseRecorder.Write(b)
}

func TestProbeRawStreamTerminalMatrix(t *testing.T) {
	for _, legacy := range []bool{false, true} {
		for _, org := range []bool{false, true} {
			for _, ending := range []string{"success", "error-channel", "closed-chunks", "cancel", "deadline", "http504", "writer", "closed-errors", "final-writer"} {
				t.Run(fmt.Sprintf("legacy=%t/org=%t/%s", legacy, org, ending), func(t *testing.T) {
					h, p := reconciliationHandler(t)
					rc := reconciliationRC(t)
					rc.Request.Stream = true
					reg := h.healthMonitor.CircuitBreakerRegistry()
					cb := reg.Get("p")
					if org {
						rc.Metadata["org_id"] = "org-a"
						p = &reconciliationProvider{id: "synthetic-org-instance"}
						cb = reg.GetScoped("org-a", "p")
					}
					openReconciliationBreaker(t, cb)
					// One prior recovery success makes accidental neutral-as-success observable.
					prior, _ := cb.Acquire()
					prior.Complete(context.Background(), nil)
					chunks := make(chan models.StreamChunk, 1)
					errs := make(chan error, 1)
					chunks <- models.StreamChunk{ID: "synthetic", Model: "m", Choices: []models.StreamChoice{{}}}
					ctx, cancel := context.WithCancel(context.Background())
					defer cancel()
					var deadlineCancel context.CancelFunc
					if ending == "deadline" {
						ctx, deadlineCancel = context.WithTimeout(context.Background(), 20*time.Millisecond)
						defer deadlineCancel()
					}
					var streamCtx context.Context
					p.stream = func(c context.Context) (<-chan models.StreamChunk, <-chan error) {
						streamCtx = c
						if ending == "closed-errors" {
							close(errs)
						}
						return chunks, errs
					}
					w := &reconciliationWriter{ResponseRecorder: httptest.NewRecorder(), fail: ending == "writer"}
					if ending == "final-writer" {
						w.failAfter = 1
						rc.RequestHeaders.Set("x-agentcc-include-metadata", "true")
					}
					w.first = func() {
						if _, ok := cb.Acquire(); ok {
							t.Error("first chunk released the stream probe")
						}
						if streamCtx.Err() != nil {
							t.Error("startup cleanup canceled stream lifetime")
						}
						switch ending {
						case "success", "closed-errors", "final-writer":
							close(chunks)
						case "error-channel":
							errs <- errors.New("synthetic transport failure")
						case "closed-chunks":
							errs <- errors.New("synthetic transport failure")
							close(chunks)
						case "http504":
							errs <- &models.APIError{Status: 504}
							close(chunks)
						case "cancel":
							cancel()
							close(chunks)
						case "deadline":
							<-ctx.Done()
							close(chunks)
						}
					}
					if legacy {
						h.handleCompletionStream(ctx, w, rc, p)
					} else {
						h.handleStream(ctx, w, rc, p, nil)
					}
					want := routing.StateHalfOpen
					if ending == "success" || ending == "closed-errors" || ending == "final-writer" {
						want = routing.StateClosed
					}
					if ending == "error-channel" || ending == "closed-chunks" || ending == "deadline" {
						want = routing.StateOpen
					}
					if cb.State() != want {
						t.Fatalf("raw outcome changed across stream order: state=%s want=%s", cb.State(), want)
					}
					if want == routing.StateHalfOpen {
						assertReconciliationCapacity(t, cb)
					}
					health := h.healthMonitor.GetHealth("p")
					samples := int64(1)
					if org || ending == "cancel" || ending == "writer" {
						samples = 0
					}
					if health.RequestCount != samples {
						t.Fatalf("terminal physical-call samples=%d want=%d", health.RequestCount, samples)
					}
					if ending == "error-channel" || ending == "writer" {
						close(chunks)
					}
				})
			}
		}
	}
}

func TestProbeModelFallbackCancellationAndProvenance(t *testing.T) {
	for _, mode := range []string{"before", "between", "exhausted", "upstream-code"} {
		t.Run(mode, func(t *testing.T) {
			h, _ := reconciliationHandler(t)
			rc := reconciliationRC(t)
			h.modelFallbacks.Store(routing.NewModelFallbacks(map[string][]string{"m": {"fallback", "m"}}))
			first := &models.APIError{Status: 500, Message: "original upstream"}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if mode == "before" {
				cancel()
			}
			calls := 0
			err := h.callModelFallbacks(ctx, rc, nil, first, true, func(providers.Provider) error {
				calls++
				if mode == "between" {
					cancel()
					return routing.CircuitOpenError()
				}
				if mode == "upstream-code" {
					return &models.APIError{Status: 503, Code: "circuit_open"}
				}
				return routing.CircuitOpenError()
			})
			want := 2
			if mode == "before" {
				want = 0
			}
			if mode == "between" {
				want = 1
			}
			if err != first || calls != want || rc.Provider != "p" || rc.Request.Model != "m" {
				t.Fatalf("fallback provenance/cancellation calls=%d want=%d provider=%s model=%s err=%v", calls, want, rc.Provider, rc.Request.Model, err)
			}
		})
	}
}

func TestProbeFallbackCandidateCredentialEligibility(t *testing.T) {
	h, p := reconciliationHandler(t)
	rc := reconciliationRC(t)
	rc.Metadata["key_type"] = "byok"
	rc.Metadata["org_id"] = "org-a"
	rc.Metadata["org_provider_model_match"] = "m"
	h.keyStore = auth.NewKeyStore(config.AuthConfig{})
	h.modelFallbacks.Store(routing.NewModelFallbacks(map[string][]string{"m": {"fallback"}}))
	first := routing.CircuitOpenError()
	calls := 0
	err := h.callModelFallbacks(context.Background(), rc, &tenant.OrgConfig{}, first, false, func(providers.Provider) error { calls++; return nil })
	if err != first || calls != 0 || p.calls != 0 {
		t.Fatal("fallback refusal introduced operator credentials for an org-only caller")
	}
}

func TestProbeRetrySnapshotAndPhysicalHealth(t *testing.T) {
	h, p := reconciliationHandler(t)
	rc := reconciliationRC(t)
	router, err := routing.NewRouter(config.RoutingConfig{DefaultStrategy: "round-robin", Targets: map[string][]config.RoutingTargetConfig{"m": {{Provider: "p"}}}}, map[string]bool{"p": true}, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer router.Close()
	old := h.healthMonitor.CircuitBreakerRegistry()
	cb := old.Get("p")
	openReconciliationBreaker(t, cb)
	fresh := routing.NewCircuitBreakerRegistry(reconciliationConfig(), nil)
	fo := routing.NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 1}, router, routing.NewRetryer(config.RetryConfig{Enabled: true, MaxRetries: 1, InitialDelay: time.Nanosecond}), old)
	p.chat = func(context.Context) error {
		if _, ok := cb.Acquire(); ok {
			t.Error("retry switched away from held original lease")
		}
		if p.calls == 1 {
			h.healthMonitor.SetCircuitBreakerRegistry(fresh)
			return &models.APIError{Status: 500}
		}
		return nil
	}
	result, err := h.executeProviderFailover(context.Background(), rc, fo, nil, func(ctx context.Context, _ string, p providers.Provider, _ string) error {
		_, err := h.callChatProvider(ctx, p, rc.Request)
		return err
	})
	if err != nil || result.Attempts != 1 || p.calls != 2 {
		t.Fatalf("retry group result=%+v calls=%d err=%v", result, p.calls, err)
	}
	if fresh.Get("p").State() != routing.StateClosed || cb.State() != routing.StateHalfOpen {
		t.Fatal("reload changed retry admission registry")
	}
	if health := h.healthMonitor.GetHealth("p"); health.RequestCount != 2 || health.ErrorCount != 1 {
		t.Fatalf("physical calls not sampled independently: %+v", health)
	}
	assertReconciliationCapacity(t, cb)
}
