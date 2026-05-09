package providers

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sort"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// boolPtr returns a pointer to a bool value.
func boolPtr(v bool) *bool { return &v }

// ---------------------------------------------------------------------------
// 1. isLocalURL / IsLocalProvider — URL-based detection
// ---------------------------------------------------------------------------

func TestIsLocalURL(t *testing.T) {
	tests := []struct {
		name string
		url  string
		want bool
	}{
		{"localhost", "http://localhost:8000", true},
		{"loopback IPv4", "http://127.0.0.1:8000", true},
		{"loopback IPv6", "http://[::1]:8000", true},
		{"private 10.x", "http://10.0.0.5:8000", true},
		{"private 172.16.x", "http://172.16.0.1:8000", true},
		{"private 192.168.x", "http://192.168.1.100:8000", true},
		{"public openai", "https://api.openai.com", false},
		{"public IP 8.8.8.8", "https://8.8.8.8", false},
		{"not private 172.32", "http://172.32.0.1:8000", false},
		{"empty URL", "", false},
		{"invalid URL", "://bad", false},
		{"hostname only", "http://my-server.example.com:8080", false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := isLocalURL(tt.url)
			if got != tt.want {
				t.Errorf("isLocalURL(%q) = %v, want %v", tt.url, got, tt.want)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// 2. IsLocalProvider — explicit Local flag overrides URL detection
// ---------------------------------------------------------------------------

func TestIsLocalProvider_ExplicitFlag(t *testing.T) {
	t.Run("Local=true overrides public URL", func(t *testing.T) {
		cfg := config.ProviderConfig{
			BaseURL: "https://api.openai.com",
			Local:   boolPtr(true),
		}
		if !IsLocalProvider(cfg) {
			t.Error("expected IsLocalProvider=true when Local=true, even with public URL")
		}
	})

	t.Run("Local=false overrides localhost URL", func(t *testing.T) {
		cfg := config.ProviderConfig{
			BaseURL: "http://localhost:8000",
			Local:   boolPtr(false),
		}
		if IsLocalProvider(cfg) {
			t.Error("expected IsLocalProvider=false when Local=false, even with localhost URL")
		}
	})

	t.Run("Local=nil falls back to URL detection (local)", func(t *testing.T) {
		cfg := config.ProviderConfig{
			BaseURL: "http://127.0.0.1:11434",
			Local:   nil,
		}
		if !IsLocalProvider(cfg) {
			t.Error("expected IsLocalProvider=true when Local=nil and URL is loopback")
		}
	})

	t.Run("Local=nil falls back to URL detection (remote)", func(t *testing.T) {
		cfg := config.ProviderConfig{
			BaseURL: "https://api.anthropic.com",
			Local:   nil,
		}
		if IsLocalProvider(cfg) {
			t.Error("expected IsLocalProvider=false when Local=nil and URL is public")
		}
	})
}

// ---------------------------------------------------------------------------
// 3. applyLocalDefaults — optimized defaults for local providers
// ---------------------------------------------------------------------------

func TestApplyLocalDefaults(t *testing.T) {
	t.Run("local provider gets defaults when zero values", func(t *testing.T) {
		cfg := config.ProviderConfig{
			BaseURL:   "http://localhost:8000",
			APIFormat: "openai",
		}
		applyLocalDefaults(&cfg)

		if cfg.DefaultTimeout != 120*time.Second {
			t.Errorf("DefaultTimeout = %v, want 120s", cfg.DefaultTimeout)
		}
		if cfg.MaxConcurrent != 10 {
			t.Errorf("MaxConcurrent = %d, want 10", cfg.MaxConcurrent)
		}
		if cfg.ConnPoolSize != 10 {
			t.Errorf("ConnPoolSize = %d, want 10", cfg.ConnPoolSize)
		}
	})

	t.Run("local provider keeps explicit values", func(t *testing.T) {
		cfg := config.ProviderConfig{
			BaseURL:        "http://localhost:8000",
			APIFormat:      "openai",
			DefaultTimeout: 30 * time.Second,
			MaxConcurrent:  5,
			ConnPoolSize:   20,
		}
		applyLocalDefaults(&cfg)

		if cfg.DefaultTimeout != 30*time.Second {
			t.Errorf("DefaultTimeout = %v, want 30s (explicit)", cfg.DefaultTimeout)
		}
		if cfg.MaxConcurrent != 5 {
			t.Errorf("MaxConcurrent = %d, want 5 (explicit)", cfg.MaxConcurrent)
		}
		if cfg.ConnPoolSize != 20 {
			t.Errorf("ConnPoolSize = %d, want 20 (explicit)", cfg.ConnPoolSize)
		}
	})

	t.Run("remote provider gets no changes", func(t *testing.T) {
		cfg := config.ProviderConfig{
			BaseURL:   "https://api.openai.com",
			APIFormat: "openai",
		}
		applyLocalDefaults(&cfg)

		if cfg.DefaultTimeout != 0 {
			t.Errorf("DefaultTimeout = %v, want 0 (unchanged for remote)", cfg.DefaultTimeout)
		}
		if cfg.MaxConcurrent != 0 {
			t.Errorf("MaxConcurrent = %d, want 0 (unchanged for remote)", cfg.MaxConcurrent)
		}
		if cfg.ConnPoolSize != 0 {
			t.Errorf("ConnPoolSize = %d, want 0 (unchanged for remote)", cfg.ConnPoolSize)
		}
	})
}

// ---------------------------------------------------------------------------
// 4. autoDiscoverModels — model discovery from /v1/models
// ---------------------------------------------------------------------------

// newModelsServer creates an httptest.Server that responds to GET /v1/models.
func newModelsServer(t *testing.T, modelIDs []string) *httptest.Server {
	t.Helper()
	mux := http.NewServeMux()
	mux.HandleFunc("/v1/models", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		type modelEntry struct {
			ID string `json:"id"`
		}
		type modelsResp struct {
			Data []modelEntry `json:"data"`
		}
		resp := modelsResp{}
		for _, id := range modelIDs {
			resp.Data = append(resp.Data, modelEntry{ID: id})
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	})
	return httptest.NewServer(mux)
}

func TestAutoDiscoverModels(t *testing.T) {
	t.Run("discovers models when Models list is empty", func(t *testing.T) {
		srv := newModelsServer(t, []string{"model-a", "model-b"})
		defer srv.Close()

		cfg := config.ProviderConfig{
			BaseURL:   srv.URL,
			APIFormat: "openai",
			Models:    nil, // empty
		}
		got := autoDiscoverModels("test", &cfg)

		sort.Strings(got)
		if len(got) != 2 || got[0] != "model-a" || got[1] != "model-b" {
			t.Errorf("got %v, want [model-a model-b]", got)
		}
	})

	t.Run("skips when Models already populated", func(t *testing.T) {
		srv := newModelsServer(t, []string{"model-a", "model-b"})
		defer srv.Close()

		cfg := config.ProviderConfig{
			BaseURL:   srv.URL,
			APIFormat: "openai",
			Models:    []string{"existing-model"},
		}
		got := autoDiscoverModels("test", &cfg)

		if got != nil {
			t.Errorf("expected nil when Models is already populated, got %v", got)
		}
	})

	t.Run("skips when AutoDiscover=false", func(t *testing.T) {
		srv := newModelsServer(t, []string{"model-a"})
		defer srv.Close()

		cfg := config.ProviderConfig{
			BaseURL:      srv.URL,
			APIFormat:    "openai",
			AutoDiscover: boolPtr(false),
		}
		got := autoDiscoverModels("test", &cfg)

		if got != nil {
			t.Errorf("expected nil when AutoDiscover=false, got %v", got)
		}
	})

	t.Run("force discovers when AutoDiscover=true even with existing Models", func(t *testing.T) {
		srv := newModelsServer(t, []string{"model-x", "model-y"})
		defer srv.Close()

		cfg := config.ProviderConfig{
			BaseURL:      srv.URL,
			APIFormat:    "openai",
			Models:       []string{"existing-model"},
			AutoDiscover: boolPtr(true),
		}
		got := autoDiscoverModels("test", &cfg)

		sort.Strings(got)
		if len(got) != 2 || got[0] != "model-x" || got[1] != "model-y" {
			t.Errorf("got %v, want [model-x model-y]", got)
		}
	})

	t.Run("returns nil gracefully on unreachable URL", func(t *testing.T) {
		cfg := config.ProviderConfig{
			BaseURL:   "http://127.0.0.1:1", // port 1 — will not connect
			APIFormat: "openai",
		}
		got := autoDiscoverModels("test", &cfg)

		if got != nil {
			t.Errorf("expected nil for unreachable URL, got %v", got)
		}
	})

	t.Run("returns nil when server returns non-200", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			http.Error(w, "not found", http.StatusNotFound)
		}))
		defer srv.Close()

		cfg := config.ProviderConfig{
			BaseURL:   srv.URL,
			APIFormat: "openai",
		}
		got := autoDiscoverModels("test", &cfg)

		if got != nil {
			t.Errorf("expected nil for non-200 response, got %v", got)
		}
	})

	t.Run("returns nil when server returns invalid JSON", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			w.Write([]byte("not json"))
		}))
		defer srv.Close()

		cfg := config.ProviderConfig{
			BaseURL:   srv.URL,
			APIFormat: "openai",
		}
		got := autoDiscoverModels("test", &cfg)

		if got != nil {
			t.Errorf("expected nil for invalid JSON, got %v", got)
		}
	})
}

