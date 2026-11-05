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
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// ceilingProvider records the deadline its caller handed it and states a
// client timeout of its own, which is the pair the gateway has to reconcile.
type ceilingProvider struct {
	clientTimeout time.Duration
	deadline      time.Time
	hasDeadline   bool
}

func (p *ceilingProvider) ID() string { return "ceiling" }

func (p *ceilingProvider) ClientTimeout() time.Duration { return p.clientTimeout }

func (p *ceilingProvider) record(ctx context.Context) {
	p.deadline, p.hasDeadline = ctx.Deadline()
}

// remaining is how long the handler gave the provider, rounded to whole
// seconds so a test can state one number.
func (p *ceilingProvider) remaining(t *testing.T) time.Duration {
	t.Helper()
	if !p.hasDeadline {
		t.Fatal("the provider was called with no deadline")
	}
	return time.Until(p.deadline).Round(time.Second)
}

func (p *ceilingProvider) CreateEmbedding(ctx context.Context, req *models.EmbeddingRequest) (*models.EmbeddingResponse, error) {
	p.record(ctx)
	return &models.EmbeddingResponse{
		Object: "list",
		Model:  req.Model,
		Data:   []models.EmbeddingData{{Object: "embedding", Index: 0, Embedding: json.RawMessage(`[0.1]`)}},
		Usage:  &models.EmbeddingUsage{PromptTokens: 1, TotalTokens: 1},
	}, nil
}

func (p *ceilingProvider) ChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	p.record(ctx)
	return &models.ChatCompletionResponse{
		ID:      "chatcmpl-ceiling",
		Object:  "chat.completion",
		Model:   req.Model,
		Choices: []models.Choice{{Index: 0, Message: models.Message{Role: "assistant", Content: json.RawMessage(`"ok"`)}, FinishReason: "stop"}},
	}, nil
}

func (p *ceilingProvider) StreamChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	chunks := make(chan models.StreamChunk)
	errs := make(chan error)
	close(chunks)
	close(errs)
	return chunks, errs
}

func (p *ceilingProvider) ListModels(ctx context.Context) ([]models.ModelObject, error) {
	return []models.ModelObject{{ID: "ceiling-model", Object: "model", OwnedBy: "ceiling"}}, nil
}

func (p *ceilingProvider) Close() error { return nil }

// newCeilingServer serves ceiling-model from p, with a config.yaml model
// timeout of modelTimeout over a 60s server default.
func newCeilingServer(t *testing.T, p *ceilingProvider, modelTimeout time.Duration) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Server.DefaultRequestTimeout = 60 * time.Second
	cfg.Routing.ModelTimeouts = map[string]time.Duration{"ceiling-model": modelTimeout}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	registry.RegisterProvider("ceiling", p, []string{"ceiling-model"})

	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	return srv
}

func postEmbedding(t *testing.T, srv *Server) *httptest.ResponseRecorder {
	t.Helper()
	body := `{"model":"ceiling-model","input":"hello"}`
	req := httptest.NewRequest("POST", "/v1/embeddings", bytes.NewBufferString(body))
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != 200 {
		t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
	}
	return w
}

// A per-model timeout above the server default used to survive only on
// /v1/chat/completions: every other route ran inside an envelope at the
// default, which silently cut it back. The envelope is now the one ceiling,
// so the model timeout reaches the provider whole.
func TestEmbedding_ModelTimeoutAboveTheServerDefaultSurvives(t *testing.T) {
	p := &ceilingProvider{clientTimeout: 300 * time.Second}
	srv := newCeilingServer(t, p, 120*time.Second)

	postEmbedding(t, srv)

	if got := p.remaining(t); got != 120*time.Second {
		t.Errorf("the provider had %v, want 120s (the model timeout, not the 60s server default)", got)
	}
}

// x-agentcc-timeout-ms used to report what the gateway resolved, which hid
// both of the limits that could end the call before it.
func TestEmbedding_TimeoutHeaderReportsTheLimitThatApplies(t *testing.T) {
	tests := []struct {
		name          string
		clientTimeout time.Duration
		modelTimeout  time.Duration
		want          string
	}{
		{"the provider's client timeout is the shorter one", 30 * time.Second, 120 * time.Second, "30000"},
		{"the model timeout is the shorter one", 300 * time.Second, 120 * time.Second, "120000"},
		{"a provider that states no timeout leaves the model timeout", 0, 90 * time.Second, "90000"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			p := &ceilingProvider{clientTimeout: tt.clientTimeout}
			srv := newCeilingServer(t, p, tt.modelTimeout)

			w := postEmbedding(t, srv)

			if got := w.Header().Get("x-agentcc-timeout-ms"); got != tt.want {
				t.Errorf("x-agentcc-timeout-ms = %q, want %q", got, tt.want)
			}
		})
	}
}

