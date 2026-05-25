package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func startEmptyStreamOpenAI(t *testing.T) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/v1/chat/completions" && r.Method == "POST":
			w.Header().Set("Content-Type", "text/event-stream")
			w.WriteHeader(http.StatusOK)
			if flusher, ok := w.(http.Flusher); ok {
				flusher.Flush()
			}
		case r.URL.Path == "/v1/models" && r.Method == "GET":
			json.NewEncoder(w).Encode(models.ModelListResponse{
				Object: "list",
				Data:   []models.ModelObject{{ID: "gpt-4o", Object: "model", OwnedBy: "openai"}},
			})
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
}

func newStreamingFailoverHandler(t *testing.T, firstURL string, secondURL string) (*Handlers, providers.Provider) {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Providers["first"] = config.ProviderConfig{BaseURL: firstURL, APIFormat: "openai", Models: []string{"gpt-4o"}}
	cfg.Providers["second"] = config.ProviderConfig{BaseURL: secondURL, APIFormat: "openai", Models: []string{"gpt-4o"}}
	cfg.Routing.DefaultStrategy = "round-robin"
	cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{
		"gpt-4o": {
			{Provider: "first", Weight: 1},
			{Provider: "second", Weight: 1},
		},
	}
	cfg.Routing.Failover = config.FailoverConfig{
		Enabled:       true,
		MaxAttempts:   2,
		OnStatusCodes: []int{http.StatusBadGateway, http.StatusTooManyRequests},
	}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	firstProvider, ok := registry.GetProvider("first")
	if !ok {
		t.Fatal("first provider not registered")
	}

	h := &Handlers{registry: registry, engine: pipeline.NewEngine()}
	h.failover.Store(routing.NewFailover(cfg.Routing.Failover, registry.Router(), nil, nil))
	return h, firstProvider
}

func newStreamingModelFallbackHandler(t *testing.T, primaryURL string, fallbackURL string) (*Handlers, providers.Provider) {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Providers["primary"] = config.ProviderConfig{BaseURL: primaryURL, APIFormat: "openai", Models: []string{"primary-model"}}
	cfg.Providers["fallback"] = config.ProviderConfig{BaseURL: fallbackURL, APIFormat: "openai", Models: []string{"fallback-model"}}
	cfg.Routing.Failover = config.FailoverConfig{
		Enabled:       true,
		MaxAttempts:   2,
		OnStatusCodes: []int{http.StatusTooManyRequests, http.StatusBadGateway},
	}
	cfg.Routing.ModelFallbacks = map[string][]string{
		"primary-model": {"fallback-model"},
	}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	primaryProvider, ok := registry.GetProvider("primary")
	if !ok {
		t.Fatal("primary provider not registered")
	}

	h := &Handlers{registry: registry, engine: pipeline.NewEngine()}
	h.failover.Store(routing.NewFailover(cfg.Routing.Failover, registry.Router(), nil, nil))
	h.modelFallbacks.Store(routing.NewModelFallbacks(cfg.Routing.ModelFallbacks))
	return h, primaryProvider
}

func TestHandleStreamFailoverOnEmptyStreamBeforeHeaders(t *testing.T) {
	empty := startEmptyStreamOpenAI(t)
	defer empty.Close()
	good := startMockOpenAI(t)
	defer good.Close()

	h, firstProvider := newStreamingFailoverHandler(t, empty.URL, good.URL)
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "req-stream-failover"
	rc.Model = "gpt-4o"
	rc.Provider = "first"
	rc.RequestHeaders = http.Header{}
	rc.Request = &models.ChatCompletionRequest{
		Model:    "gpt-4o",
		Stream:   true,
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hello"`)}},
	}

	w := httptest.NewRecorder()
	h.handleStream(context.Background(), w, rc, firstProvider, nil)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
	}
	if got := w.Header().Get("x-agentcc-provider"); got != "second" {
		t.Fatalf("x-agentcc-provider = %q, want second", got)
	}
	body := w.Body.String()
	if !strings.Contains(body, "Hello! How can I help you?") {
		t.Fatalf("stream body did not include fallback provider content: %s", body)
	}
	if !strings.Contains(body, "data: [DONE]") {
		t.Fatalf("stream body missing [DONE]: %s", body)
	}
	if strings.Contains(body, "provider_502") || strings.Contains(body, "upstream stream closed") {
		t.Fatalf("client saw pre-header empty-stream error instead of failover: %s", body)
	}
}