// ---------------------------------------------------------------------------
// 5. Integration: NewRegistry with a local provider and auto-discovery
// ---------------------------------------------------------------------------

// newFullMockServer creates a mock server that handles both /v1/models and
// /v1/chat/completions, simulating a local OpenAI-compatible endpoint.
func newFullMockServer(t *testing.T, modelIDs []string) *httptest.Server {
	t.Helper()
	mux := http.NewServeMux()

	mux.HandleFunc("/v1/models", func(w http.ResponseWriter, r *http.Request) {
		type modelEntry struct {
			ID      string `json:"id"`
			Object  string `json:"object"`
			Created int64  `json:"created"`
			OwnedBy string `json:"owned_by"`
		}
		type modelsResp struct {
			Object string       `json:"object"`
			Data   []modelEntry `json:"data"`
		}
		resp := modelsResp{Object: "list"}
		for _, id := range modelIDs {
			resp.Data = append(resp.Data, modelEntry{
				ID:      id,
				Object:  "model",
				Created: 1700000000,
				OwnedBy: "local",
			})
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	})

	mux.HandleFunc("/v1/chat/completions", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		resp := map[string]interface{}{
			"id":      "chatcmpl-test",
			"object":  "chat.completion",
			"created": 1700000000,
			"model":   "local-model",
			"choices": []map[string]interface{}{
				{
					"index": 0,
					"message": map[string]interface{}{
						"role":    "assistant",
						"content": "Hello from local!",
					},
					"finish_reason": "stop",
				},
			},
			"usage": map[string]interface{}{
				"prompt_tokens":     10,
				"completion_tokens": 5,
				"total_tokens":      15,
			},
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	})

	return httptest.NewServer(mux)
}

func TestNewRegistry_LocalProvider(t *testing.T) {
	modelIDs := []string{"llama-3.1-8b", "codellama-7b"}
	srv := newFullMockServer(t, modelIDs)
	defer srv.Close()

	cfg := config.DefaultConfig()
	cfg.Providers["local-vllm"] = config.ProviderConfig{
		BaseURL:   srv.URL,
		APIFormat: "openai",
		Type:      "vllm",
		// Models deliberately empty — should be auto-discovered.
	}

	registry, err := newRegistryForTest(cfg)
	if err != nil {
		t.Fatalf("NewRegistry failed: %v", err)
	}
	defer registry.Close()

	// Verify provider was registered.
	p, ok := registry.GetProvider("local-vllm")
	if !ok {
		t.Fatal("expected provider 'local-vllm' to be registered")
	}
	if p.ID() != "local-vllm" {
		t.Errorf("provider ID = %q, want %q", p.ID(), "local-vllm")
	}

	// Verify discovered models are in the model map.
	allModels := registry.ListAllModels()
	modelSet := make(map[string]bool)
	for _, m := range allModels {
		modelSet[m.ID] = true
	}
	for _, id := range modelIDs {
		if !modelSet[id] {
			t.Errorf("expected model %q to be in registry, models: %v", id, allModels)
		}
	}

	// Verify each model resolves to the local provider.
	for _, id := range modelIDs {
		resolved, err := registry.Resolve(id)
		if err != nil {
			t.Errorf("Resolve(%q) error: %v", id, err)
			continue
		}
		if resolved.ID() != "local-vllm" {
			t.Errorf("Resolve(%q) → provider %q, want %q", id, resolved.ID(), "local-vllm")
		}
	}
}

// newRegistryForTest is a wrapper around NewRegistry that avoids the
// non-blocking connectivity check goroutine racing with test cleanup.
// It calls NewRegistry and gives connectivity checks a brief moment to fire.
func newRegistryForTest(cfg *config.Config) (*Registry, error) {
	reg, err := NewRegistry(cfg)
	if err != nil {
		return nil, err
	}
	// Give the non-blocking connectivity check goroutine time to complete.
	time.Sleep(50 * time.Millisecond)
	return reg, nil
}

func TestNewRegistry_LocalProvider_ExplicitModels(t *testing.T) {
	// When models are explicitly provided, auto-discover is skipped.
	srv := newFullMockServer(t, []string{"server-model-a"})
	defer srv.Close()

	cfg := config.DefaultConfig()
	cfg.Providers["my-ollama"] = config.ProviderConfig{
		BaseURL:   srv.URL,
		APIFormat: "openai",
		Type:      "ollama",
		Models:    []string{"my-explicit-model"},
	}

	registry, err := newRegistryForTest(cfg)
	if err != nil {
		t.Fatalf("NewRegistry failed: %v", err)
	}
	defer registry.Close()

	allModels := registry.ListAllModels()
	modelSet := make(map[string]bool)
	for _, m := range allModels {
		modelSet[m.ID] = true
	}

	// The explicit model should be present.
	if !modelSet["my-explicit-model"] {
		t.Errorf("expected 'my-explicit-model' in registry, got %v", allModels)
	}

	// The server's model should NOT be discovered (auto-discover skipped).
	if modelSet["server-model-a"] {
		t.Error("did not expect 'server-model-a' — auto-discover should have been skipped")
	}
}

func TestNewRegistry_LocalProvider_ForceDiscover(t *testing.T) {
	// When AutoDiscover=true and Models are set, discovery appends to existing.
	srv := newFullMockServer(t, []string{"discovered-1", "discovered-2"})
	defer srv.Close()

	cfg := config.DefaultConfig()
	cfg.Providers["my-local"] = config.ProviderConfig{
		BaseURL:      srv.URL,
		APIFormat:    "openai",
		Models:       []string{"explicit-1"},
		AutoDiscover: boolPtr(true),
	}

	registry, err := newRegistryForTest(cfg)
	if err != nil {
		t.Fatalf("NewRegistry failed: %v", err)
	}
	defer registry.Close()

	allModels := registry.ListAllModels()
	modelSet := make(map[string]bool)
	for _, m := range allModels {
		modelSet[m.ID] = true
	}

	// All three models should be present: the explicit one plus the two discovered.
	for _, id := range []string{"explicit-1", "discovered-1", "discovered-2"} {
		if !modelSet[id] {
			t.Errorf("expected %q in registry, got models: %v", id, allModels)
		}
	}
}

func TestResolveWithRouting_ExplicitModelPreservesRequestedModel(t *testing.T) {
	mock := httptest.NewServer(http.NotFoundHandler())
	defer mock.Close()

	cfg := config.DefaultConfig()
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL:   mock.URL,
		APIFormat: "openai",
		Models:    []string{"gpt-4o"},
	}
	cfg.Providers["fast-proxy"] = config.ProviderConfig{
		BaseURL:   mock.URL,
		APIFormat: "openai",
		Models:    []string{"gpt-4o-mini"},
	}
	cfg.Routing.DefaultStrategy = "least-latency"
	cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{
		"gpt-4o": {
			{Provider: "openai", Weight: 1},
			{Provider: "fast-proxy", Weight: 1, ModelOverride: "gpt-4o-mini"},
		},
	}

	registry, err := newRegistryForTest(cfg)
	if err != nil {
		t.Fatalf("NewRegistry failed: %v", err)
	}
	defer registry.Close()

	if registry.Router() == nil {
		t.Fatal("expected router to be configured")
	}

	registry.Router().RecordLatency("openai", 500*time.Millisecond)
	registry.Router().RecordLatency("fast-proxy", 50*time.Millisecond)

	result, err := registry.ResolveWithRouting("gpt-4o")
	if err != nil {
		t.Fatalf("ResolveWithRouting returned error: %v", err)
	}

	if result.Provider.ID() != "openai" {
		t.Fatalf("Provider ID = %q, want %q", result.Provider.ID(), "openai")
	}
	if result.ModelOverride != "" {
		t.Fatalf("ModelOverride = %q, want empty for explicit model request", result.ModelOverride)
	}
}