// The ceiling holds whatever a client asks for.
func TestEmbedding_MaxRequestTimeoutHoldsTheHeaderAndTheModelTimeout(t *testing.T) {
	p := &ceilingProvider{clientTimeout: 300 * time.Second}
	srv := newCeilingServer(t, p, 600*time.Second)
	srv.handlers.maxTimeout = 200 * time.Second

	body := `{"model":"ceiling-model","input":"hello"}`
	req := httptest.NewRequest("POST", "/v1/embeddings", bytes.NewBufferString(body))
	req.Header.Set("x-agentcc-timeout", "10m")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != 200 {
		t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
	}
	if got := w.Header().Get("x-agentcc-timeout-ms"); got != "200000" {
		t.Errorf("x-agentcc-timeout-ms = %q, want 200000 (the ceiling)", got)
	}
	if got := p.remaining(t); got != 200*time.Second {
		t.Errorf("the provider had %v, want 200s (the ceiling)", got)
	}
}

// Chat completions was the one route the timeout middleware skipped, so a
// client could ask for an hour there and get it. It carries the same ceiling
// as every other route now.
func TestChatCompletion_HeaderIsHeldAtTheCeiling(t *testing.T) {
	p := &ceilingProvider{clientTimeout: 300 * time.Second}
	srv := newCeilingServer(t, p, 60*time.Second)
	srv.handlers.maxTimeout = 200 * time.Second

	body := `{"model":"ceiling-model","messages":[{"role":"user","content":"hello"}]}`
	req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewBufferString(body))
	req.Header.Set("x-agentcc-timeout", "10m")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != 200 {
		t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
	}
	if got := p.remaining(t); got != 200*time.Second {
		t.Errorf("the provider had %v, want 200s (the ceiling, not the 10m header)", got)
	}
	if got := w.Header().Get("x-agentcc-timeout-ms"); got != "200000" {
		t.Errorf("x-agentcc-timeout-ms = %q, want 200000 (the ceiling)", got)
	}
}

// The header narrowing reads the provider through an optional interface, so it
// has to hold for the instances the gateway builds itself, not just one handed
// to the registry in a test. Both build paths run through createProvider.
func TestChatCompletion_ProviderCeilingIsReadFromBuiltProviders(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	// A config.yaml provider, resolved through the registry.
	t.Run("a provider from the registry", func(t *testing.T) {
		cfg := config.DefaultConfig()
		cfg.Providers["openai"] = config.ProviderConfig{
			BaseURL:        mock.URL,
			APIKey:         "platform-openai-key",
			APIFormat:      "openai",
			Models:         []string{"gpt-4o"},
			DefaultTimeout: 30 * time.Second,
		}
		cfg.Routing.ModelTimeouts = map[string]time.Duration{"gpt-4o": 120 * time.Second}

		registry, err := providers.NewRegistry(cfg)
		if err != nil {
			t.Fatalf("creating registry: %v", err)
		}
		srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
		srv.ready.Store(true)

		if got := chatTimeoutHeader(t, srv, ""); got != "30000" {
			t.Errorf("x-agentcc-timeout-ms = %q, want 30000 (the provider's client timeout)", got)
		}
	})

	// An org's own provider, built and cached per org.
	t.Run("a provider from the org cache", func(t *testing.T) {
		cfg := config.DefaultConfig()
		cfg.Auth.Enabled = true
		cfg.Auth.Keys = orgTimeoutKeys()
		cfg.Providers["openai"] = config.ProviderConfig{
			BaseURL:   mock.URL,
			APIKey:    "platform-openai-key",
			APIFormat: "openai",
			Models:    []string{"gpt-4o"},
		}
		cfg.Routing.ModelTimeouts = map[string]time.Duration{"gpt-4o": 120 * time.Second}

		registry, err := providers.NewRegistry(cfg)
		if err != nil {
			t.Fatalf("creating registry: %v", err)
		}
		tenantStore := tenant.NewStore()
		tenantStore.Set(orgTimeoutOrgID, &tenant.OrgConfig{
			Providers: map[string]*tenant.ProviderConfig{
				"openai": {Enabled: true, APIKey: "org-openai-key", Models: []string{"gpt-4o"}, Timeout: 20},
			},
		})
		srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), tenantStore, nil)
		srv.ready.Store(true)

		if got := chatTimeoutHeader(t, srv, "Bearer "+orgTimeoutKey); got != "20000" {
			t.Errorf("x-agentcc-timeout-ms = %q, want 20000 (the org provider's client timeout)", got)
		}
	})
}

func chatTimeoutHeader(t *testing.T, srv *Server, bearer string) string {
	t.Helper()
	body := `{"model":"gpt-4o","messages":[{"role":"user","content":"Hello"}]}`
	req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewBufferString(body))
	if bearer != "" {
		req.Header.Set("Authorization", bearer)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != 200 {
		t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
	}
	return w.Header().Get("x-agentcc-timeout-ms")
}