func TestWaitForFirstStreamChunkTreatsClosedStreamAsBadGateway(t *testing.T) {
	chunks := make(chan models.StreamChunk)
	errs := make(chan error)
	close(chunks)
	close(errs)

	chunk, err := waitForFirstStreamChunk(context.Background(), chunks, errs)
	if err == nil {
		t.Fatal("expected error for closed empty stream")
	}
	if chunk != nil {
		t.Fatalf("chunk = %+v, want nil", chunk)
	}
	if !strings.Contains(err.Error(), "provider_502") {
		t.Fatalf("error = %v, want provider_502", err)
	}
}

func TestWaitForFirstStreamChunkReturnsFirstChunk(t *testing.T) {
	chunks := make(chan models.StreamChunk, 1)
	errs := make(chan error)
	content := "hello"
	chunks <- models.StreamChunk{ID: "chunk-1", Choices: []models.StreamChoice{{Delta: models.Delta{Content: &content}}}}

	chunk, err := waitForFirstStreamChunk(context.Background(), chunks, errs)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if chunk == nil || chunk.ID != "chunk-1" {
		t.Fatalf("chunk = %+v, want chunk-1", chunk)
	}
}

func TestWaitForFirstStreamChunkReturnsErrorBeforeChunk(t *testing.T) {
	chunks := make(chan models.StreamChunk)
	errs := make(chan error, 1)
	errs <- models.ErrTooManyRequests("rate limited")

	chunk, err := waitForFirstStreamChunk(context.Background(), chunks, errs)
	if err == nil {
		t.Fatal("expected error before first chunk")
	}
	if chunk != nil {
		t.Fatalf("chunk = %+v, want nil", chunk)
	}
	if !strings.Contains(err.Error(), "rate_limit_exceeded") {
		t.Fatalf("error = %v, want rate_limit_exceeded", err)
	}
}

func TestWaitForFirstStreamChunkReturnsContextCancellation(t *testing.T) {
	chunks := make(chan models.StreamChunk)
	errs := make(chan error)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	chunk, err := waitForFirstStreamChunk(ctx, chunks, errs)
	if err == nil {
		t.Fatal("expected context cancellation")
	}
	if chunk != nil {
		t.Fatalf("chunk = %+v, want nil", chunk)
	}
	if err != context.Canceled {
		t.Fatalf("error = %v, want context.Canceled", err)
	}
}

func TestOpenAIProviderEmptySSEProducesClosedChunkChannel(t *testing.T) {
	upstream := startEmptyStreamOpenAI(t)
	defer upstream.Close()
	cfg := config.ProviderConfig{BaseURL: upstream.URL, APIFormat: "openai", Models: []string{"gpt-4o"}}
	registryCfg := config.DefaultConfig()
	registryCfg.Providers["openai"] = cfg
	registry, err := providers.NewRegistry(registryCfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	p, ok := registry.GetProvider("openai")
	if !ok {
		t.Fatal("openai provider not registered")
	}

	req := &models.ChatCompletionRequest{Model: "gpt-4o", Stream: true, Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}}}
	chunks, errCh := p.StreamChatCompletion(context.Background(), req)
	chunk, firstErr := waitForFirstStreamChunk(context.Background(), chunks, errCh)
	if firstErr == nil || chunk != nil {
		t.Fatalf("chunk=%+v err=%v, want empty stream error", chunk, firstErr)
	}
	if !strings.Contains(firstErr.Error(), "upstream stream closed before sending any chunks") {
		t.Fatalf("error = %v", firstErr)
	}
}

func TestHandleStreamNoFailoverReturnsJSONBeforeSSEHeaders(t *testing.T) {
	empty := startEmptyStreamOpenAI(t)
	defer empty.Close()
	cfg := config.DefaultConfig()
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: empty.URL, APIFormat: "openai", Models: []string{"gpt-4o"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	provider, ok := registry.GetProvider("openai")
	if !ok {
		t.Fatal("openai provider not registered")
	}
	h := &Handlers{registry: registry, engine: pipeline.NewEngine()}
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "req-stream-empty"
	rc.Model = "gpt-4o"
	rc.Provider = "openai"
	rc.RequestHeaders = http.Header{}
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o", Stream: true, Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hello"`)}}}

	w := httptest.NewRecorder()
	h.handleStream(context.Background(), w, rc, provider, nil)

	if w.Code != http.StatusBadGateway {
		t.Fatalf("status = %d, want 502. Body: %s", w.Code, w.Body.String())
	}
	if ct := w.Header().Get("Content-Type"); !strings.HasPrefix(ct, "application/json") {
		t.Fatalf("Content-Type = %q, want application/json", ct)
	}
	if bytes.Contains(w.Body.Bytes(), []byte("data: [DONE]")) {
		t.Fatalf("empty stream should not be finalized as SSE success: %s", w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "upstream stream closed before sending any chunks") {
		t.Fatalf("body missing empty stream explanation: %s", w.Body.String())
	}
}

func TestHandleStreamPropagatesFirstProviderHTTPErrorToFailover(t *testing.T) {
	first := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/v1/chat/completions" && r.Method == "POST":
			w.WriteHeader(http.StatusTooManyRequests)
			fmt.Fprint(w, `{"error":{"message":"rate limited","type":"rate_limit_error","code":"rate_limit_exceeded"}}`)
		case r.URL.Path == "/v1/models" && r.Method == "GET":
			json.NewEncoder(w).Encode(models.ModelListResponse{Object: "list", Data: []models.ModelObject{{ID: "gpt-4o", Object: "model", OwnedBy: "openai"}}})
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer first.Close()
	good := startMockOpenAI(t)
	defer good.Close()

	h, firstProvider := newStreamingFailoverHandler(t, first.URL, good.URL)
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "req-stream-429-failover"
	rc.Model = "gpt-4o"
	rc.Provider = "first"
	rc.RequestHeaders = http.Header{}
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o", Stream: true, Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hello"`)}}}

	w := httptest.NewRecorder()
	h.handleStream(context.Background(), w, rc, firstProvider, nil)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
	}
	if got := w.Header().Get("x-agentcc-provider"); got != "second" {
		t.Fatalf("x-agentcc-provider = %q, want second", got)
	}
}