// An internal key keeps the operator's base provider even when its org has a
// provider instance of its own, so the ceiling reported for it has to be the
// base provider's. The org's cached instance is a live decoy: it exists, it is
// keyed by the same org and provider id, and it holds a different timeout.
func TestChatCompletion_InternalKeyReportsTheBaseProviderCeiling(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	const internalKey = "sk-agentcc-internal-ceiling"

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name:     "internal-backend",
		Key:      internalKey,
		Owner:    "futureagi-backend",
		KeyType:  "internal",
		Metadata: map[string]string{"org_id": orgTimeoutOrgID},
	}}
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL:        mock.URL,
		APIKey:         "platform-openai-key",
		APIFormat:      "openai",
		Models:         []string{"gpt-4o"},
		DefaultTimeout: 30 * time.Second,
	}
	cfg.Routing.ModelTimeouts = map[string]time.Duration{"gpt-4o": 120 * time.Second}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}

	// The org has its own openai credentials, for a model this request is not
	// asking for, so the org instance cannot serve it.
	orgProvider := &tenant.ProviderConfig{
		Enabled: true,
		APIKey:  "org-openai-key",
		Models:  []string{"gpt-4o-mini"},
		Timeout: 20,
	}
	tenantStore := tenant.NewStore()
	tenantStore.Set(orgTimeoutOrgID, &tenant.OrgConfig{
		Providers: map[string]*tenant.ProviderConfig{"openai": orgProvider},
	})

	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), tenantStore, nil)
	srv.ready.Store(true)

	// Put the org's instance in the cache, as an earlier request of its own
	// would have.
	if _, err := srv.OrgProviderCache.GetOrCreateWithTenantConfig(orgTimeoutOrgID, "openai", orgProvider.APIKey, orgProvider); err != nil {
		t.Fatalf("warming the org provider cache: %v", err)
	}

	if got := chatTimeoutHeader(t, srv, "Bearer "+internalKey); got != "30000" {
		t.Errorf("x-agentcc-timeout-ms = %q, want 30000 (the base provider, not the org's cached 20s instance)", got)
	}
}

// Model fallback can select an org provider even for an internal key. The
// header must describe that instance, not the base provider with the same ID.
func TestChatCompletion_InternalKeyReportsTheFallbackProviderCeiling(t *testing.T) {
	primary := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusTooManyRequests)
		fmt.Fprint(w, `{"error":{"message":"rate limited","type":"rate_limit_error"}}`)
	}))
	defer primary.Close()
	fallback := startMockOpenAI(t)
	defer fallback.Close()

	for _, stream := range []bool{false, true} {
		for _, providerFailover := range []bool{false, true} {
			t.Run(fmt.Sprintf("stream=%t/provider_failover=%t", stream, providerFailover), func(t *testing.T) {
				const key = "sk-agentcc-internal-fallback-timeout"
				cfg := config.DefaultConfig()
				cfg.Auth.Enabled = true
				cfg.Auth.Keys = []config.AuthKeyConfig{{
					Name: "internal", Key: key, KeyType: "internal",
					Metadata: map[string]string{"org_id": orgTimeoutOrgID},
				}}
				cfg.Providers["primary"] = config.ProviderConfig{
					BaseURL: primary.URL, APIFormat: "openai", Models: []string{"primary-model"},
				}
				cfg.Providers["openai"] = config.ProviderConfig{
					BaseURL: fallback.URL, APIFormat: "openai", Models: []string{"fallback-model"},
					DefaultTimeout: 30 * time.Second,
				}
				cfg.Routing.DefaultStrategy = "round-robin"
				cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 2, OnStatusCodes: []int{http.StatusTooManyRequests}}
				cfg.Routing.ModelFallbacks = map[string][]string{"primary-model": {"fallback-model"}}
				if providerFailover {
					cfg.Providers["primary-backup"] = cfg.Providers["primary"]
				}

				registry, err := providers.NewRegistry(cfg)
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { registry.Close() })
				tenantStore := tenant.NewStore()
				tenantStore.Set(orgTimeoutOrgID, &tenant.OrgConfig{
					Providers: map[string]*tenant.ProviderConfig{
						"openai": {Enabled: true, APIKey: "org-fallback-key", Models: []string{"fallback-model"}, Timeout: 20},
					},
				})
				srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), tenantStore, nil)
				srv.ready.Store(true)

				body := fmt.Sprintf(`{"model":"primary-model","stream":%t,"messages":[{"role":"user","content":"hello"}]}`, stream)
				req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
				req.Header.Set("Authorization", "Bearer "+key)
				w := httptest.NewRecorder()
				srv.httpServer.Handler.ServeHTTP(w, req)

				if w.Code != http.StatusOK {
					t.Fatalf("status = %d, want 200: %s", w.Code, w.Body.String())
				}
				if got := w.Header().Get("x-agentcc-fallback-used"); got != "true" {
					t.Fatalf("fallback used = %q, want true", got)
				}
				if got := w.Header().Get("x-agentcc-provider"); got != "openai" {
					t.Fatalf("provider = %q, want openai", got)
				}
				if !strings.Contains(w.Body.String(), "Hello!") {
					t.Fatalf("missing fallback response: %s", w.Body.String())
				}
				if got := w.Header().Get("x-agentcc-timeout-ms"); got != "20000" {
					t.Errorf("timeout = %q, want the org fallback's 20000, not the base provider's 30000", got)
				}
			})
		}
	}
}