func TestHandleStreamFailoverUsesOrgProviderOverride(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/v1/chat/completions" && r.Method == "POST":
			if got := r.Header.Get("Authorization"); got != "Bearer org-key" {
				w.WriteHeader(http.StatusUnauthorized)
				fmt.Fprintf(w, `{"error":{"message":"wrong key %s","type":"auth_error","code":"invalid_key"}}`, got)
				return
			}
			w.Header().Set("Content-Type", "text/event-stream")
			fmt.Fprint(w, "data: {\"id\":\"org-override\",\"object\":\"chat.completion.chunk\",\"model\":\"gpt-4o\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"org override ok\"},\"finish_reason\":null}]}\n\n")
			fmt.Fprint(w, "data: [DONE]\n\n")
		case r.URL.Path == "/v1/models" && r.Method == "GET":
			json.NewEncoder(w).Encode(models.ModelListResponse{Object: "list", Data: []models.ModelObject{{ID: "gpt-4o", Object: "model", OwnedBy: "openai"}}})
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer upstream.Close()

	cfg := config.DefaultConfig()
	cfg.Providers["first"] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "base-key", APIFormat: "openai", Models: []string{"gpt-4o"}}
	cfg.Providers["second"] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "base-key", APIFormat: "openai", Models: []string{"gpt-4o"}}
	cfg.Routing.DefaultStrategy = "round-robin"
	cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{
		"gpt-4o": {{Provider: "first", Weight: 1}, {Provider: "second", Weight: 1}},
	}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	firstProvider, ok := registry.GetProvider("first")
	if !ok {
		t.Fatal("first provider not registered")
	}

	h := &Handlers{
		registry:         registry,
		engine:           pipeline.NewEngine(),
		orgProviderCache: providers.NewOrgProviderCache(cfg.Providers),
	}
	orgCfg := &tenant.OrgConfig{
		Providers: map[string]*tenant.ProviderConfig{
			"first": {APIKey: "org-key", Enabled: true, Models: []string{"gpt-4o"}},
		},
		Routing: &tenant.RoutingConfig{Failover: &tenant.FailoverConfig{Enabled: true, MaxAttempts: 1, OnStatusCodes: []int{http.StatusBadGateway}}},
	}
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "req-stream-org-override"
	rc.Model = "gpt-4o"
	rc.Provider = "first"
	rc.RequestHeaders = http.Header{}
	rc.Metadata["org_id"] = "org-1"
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o", Stream: true, Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hello"`)}}}

	w := httptest.NewRecorder()
	h.handleStream(context.Background(), w, rc, firstProvider, orgCfg)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "org override ok") {
		t.Fatalf("body did not use org provider credentials: %s", w.Body.String())
	}
}

func TestHandleStreamUsesModelFallbackChainBeforeHeaders(t *testing.T) {
	primary := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/v1/chat/completions" && r.Method == "POST":
			w.WriteHeader(http.StatusTooManyRequests)
			fmt.Fprint(w, `{"error":{"message":"rate limited","type":"rate_limit_error","code":"rate_limit_exceeded"}}`)
		case r.URL.Path == "/v1/models" && r.Method == "GET":
			json.NewEncoder(w).Encode(models.ModelListResponse{Object: "list", Data: []models.ModelObject{{ID: "primary-model", Object: "model", OwnedBy: "openai"}}})
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer primary.Close()
	fallback := startMockOpenAI(t)
	defer fallback.Close()

	h, primaryProvider := newStreamingModelFallbackHandler(t, primary.URL, fallback.URL)
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "req-stream-model-fallback"
	rc.Model = "primary-model"
	rc.Provider = "primary"
	rc.RequestHeaders = http.Header{}
	rc.Request = &models.ChatCompletionRequest{
		Model:    "primary-model",
		Stream:   true,
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hello"`)}},
	}

	w := httptest.NewRecorder()
	h.handleStream(context.Background(), w, rc, primaryProvider, nil)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
	}
	if got := w.Header().Get("x-agentcc-provider"); got != "fallback" {
		t.Fatalf("x-agentcc-provider = %q, want fallback", got)
	}
	if rc.Request.Model != "fallback-model" {
		t.Fatalf("request model = %q, want fallback-model", rc.Request.Model)
	}
	if !rc.Flags.FallbackUsed {
		t.Fatal("FallbackUsed = false, want true")
	}
	if rc.Metadata["original_model"] != "primary-model" {
		t.Fatalf("original_model metadata = %q, want primary-model", rc.Metadata["original_model"])
	}
	if rc.Metadata["fallback_model"] != "fallback-model" {
		t.Fatalf("fallback_model metadata = %q, want fallback-model", rc.Metadata["fallback_model"])
	}
	body := w.Body.String()
	if !strings.Contains(body, "Hello! How can I help you?") {
		t.Fatalf("stream body did not include fallback model content: %s", body)
	}
	if strings.Contains(body, "rate_limit_exceeded") {
		t.Fatalf("client saw primary model error instead of fallback stream: %s", body)
	}
}

func TestHandleStreamUsesModelFallbackAfterProviderFailoverExhausted(t *testing.T) {
	primaryA := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/v1/chat/completions" && r.Method == "POST":
			w.WriteHeader(http.StatusTooManyRequests)
			fmt.Fprint(w, `{"error":{"message":"rate limited a","type":"rate_limit_error","code":"rate_limit_exceeded"}}`)
		case r.URL.Path == "/v1/models" && r.Method == "GET":
			json.NewEncoder(w).Encode(models.ModelListResponse{Object: "list", Data: []models.ModelObject{{ID: "primary-model", Object: "model", OwnedBy: "openai"}}})
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer primaryA.Close()
	primaryB := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/v1/chat/completions" && r.Method == "POST":
			w.WriteHeader(http.StatusTooManyRequests)
			fmt.Fprint(w, `{"error":{"message":"rate limited b","type":"rate_limit_error","code":"rate_limit_exceeded"}}`)
		case r.URL.Path == "/v1/models" && r.Method == "GET":
			json.NewEncoder(w).Encode(models.ModelListResponse{Object: "list", Data: []models.ModelObject{{ID: "primary-model", Object: "model", OwnedBy: "openai"}}})
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer primaryB.Close()
	fallback := startMockOpenAI(t)
	defer fallback.Close()

	cfg := config.DefaultConfig()
	cfg.Providers["primary-a"] = config.ProviderConfig{BaseURL: primaryA.URL, APIFormat: "openai", Models: []string{"primary-model"}}
	cfg.Providers["primary-b"] = config.ProviderConfig{BaseURL: primaryB.URL, APIFormat: "openai", Models: []string{"primary-model"}}
	cfg.Providers["fallback"] = config.ProviderConfig{BaseURL: fallback.URL, APIFormat: "openai", Models: []string{"fallback-model"}}
	cfg.Routing.DefaultStrategy = "round-robin"
	cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{
		"primary-model": {
			{Provider: "primary-a", Weight: 1},
			{Provider: "primary-b", Weight: 1},
		},
	}
	cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 2, OnStatusCodes: []int{http.StatusTooManyRequests}}
	cfg.Routing.ModelFallbacks = map[string][]string{"primary-model": {"fallback-model"}}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	primaryProvider, ok := registry.GetProvider("primary-a")
	if !ok {
		t.Fatal("primary-a provider not registered")
	}
	h := &Handlers{registry: registry, engine: pipeline.NewEngine()}
	h.failover.Store(routing.NewFailover(cfg.Routing.Failover, registry.Router(), nil, nil))
	h.modelFallbacks.Store(routing.NewModelFallbacks(cfg.Routing.ModelFallbacks))

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "req-stream-failover-exhausted-model-fallback"
	rc.Model = "primary-model"
	rc.Provider = "primary-a"
	rc.RequestHeaders = http.Header{}
	rc.Request = &models.ChatCompletionRequest{
		Model:    "primary-model",
		Stream:   true,
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hello"`)}},
	}

	w := httptest.NewRecorder()
	h.handleStream(context.Background(), w, rc, primaryProvider, nil)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
	}
	if got := w.Header().Get("x-agentcc-provider"); got != "fallback" {
		t.Fatalf("x-agentcc-provider = %q, want fallback", got)
	}
	if rc.Metadata["original_model"] != "primary-model" || rc.Metadata["fallback_model"] != "fallback-model" {
		t.Fatalf("fallback metadata = original:%q fallback:%q", rc.Metadata["original_model"], rc.Metadata["fallback_model"])
	}
	if !strings.Contains(w.Body.String(), "Hello! How can I help you?") {
		t.Fatalf("stream body did not include model fallback content: %s", w.Body.String())
	}
}
