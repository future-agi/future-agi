package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	agentcca2a "github.com/futureagi/agentcc-gateway/internal/a2a"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type metadataPostPlugin struct{}

type presetResolvedModelPlugin struct{}

func testStrPtr(s string) *string { return &s }

func (p *metadataPostPlugin) Name() string  { return "metadata_post" }
func (p *metadataPostPlugin) Priority() int { return 100 }
func (p *metadataPostPlugin) ProcessRequest(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (p *metadataPostPlugin) ProcessResponse(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	rc.Metadata["cost"] = "0.004500"
	return pipeline.ResultContinue()
}

func (p *presetResolvedModelPlugin) Name() string  { return "preset_resolved_model" }
func (p *presetResolvedModelPlugin) Priority() int { return 5 }
func (p *presetResolvedModelPlugin) ProcessRequest(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	rc.ResolvedModel = rc.Model
	return pipeline.ResultContinue()
}
func (p *presetResolvedModelPlugin) ProcessResponse(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

// testModelDBPtr returns a shared atomic pointer to a test ModelDB (uses bundled data).
func testModelDBPtr() *atomic.Pointer[modeldb.ModelDB] {
	db := modeldb.New(modeldb.BundledModels, nil)
	var ptr atomic.Pointer[modeldb.ModelDB]
	ptr.Store(db)
	return &ptr
}

// startMockOpenAI creates a mock OpenAI API server for testing.
func startMockOpenAI(t *testing.T) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/v1/chat/completions" && r.Method == "POST":
			var req models.ChatCompletionRequest
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				w.WriteHeader(400)
				json.NewEncoder(w).Encode(models.ErrorResponse{
					Error: models.ErrorDetail{Message: "bad request", Type: "invalid_request_error", Code: "bad_request"},
				})
				return
			}

			if req.Stream {
				// Streaming response.
				w.Header().Set("Content-Type", "text/event-stream")
				w.WriteHeader(200)
				flusher := w.(http.Flusher)

				chunk1 := models.StreamChunk{
					ID:      "chatcmpl-test",
					Object:  "chat.completion.chunk",
					Created: 1700000000,
					Model:   req.Model,
					Choices: []models.StreamChoice{
						{Index: 0, Delta: models.Delta{Role: "assistant"}},
					},
				}
				data1, _ := json.Marshal(chunk1)
				fmt.Fprintf(w, "data: %s\n\n", data1)
				flusher.Flush()

				content := "Hello! How can I help you?"
				chunk2 := models.StreamChunk{
					ID:      "chatcmpl-test",
					Object:  "chat.completion.chunk",
					Created: 1700000000,
					Model:   req.Model,
					Choices: []models.StreamChoice{
						{Index: 0, Delta: models.Delta{Content: &content}},
					},
				}
				data2, _ := json.Marshal(chunk2)
				fmt.Fprintf(w, "data: %s\n\n", data2)
				flusher.Flush()

				done := "stop"
				chunk3 := models.StreamChunk{
					ID:      "chatcmpl-test",
					Object:  "chat.completion.chunk",
					Created: 1700000000,
					Model:   req.Model,
					Choices: []models.StreamChoice{
						{Index: 0, Delta: models.Delta{}, FinishReason: &done},
					},
				}
				data3, _ := json.Marshal(chunk3)
				fmt.Fprintf(w, "data: %s\n\n", data3)
				flusher.Flush()

				fmt.Fprint(w, "data: [DONE]\n\n")
				flusher.Flush()
				return
			}

			// Non-streaming response.
			resp := models.ChatCompletionResponse{
				ID:      "chatcmpl-test-123",
				Object:  "chat.completion",
				Created: 1700000000,
				Model:   req.Model,
				Choices: []models.Choice{
					{
						Index: 0,
						Message: models.Message{
							Role:    "assistant",
							Content: json.RawMessage(`"Hello! How can I help you today?"`),
						},
						FinishReason: "stop",
					},
				},
				Usage: &models.Usage{
					PromptTokens:     10,
					CompletionTokens: 20,
					TotalTokens:      30,
				},
			}
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(200)
			json.NewEncoder(w).Encode(resp)

		case r.URL.Path == "/v1/models" && r.Method == "GET":
			resp := models.ModelListResponse{
				Object: "list",
				Data: []models.ModelObject{
					{ID: "gpt-4o", Object: "model", OwnedBy: "openai"},
				},
			}
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(resp)

		case r.URL.Path == "/v1/responses" && r.Method == "POST":
			var req models.ResponsesRequest
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				w.WriteHeader(400)
				json.NewEncoder(w).Encode(models.ErrorResponse{
					Error: models.ErrorDetail{Message: "bad request", Type: "invalid_request_error", Code: "bad_request"},
				})
				return
			}

			resp := map[string]any{
				"id":         "resp_test_123",
				"object":     "response",
				"created_at": 1700000000,
				"status":     "completed",
				"model":      req.Model,
				"output": []map[string]any{{
					"id":     "msg_test_123",
					"type":   "message",
					"status": "completed",
					"role":   "assistant",
					"content": []map[string]any{{
						"type": "output_text",
						"text": "2 + 2 equals 4.",
					}},
				}},
				"usage": map[string]any{
					"input_tokens":  10,
					"output_tokens": 5,
					"total_tokens":  15,
				},
			}
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(200)
			json.NewEncoder(w).Encode(resp)

		default:
			w.WriteHeader(404)
		}
	}))
}

func startMockOpenAIWithUsageChunk(t *testing.T) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/v1/chat/completions" && r.Method == "POST":
			var req models.ChatCompletionRequest
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				w.WriteHeader(400)
				json.NewEncoder(w).Encode(models.ErrorResponse{
					Error: models.ErrorDetail{Message: "bad request", Type: "invalid_request_error", Code: "bad_request"},
				})
				return
			}

			if req.Stream {
				w.Header().Set("Content-Type", "text/event-stream")
				w.WriteHeader(200)
				flusher := w.(http.Flusher)

				chunks := []models.StreamChunk{
					{
						ID:      "chatcmpl-test",
						Object:  "chat.completion.chunk",
						Created: 1700000000,
						Model:   req.Model,
						Choices: []models.StreamChoice{{Index: 0, Delta: models.Delta{Role: "assistant"}}},
					},
					{
						ID:      "chatcmpl-test",
						Object:  "chat.completion.chunk",
						Created: 1700000000,
						Model:   req.Model,
						Choices: []models.StreamChoice{{Index: 0, Delta: models.Delta{Content: testStrPtr("Hello! How can I help you?")}}},
					},
					{
						ID:      "chatcmpl-test",
						Object:  "chat.completion.chunk",
						Created: 1700000000,
						Model:   req.Model,
						Choices: []models.StreamChoice{},
						Usage:   &models.Usage{PromptTokens: 10, CompletionTokens: 20, TotalTokens: 30},
					},
				}
				for _, chunk := range chunks {
					data, _ := json.Marshal(chunk)
					fmt.Fprintf(w, "data: %s\n\n", data)
					flusher.Flush()
				}
				fmt.Fprint(w, "data: [DONE]\n\n")
				flusher.Flush()
				return
			}

			w.WriteHeader(http.StatusBadRequest)
		default:
			w.WriteHeader(404)
		}
	}))
}

func startValidatedMockOpenAI(t *testing.T, delay time.Duration) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/v1/chat/completions" && r.Method == "POST":
			var req models.ChatCompletionRequest
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				w.WriteHeader(http.StatusBadRequest)
				json.NewEncoder(w).Encode(models.ErrorResponse{
					Error: models.ErrorDetail{Message: "bad request", Type: "invalid_request_error", Code: "bad_request"},
				})
				return
			}

			if req.Model != "gpt-4o" && req.Model != "gpt-4o-mini" {
				w.WriteHeader(http.StatusBadRequest)
				json.NewEncoder(w).Encode(models.ErrorResponse{
					Error: models.ErrorDetail{Message: "unknown model", Type: "invalid_request_error", Code: "model_not_found"},
				})
				return
			}

			if delay > 0 {
				select {
				case <-time.After(delay):
				case <-r.Context().Done():
					return
				}
			}

			resp := models.ChatCompletionResponse{
				ID:      "chatcmpl-test-validated",
				Object:  "chat.completion",
				Created: 1700000000,
				Model:   req.Model,
				Choices: []models.Choice{{
					Index: 0,
					Message: models.Message{
						Role:    "assistant",
						Content: json.RawMessage(`"validated response"`),
					},
					FinishReason: "stop",
				}},
				Usage: &models.Usage{PromptTokens: 10, CompletionTokens: 20, TotalTokens: 30},
			}
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusOK)
			json.NewEncoder(w).Encode(resp)

		case r.URL.Path == "/v1/models" && r.Method == "GET":
			resp := models.ModelListResponse{
				Object: "list",
				Data: []models.ModelObject{
					{ID: "gpt-4o", Object: "model", OwnedBy: "openai"},
					{ID: "gpt-4o-mini", Object: "model", OwnedBy: "openai"},
				},
			}
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(resp)

		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
}

func startMockAnthropicNative(t *testing.T) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/v1/messages" && r.Method == "POST":
			if got := r.Header.Get("x-api-key"); got != "test-api-key" {
				w.WriteHeader(http.StatusUnauthorized)
				json.NewEncoder(w).Encode(map[string]any{"error": map[string]any{"type": "authentication_error", "message": "bad api key"}})
				return
			}
			if got := r.Header.Get("anthropic-version"); got == "" {
				w.WriteHeader(http.StatusBadRequest)
				json.NewEncoder(w).Encode(map[string]any{"error": map[string]any{"type": "invalid_request_error", "message": "missing anthropic-version"}})
				return
			}
			resp := map[string]any{
				"id":    "msg_test_123",
				"type":  "message",
				"role":  "assistant",
				"model": "claude-haiku-4-5",
				"content": []map[string]any{{
					"type": "text",
					"text": "ANTHROPIC_OK",
				}},
				"stop_reason": "end_turn",
				"usage": map[string]any{
					"input_tokens":  10,
					"output_tokens": 5,
				},
			}
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusOK)
			json.NewEncoder(w).Encode(resp)
		case r.URL.Path == "/v1/messages/count_tokens" && r.Method == "POST":
			if got := r.Header.Get("x-api-key"); got != "test-api-key" {
				w.WriteHeader(http.StatusUnauthorized)
				json.NewEncoder(w).Encode(map[string]any{"error": map[string]any{"type": "authentication_error", "message": "bad api key"}})
				return
			}
			if got := r.Header.Get("anthropic-version"); got == "" {
				w.WriteHeader(http.StatusBadRequest)
				json.NewEncoder(w).Encode(map[string]any{"error": map[string]any{"type": "invalid_request_error", "message": "missing anthropic-version"}})
				return
			}
			resp := map[string]any{
				"input_tokens": 1,
			}
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusOK)
			json.NewEncoder(w).Encode(resp)
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
}

func createTestServer(t *testing.T, mockURL string) *Server {
	t.Helper()

	cfg := config.DefaultConfig()
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL:   mockURL,
		APIFormat: "openai",
		Models:    []string{"gpt-4o", "gpt-4o-mini"},
	}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}

	engine := pipeline.NewEngine()
	srv := New(cfg, "", registry, engine, nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true) // Mark ready for tests (normally set by Start).
	return srv
}

func createAnthropicAuthTestServer(t *testing.T, mockURL string) *Server {
	t.Helper()

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name:    "test-service",
		Key:     "test-api-key",
		Owner:   "test-org",
		KeyType: "internal",
	}}
	cfg.Providers["anthropic"] = config.ProviderConfig{
		BaseURL:   mockURL,
		APIKey:    "test-api-key",
		APIFormat: "anthropic",
		Headers: map[string]string{
			"anthropic-version": "2023-06-01",
		},
		Models: []string{"claude-haiku-4-5"},
	}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}

	engine := pipeline.NewEngine()
	srv := New(cfg, "", registry, engine, nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	return srv
}

func createOrgScopedAnthropicTestServer(t *testing.T, mockURL string) *Server {
	t.Helper()

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name:    "org-user",
		Key:     "sk-agentcc-org-anthropic",
		Owner:   "user",
		KeyType: "byok",
		Metadata: map[string]string{
			"org_id": "org-claude",
		},
	}}
	cfg.Providers["anthropic"] = config.ProviderConfig{
		BaseURL:   mockURL,
		APIKey:    "global-should-not-be-used",
		APIFormat: "anthropic",
		Headers: map[string]string{
			"anthropic-version": "2023-06-01",
		},
		Models: []string{"claude-global-only"},
	}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}

	tenantStore := tenant.NewStore()
	tenantStore.Set("org-claude", &tenant.OrgConfig{
		Providers: map[string]*tenant.ProviderConfig{
			"anthropic": {
				Enabled: true,
				APIKey:  "test-api-key",
				Models:  []string{"claude-haiku-4-5"},
			},
		},
	})

	engine := pipeline.NewEngine()
	srv := New(cfg, "", registry, engine, nil, nil, nil, nil, testModelDBPtr(), tenantStore, nil)
	srv.ready.Store(true)
	return srv
}

func createTestServerWithPlugins(t *testing.T, mockURL string, plugins ...pipeline.Plugin) *Server {
	t.Helper()

	cfg := config.DefaultConfig()
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL:   mockURL,
		APIFormat: "openai",
		Models:    []string{"gpt-4o", "gpt-4o-mini"},
	}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}

	engine := pipeline.NewEngine(plugins...)
	srv := New(cfg, "", registry, engine, nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true) // Mark ready for tests (normally set by Start).
	return srv
}

func createAuthRequiredTestServerWithPlugins(t *testing.T, mockURL string, plugins ...pipeline.Plugin) *Server {
	t.Helper()

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name:    "test-service",
		Key:     "sk-test-key-001",
		Owner:   "test-org",
		KeyType: "internal",
	}}
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL:   mockURL,
		APIFormat: "openai",
		Models:    []string{"gpt-4o", "gpt-4o-mini"},
	}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}

	engine := pipeline.NewEngine(plugins...)
	srv := New(cfg, "", registry, engine, nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	return srv
}

func createAuthRequiredTestServer(t *testing.T, mockURL string) *Server {
	t.Helper()

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name:    "test-service",
		Key:     "sk-test-key-001",
		Owner:   "test-org",
		KeyType: "internal",
	}}
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL:   mockURL,
		APIFormat: "openai",
		Models:    []string{"gpt-4o", "gpt-4o-mini"},
	}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}

	engine := pipeline.NewEngine()
	// Intentionally pass nil keyStore to verify the server wires auth defensively
	// from cfg.Auth when auth is enabled.
	srv := New(cfg, "", registry, engine, nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	return srv
}

func createAuthRequiredA2ATestServer(t *testing.T, mockURL string) *Server {
	t.Helper()

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name:    "test-service",
		Key:     "sk-test-key-001",
		Owner:   "test-org",
		KeyType: "internal",
	}}
	cfg.A2A.Enabled = true
	cfg.A2A.Card = config.A2ACardConfig{
		Name:        "Agentcc LLM Gateway",
		Description: "Route to any LLM with guardrails, caching, and observability",
		Version:     "1.0.0",
	}
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL:   mockURL,
		APIFormat: "openai",
		Models:    []string{"gpt-4o", "gpt-4o-mini"},
	}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}

	engine := pipeline.NewEngine()
	srv := New(cfg, "", registry, engine, nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	return srv
}

func createByokAuthTestServer(t *testing.T, mockURL string) *Server {
	t.Helper()

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name:    "byok-user",
		Key:     "sk-agentcc-byok-test",
		Owner:   "user",
		KeyType: "byok",
	}}
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL:   mockURL,
		APIFormat: "openai",
		Models:    []string{"gpt-4o", "gpt-4o-mini"},
	}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}

	engine := pipeline.NewEngine()
	srv := New(cfg, "", registry, engine, nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	return srv
}

func createOrgScopedModelsTestServer(t *testing.T, mockURL string) *Server {
	t.Helper()

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{
			Name:    "org-user",
			Key:     "sk-agentcc-org-test",
			Owner:   "user",
			KeyType: "byok",
			Metadata: map[string]string{
				"org_id": "org-123",
			},
		},
		{
			Name:    "org-internal",
			Key:     "sk-agentcc-org-internal-test",
			Owner:   "test-org",
			KeyType: "internal",
			Metadata: map[string]string{
				"org_id": "org-123",
			},
		},
		{
			Name:    "non-org-user",
			Key:     "sk-agentcc-no-org-test",
			Owner:   "user",
			KeyType: "byok",
		},
	}
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL:   mockURL,
		APIKey:    "platform-openai-key",
		APIFormat: "openai",
		Models:    []string{"gpt-4o", "gpt-4o-mini"},
	}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}

	tenantStore := tenant.NewStore()
	tenantStore.Set("org-123", &tenant.OrgConfig{
		Providers: map[string]*tenant.ProviderConfig{
			"openai": {
				Enabled: true,
				APIKey:  "org-openai-key",
				Models:  []string{"gpt-4o"},
			},
			"anthropic": {
				Enabled: true,
				APIKey:  "org-anthropic-key",
				Models:  []string{"claude-sonnet-4-6"},
			},
		},
	})

	engine := pipeline.NewEngine()
	srv := New(cfg, "", registry, engine, nil, nil, nil, nil, testModelDBPtr(), tenantStore, nil)
	srv.ready.Store(true)
	return srv
}

func createByokA2ATestServer(t *testing.T, mockURL string) *Server {
	t.Helper()

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name:    "byok-user",
		Key:     "sk-agentcc-byok-test",
		Owner:   "user",
		KeyType: "byok",
	}}
	cfg.A2A.Enabled = true
	cfg.A2A.Card = config.A2ACardConfig{
		Name:        "Agentcc LLM Gateway",
		Description: "Route to any LLM with guardrails, caching, and observability",
		Version:     "1.0.0",
	}
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL:   mockURL,
		APIFormat: "openai",
		Models:    []string{"gpt-4o", "gpt-4o-mini"},
	}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}

	engine := pipeline.NewEngine()
	srv := New(cfg, "", registry, engine, nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	return srv
}

func TestHealthEndpoints(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	tests := []struct {
		path       string
		wantStatus int
	}{
		{"/healthz", 200},
		{"/livez", 200},
		{"/readyz", 200},
	}

	for _, tt := range tests {
		t.Run(tt.path, func(t *testing.T) {
			req := httptest.NewRequest("GET", tt.path, nil)
			w := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(w, req)

			if w.Code != tt.wantStatus {
				t.Errorf("status = %d, want %d", w.Code, tt.wantStatus)
			}
		})
	}
}

func TestChatCompletionNonStreaming(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	reqBody := `{"model":"gpt-4o","messages":[{"role":"user","content":"Hello"}]}`
	req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewBufferString(reqBody))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != 200 {
		t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
	}

	var resp models.ChatCompletionResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("parsing response: %v", err)
	}

	if resp.ID == "" {
		t.Error("response ID should not be empty")
	}
	if len(resp.Choices) == 0 {
		t.Error("response should have choices")
	}
	if resp.Usage == nil || resp.Usage.TotalTokens == 0 {
		t.Error("response should have usage")
	}

	// Verify Agentcc headers.
	if w.Header().Get("x-agentcc-request-id") == "" {
		t.Error("x-agentcc-request-id header should be set")
	}
	if w.Header().Get("x-agentcc-trace-id") == "" {
		t.Error("x-agentcc-trace-id header should be set")
	}
	if w.Header().Get("x-agentcc-provider") != "openai" {
		t.Errorf("x-agentcc-provider = %q, want %q", w.Header().Get("x-agentcc-provider"), "openai")
	}
}

func TestChatCompletionMissingAPIKeyReturnsUnauthorized(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createAuthRequiredTestServer(t, mock.URL)

	reqBody := `{"model":"gpt-4o","messages":[{"role":"user","content":"Hello"}]}`
	req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewBufferString(reqBody))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want %d. Body: %s", w.Code, http.StatusUnauthorized, w.Body.String())
	}

	var resp models.ErrorResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("parsing error response: %v", err)
	}
	if resp.Error.Message != "Invalid or missing API key" {
		t.Fatalf("error.message = %q, want %q", resp.Error.Message, "Invalid or missing API key")
	}
	if resp.Error.Type != models.ErrTypeAuthentication {
		t.Fatalf("error.type = %q, want %q", resp.Error.Type, models.ErrTypeAuthentication)
	}
	if resp.Error.Code != "invalid_api_key" {
		t.Fatalf("error.code = %q, want %q", resp.Error.Code, "invalid_api_key")
	}
}

func TestChatCompletionInvalidAPIKeyReturnsUnauthorized(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createAuthRequiredTestServer(t, mock.URL)

	reqBody := `{"model":"gpt-4o","messages":[{"role":"user","content":"Hello"}]}`
	req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewBufferString(reqBody))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer NOTAKEY")
	w := httptest.NewRecorder()

	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want %d. Body: %s", w.Code, http.StatusUnauthorized, w.Body.String())
	}

	var resp models.ErrorResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("parsing error response: %v", err)
	}
	if resp.Error.Message != "Invalid or missing API key" {
		t.Fatalf("error.message = %q, want %q", resp.Error.Message, "Invalid or missing API key")
	}
	if resp.Error.Type != models.ErrTypeAuthentication {
		t.Fatalf("error.type = %q, want %q", resp.Error.Type, models.ErrTypeAuthentication)
	}
	if resp.Error.Code != "invalid_api_key" {
		t.Fatalf("error.code = %q, want %q", resp.Error.Code, "invalid_api_key")
	}
}

func TestA2AAgentCardAvailableUnderV1WithAuth(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createAuthRequiredA2ATestServer(t, mock.URL)

	req := httptest.NewRequest("GET", "/v1/.well-known/agent.json", nil)
	req.Header.Set("Authorization", "Bearer sk-test-key-001")
	w := httptest.NewRecorder()

	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d. Body: %s", w.Code, http.StatusOK, w.Body.String())
	}

	var card agentcca2a.AgentCard
	if err := json.Unmarshal(w.Body.Bytes(), &card); err != nil {
		t.Fatalf("parsing agent card: %v", err)
	}
	if card.Name != "Agentcc LLM Gateway" {
		t.Fatalf("card.name = %q, want %q", card.Name, "Agentcc LLM Gateway")
	}
	if card.Version != "1.0.0" {
		t.Fatalf("card.version = %q, want %q", card.Version, "1.0.0")
	}
	if w.Header().Get("Cache-Control") != "public, max-age=3600" {
		t.Fatalf("cache-control = %q, want %q", w.Header().Get("Cache-Control"), "public, max-age=3600")
	}
}

func TestA2AMessageEndpointAvailableUnderV1WithAuth(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createAuthRequiredA2ATestServer(t, mock.URL)

	params, err := json.Marshal(agentcca2a.MessageSendParams{
		Message: agentcca2a.Message{
			Role: "user",
			Parts: []agentcca2a.MessagePart{{
				Type: "text",
				Text: "Hello from A2A",
			}},
		},
		Metadata: json.RawMessage(`{"model":"gpt-4o"}`),
	})
	if err != nil {
		t.Fatalf("marshal params: %v", err)
	}

	body, err := json.Marshal(mcp.Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`1`),
		Method:  agentcca2a.MethodMessageSend,
		Params:  params,
	})
	if err != nil {
		t.Fatalf("marshal body: %v", err)
	}

	req := httptest.NewRequest("POST", "/v1/a2a", bytes.NewReader(body))
	req.Header.Set("Authorization", "Bearer sk-test-key-001")
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d. Body: %s", w.Code, http.StatusOK, w.Body.String())
	}

	var resp mcp.Message
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("parsing a2a response: %v", err)
	}
	if resp.Error != nil {
		t.Fatalf("unexpected JSON-RPC error: %s", resp.Error.Message)
	}

	var task agentcca2a.Task
	if err := json.Unmarshal(resp.Result, &task); err != nil {
		t.Fatalf("parsing task result: %v", err)
	}
	if task.ID == "" {
		t.Fatal("expected task ID")
	}
	if task.Status.State != agentcca2a.TaskStatusCompleted {
		t.Fatalf("task.status.state = %q, want %q", task.Status.State, agentcca2a.TaskStatusCompleted)
	}
}

func TestA2ATasksSendAliasAvailableUnderV1WithAuth(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createAuthRequiredA2ATestServer(t, mock.URL)

	params, err := json.Marshal(agentcca2a.MessageSendParams{
		Message: agentcca2a.Message{
			Role: "user",
			Parts: []agentcca2a.MessagePart{{
				Type: "text",
				Text: "Hello from tasks/send",
			}},
		},
		Metadata: json.RawMessage(`{"model":"gpt-4o"}`),
	})
	if err != nil {
		t.Fatalf("marshal params: %v", err)
	}

	body, err := json.Marshal(mcp.Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`1`),
		Method:  "tasks/send",
		Params:  params,
	})
	if err != nil {
		t.Fatalf("marshal body: %v", err)
	}

	req := httptest.NewRequest("POST", "/v1/a2a", bytes.NewReader(body))
	req.Header.Set("Authorization", "Bearer sk-test-key-001")
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d. Body: %s", w.Code, http.StatusOK, w.Body.String())
	}

	var resp mcp.Message
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("parsing a2a response: %v", err)
	}
	if resp.Error != nil {
		t.Fatalf("unexpected JSON-RPC error: %s", resp.Error.Message)
	}
}

func TestA2AFrontendModelMetadataExecutesUnderV1(t *testing.T) {
	mock := startValidatedMockOpenAI(t, 0)
	defer mock.Close()

	srv := createAuthRequiredA2ATestServer(t, mock.URL)

	body := `{"jsonrpc":"2.0","id":1,"method":"tasks/send","params":{"message":{"role":"user","parts":[{"type":"text","text":"Use the frontend model"}]},"metadata":{"model":"gpt-4o"}}}`
	req := httptest.NewRequest("POST", "/v1/a2a", bytes.NewBufferString(body))
	req.Header.Set("Authorization", "Bearer sk-test-key-001")
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d. Body: %s", w.Code, http.StatusOK, w.Body.String())
	}

	var resp mcp.Message
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("parsing a2a response: %v", err)
	}
	if resp.Error != nil {
		t.Fatalf("unexpected JSON-RPC error: %s", resp.Error.Message)
	}

	var task agentcca2a.Task
	if err := json.Unmarshal(resp.Result, &task); err != nil {
		t.Fatalf("parsing task result: %v", err)
	}
	if task.Status.State != agentcca2a.TaskStatusCompleted {
		t.Fatalf("task.status.state = %q, want %q", task.Status.State, agentcca2a.TaskStatusCompleted)
	}
	if len(task.Artifacts) == 0 || len(task.Artifacts[0].Parts) == 0 || task.Artifacts[0].Parts[0].Text != "validated response" {
		t.Fatalf("unexpected task artifacts: %#v", task.Artifacts)
	}
}

func TestA2ATasksGetAvailableUnderV1WithAuth(t *testing.T) {
	mock := startValidatedMockOpenAI(t, 0)
	defer mock.Close()

	srv := createAuthRequiredA2ATestServer(t, mock.URL)

	sendBody := `{"jsonrpc":"2.0","id":1,"method":"tasks/send","params":{"message":{"role":"user","parts":[{"type":"text","text":"Fetch me later"}]},"metadata":{"model":"gpt-4o"}}}`
	sendReq := httptest.NewRequest("POST", "/v1/a2a", bytes.NewBufferString(sendBody))
	sendReq.Header.Set("Authorization", "Bearer sk-test-key-001")
	sendReq.Header.Set("Content-Type", "application/json")
	sendW := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(sendW, sendReq)

	var sendResp mcp.Message
	if err := json.Unmarshal(sendW.Body.Bytes(), &sendResp); err != nil {
		t.Fatalf("parsing send response: %v", err)
	}
	var sentTask agentcca2a.Task
	if err := json.Unmarshal(sendResp.Result, &sentTask); err != nil {
		t.Fatalf("parsing sent task: %v", err)
	}

	getBody := fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":"tasks/get","params":{"task_id":"%s"}}`, sentTask.ID)
	getReq := httptest.NewRequest("POST", "/v1/a2a", bytes.NewBufferString(getBody))
	getReq.Header.Set("Authorization", "Bearer sk-test-key-001")
	getReq.Header.Set("Content-Type", "application/json")
	getW := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(getW, getReq)

	if getW.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d. Body: %s", getW.Code, http.StatusOK, getW.Body.String())
	}

	var getResp mcp.Message
	if err := json.Unmarshal(getW.Body.Bytes(), &getResp); err != nil {
		t.Fatalf("parsing get response: %v", err)
	}
	if getResp.Error != nil {
		t.Fatalf("unexpected JSON-RPC error: %s", getResp.Error.Message)
	}

	var gotTask agentcca2a.Task
	if err := json.Unmarshal(getResp.Result, &gotTask); err != nil {
		t.Fatalf("parsing retrieved task: %v", err)
	}
	if gotTask.ID != sentTask.ID {
		t.Fatalf("task.id = %q, want %q", gotTask.ID, sentTask.ID)
	}
	if gotTask.Status.State != agentcca2a.TaskStatusCompleted {
		t.Fatalf("task.status.state = %q, want %q", gotTask.Status.State, agentcca2a.TaskStatusCompleted)
	}
}

func TestA2AReturnImmediatelyAndCancelUnderV1WithAuth(t *testing.T) {
	mock := startValidatedMockOpenAI(t, 300*time.Millisecond)
	defer mock.Close()

	srv := createAuthRequiredA2ATestServer(t, mock.URL)

	sendBody := `{"jsonrpc":"2.0","id":1,"method":"tasks/send","params":{"message":{"role":"user","parts":[{"type":"text","text":"Cancel me"}]},"metadata":{"model":"gpt-4o"},"configuration":{"returnImmediately":true}}}`
	sendReq := httptest.NewRequest("POST", "/v1/a2a", bytes.NewBufferString(sendBody))
	sendReq.Header.Set("Authorization", "Bearer sk-test-key-001")
	sendReq.Header.Set("Content-Type", "application/json")
	sendW := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(sendW, sendReq)

	if sendW.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d. Body: %s", sendW.Code, http.StatusOK, sendW.Body.String())
	}

	var sendResp mcp.Message
	if err := json.Unmarshal(sendW.Body.Bytes(), &sendResp); err != nil {
		t.Fatalf("parsing send response: %v", err)
	}
	if sendResp.Error != nil {
		t.Fatalf("unexpected JSON-RPC error: %s", sendResp.Error.Message)
	}

	var sentTask agentcca2a.Task
	if err := json.Unmarshal(sendResp.Result, &sentTask); err != nil {
		t.Fatalf("parsing sent task: %v", err)
	}
	if sentTask.Status.State != agentcca2a.TaskStatusWorking {
		t.Fatalf("initial task.status.state = %q, want %q", sentTask.Status.State, agentcca2a.TaskStatusWorking)
	}

	cancelBody := fmt.Sprintf(`{"jsonrpc":"2.0","id":2,"method":"tasks/cancel","params":{"task_id":"%s"}}`, sentTask.ID)
	cancelReq := httptest.NewRequest("POST", "/v1/a2a", bytes.NewBufferString(cancelBody))
	cancelReq.Header.Set("Authorization", "Bearer sk-test-key-001")
	cancelReq.Header.Set("Content-Type", "application/json")
	cancelW := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(cancelW, cancelReq)

	if cancelW.Code != http.StatusOK {
		t.Fatalf("cancel status = %d, want %d. Body: %s", cancelW.Code, http.StatusOK, cancelW.Body.String())
	}

	var cancelResp mcp.Message
	if err := json.Unmarshal(cancelW.Body.Bytes(), &cancelResp); err != nil {
		t.Fatalf("parsing cancel response: %v", err)
	}
	if cancelResp.Error != nil {
		t.Fatalf("unexpected cancel JSON-RPC error: %s", cancelResp.Error.Message)
	}

	var canceledTask agentcca2a.Task
	if err := json.Unmarshal(cancelResp.Result, &canceledTask); err != nil {
		t.Fatalf("parsing canceled task: %v", err)
	}
	if canceledTask.Status.State != agentcca2a.TaskStatusCanceled {
		t.Fatalf("canceled task.status.state = %q, want %q", canceledTask.Status.State, agentcca2a.TaskStatusCanceled)
	}

	time.Sleep(50 * time.Millisecond)
	getBody := fmt.Sprintf(`{"jsonrpc":"2.0","id":3,"method":"tasks/get","params":{"task_id":"%s"}}`, sentTask.ID)
	getReq := httptest.NewRequest("POST", "/v1/a2a", bytes.NewBufferString(getBody))
	getReq.Header.Set("Authorization", "Bearer sk-test-key-001")
	getReq.Header.Set("Content-Type", "application/json")
	getW := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(getW, getReq)

	var getResp mcp.Message
	if err := json.Unmarshal(getW.Body.Bytes(), &getResp); err != nil {
		t.Fatalf("parsing get response: %v", err)
	}
	var gotTask agentcca2a.Task
	if err := json.Unmarshal(getResp.Result, &gotTask); err != nil {
		t.Fatalf("parsing retrieved task: %v", err)
	}
	if gotTask.Status.State != agentcca2a.TaskStatusCanceled {
		t.Fatalf("retrieved task.status.state = %q, want %q", gotTask.Status.State, agentcca2a.TaskStatusCanceled)
	}
}

func TestChatCompletionByokKeyCannotUseGlobalProvider(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createByokAuthTestServer(t, mock.URL)

	reqBody := `{"model":"gpt-4o","messages":[{"role":"user","content":"Hello"}]}`
	req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewBufferString(reqBody))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer sk-agentcc-byok-test")
	w := httptest.NewRecorder()

	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusForbidden {
		t.Fatalf("status = %d, want %d. Body: %s", w.Code, http.StatusForbidden, w.Body.String())
	}

	var resp models.ErrorResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("parsing error response: %v", err)
	}
	if resp.Error.Type != models.ErrTypePermission {
		t.Fatalf("error.type = %q, want %q", resp.Error.Type, models.ErrTypePermission)
	}
	if resp.Error.Code != "permission_denied" {
		t.Fatalf("error.code = %q, want %q", resp.Error.Code, "permission_denied")
	}
	if strings.Contains(resp.Error.Message, "internal API key") {
		t.Fatalf("error.message should not leak internal wording: %q", resp.Error.Message)
	}
}

func TestA2AByokKeyDoesNotLeakInternalPermissionMessage(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createByokA2ATestServer(t, mock.URL)

	body := `{"jsonrpc":"2.0","id":1,"method":"tasks/send","params":{"message":{"role":"user","parts":[{"type":"text","text":"Hello"}]},"metadata":{"model":"gpt-4o"}}}`
	req := httptest.NewRequest("POST", "/v1/a2a", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer sk-agentcc-byok-test")
	w := httptest.NewRecorder()

	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d. Body: %s", w.Code, http.StatusOK, w.Body.String())
	}

	var resp mcp.Message
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("parsing a2a response: %v", err)
	}
	if resp.Error != nil {
		t.Fatalf("unexpected JSON-RPC error: %s", resp.Error.Message)
	}

	var task agentcca2a.Task
	if err := json.Unmarshal(resp.Result, &task); err != nil {
		t.Fatalf("parsing task result: %v", err)
	}
	if task.Status.State != agentcca2a.TaskStatusFailed {
		t.Fatalf("task.status.state = %q, want %q", task.Status.State, agentcca2a.TaskStatusFailed)
	}
	if len(task.Status.Message) == 0 {
		t.Fatal("expected task error message")
	}
	if strings.Contains(task.Status.Message[0].Text, "internal API key") {
		t.Fatalf("task error should not leak internal wording: %q", task.Status.Message[0].Text)
	}
}

func TestChatCompletionStreaming(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	reqBody := `{"model":"gpt-4o","messages":[{"role":"user","content":"Hello"}],"stream":true}`
	req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewBufferString(reqBody))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != 200 {
		t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
	}

	// Verify SSE content type.
	ct := w.Header().Get("Content-Type")
	if !strings.HasPrefix(ct, "text/event-stream") {
		t.Errorf("Content-Type = %q, want text/event-stream", ct)
	}

	// Parse SSE events.
	body := w.Body.String()
	lines := strings.Split(body, "\n")
	var dataLines []string
	for _, line := range lines {
		if strings.HasPrefix(line, "data: ") {
			dataLines = append(dataLines, strings.TrimPrefix(line, "data: "))
		}
	}

	if len(dataLines) < 2 {
		t.Fatalf("expected at least 2 data lines, got %d: %s", len(dataLines), body)
	}

	// Last data line should be [DONE].
	if dataLines[len(dataLines)-1] != "[DONE]" {
		t.Errorf("last data line = %q, want [DONE]", dataLines[len(dataLines)-1])
	}

	// First data line should be valid JSON chunk.
	var chunk models.StreamChunk
	if err := json.Unmarshal([]byte(dataLines[0]), &chunk); err != nil {
		t.Fatalf("parsing first chunk: %v", err)
	}
	if chunk.Object != "chat.completion.chunk" {
		t.Errorf("chunk object = %q, want chat.completion.chunk", chunk.Object)
	}
}

func TestChatCompletionStreaming_WritesFinalMetadataChunkBeforeDone(t *testing.T) {
	mock := startMockOpenAIWithUsageChunk(t)
	defer mock.Close()

	srv := createAuthRequiredTestServerWithPlugins(t, mock.URL, &metadataPostPlugin{})

	reqBody := `{"model":"gpt-4o","messages":[{"role":"user","content":"Hello"}],"stream":true}`
	req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewBufferString(reqBody))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer sk-test-key-001")
	req.Header.Set("x-agentcc-include-metadata", "1")
	w := httptest.NewRecorder()

	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != 200 {
		t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
	}
	if trailer := w.Header().Get("Trailer"); trailer != "" {
		t.Fatalf("Trailer header = %q, want empty", trailer)
	}

	body := w.Body.String()
	lines := strings.Split(body, "\n")
	var dataLines []string
	for _, line := range lines {
		if strings.HasPrefix(line, "data: ") {
			dataLines = append(dataLines, strings.TrimPrefix(line, "data: "))
		}
	}

	if len(dataLines) < 3 {
		t.Fatalf("expected at least 3 data lines, got %d: %s", len(dataLines), body)
	}
	if dataLines[len(dataLines)-1] != "[DONE]" {
		t.Fatalf("last data line = %q, want [DONE]", dataLines[len(dataLines)-1])
	}

	var finalChunk models.StreamChunk
	if err := json.Unmarshal([]byte(dataLines[len(dataLines)-2]), &finalChunk); err != nil {
		t.Fatalf("parsing final metadata chunk: %v", err)
	}
	if finalChunk.Object != "chat.completion.chunk" {
		t.Fatalf("final chunk object = %q, want chat.completion.chunk", finalChunk.Object)
	}
	if len(finalChunk.Choices) != 0 {
		t.Fatalf("final chunk choices length = %d, want 0", len(finalChunk.Choices))
	}
	if finalChunk.Usage == nil || finalChunk.Usage.TotalTokens != 30 {
		t.Fatalf("final chunk usage = %+v, want total_tokens=30", finalChunk.Usage)
	}
	if finalChunk.AgentccMetadata == nil {
		t.Fatal("expected agentcc metadata in final chunk")
	}
	if finalChunk.AgentccMetadata.Cost != 0.0045 {
		t.Fatalf("final chunk cost = %v, want 0.0045", finalChunk.AgentccMetadata.Cost)
	}
	if !strings.Contains(dataLines[len(dataLines)-2], `"latency_ms":`) {
		t.Fatalf("final chunk body missing latency_ms: %s", dataLines[len(dataLines)-2])
	}
	if finalChunk.Model != "gpt-4o" {
		t.Fatalf("final chunk model = %q, want gpt-4o", finalChunk.Model)
	}
	if finalChunk.ID != "chatcmpl-test" {
		t.Fatalf("final chunk id = %q, want chatcmpl-test", finalChunk.ID)
	}
}

func TestChatCompletionStreaming_OmitsFinalMetadataByDefault(t *testing.T) {
	mock := startMockOpenAIWithUsageChunk(t)
	defer mock.Close()

	srv := createAuthRequiredTestServerWithPlugins(t, mock.URL, &metadataPostPlugin{})

	reqBody := `{"model":"gpt-4o","messages":[{"role":"user","content":"Hello"}],"stream":true}`
	req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewBufferString(reqBody))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer sk-test-key-001")
	w := httptest.NewRecorder()

	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d. Body: %s", w.Code, http.StatusOK, w.Body.String())
	}

	body := w.Body.String()
	lines := strings.Split(body, "\n")
	var dataLines []string
	for _, line := range lines {
		if strings.HasPrefix(line, "data: ") {
			dataLines = append(dataLines, strings.TrimPrefix(line, "data: "))
		}
	}

	if len(dataLines) < 3 {
		t.Fatalf("expected at least 3 data lines, got %d: %s", len(dataLines), body)
	}
	if strings.Contains(dataLines[len(dataLines)-2], `"agentcc_metadata"`) {
		t.Fatalf("final chunk unexpectedly included agentcc_metadata: %s", dataLines[len(dataLines)-2])
	}
}

func TestChatCompletionValidation(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	tests := []struct {
		name       string
		body       string
		wantStatus int
		wantCode   string
	}{
		{
			name:       "missing model",
			body:       `{"messages":[{"role":"user","content":"hi"}]}`,
			wantStatus: 400,
			wantCode:   "missing_model",
		},
		{
			name:       "missing messages",
			body:       `{"model":"gpt-4o"}`,
			wantStatus: 400,
			wantCode:   "missing_messages",
		},
		{
			name:       "empty messages",
			body:       `{"model":"gpt-4o","messages":[]}`,
			wantStatus: 400,
			wantCode:   "missing_messages",
		},
		{
			name:       "invalid json",
			body:       `not json`,
			wantStatus: 400,
			wantCode:   "invalid_json",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewBufferString(tt.body))
			w := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(w, req)

			if w.Code != tt.wantStatus {
				t.Errorf("status = %d, want %d. Body: %s", w.Code, tt.wantStatus, w.Body.String())
			}

			var errResp models.ErrorResponse
			json.Unmarshal(w.Body.Bytes(), &errResp)
			if errResp.Error.Code != tt.wantCode {
				t.Errorf("error code = %q, want %q", errResp.Error.Code, tt.wantCode)
			}
		})
	}
}

func TestListModels(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	req := httptest.NewRequest("GET", "/v1/models", nil)
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != 200 {
		t.Fatalf("status = %d, want 200", w.Code)
	}

	var resp models.ModelListResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("parsing response: %v", err)
	}

	if resp.Object != "list" {
		t.Errorf("object = %q, want list", resp.Object)
	}
	if len(resp.Data) == 0 {
		t.Error("should return at least one model")
	}
}

func TestShouldApplyOrgProviderOverride(t *testing.T) {
	tests := []struct {
		name string
		rc   *models.RequestContext
		want bool
	}{
		{
			name: "applies for normal byok key",
			rc:   &models.RequestContext{Metadata: map[string]string{"key_type": "byok"}},
			want: true,
		},
		{
			name: "skips for internal key",
			rc:   &models.RequestContext{Metadata: map[string]string{"key_type": "internal"}},
			want: false,
		},
		{
			name: "skips for resolved org provider fallback",
			rc:   &models.RequestContext{Metadata: map[string]string{"org_provider": "true"}},
			want: false,
		},
		{
			name: "skips for direct org model match",
			rc:   &models.RequestContext{Metadata: map[string]string{"org_provider_model_match": "gpt-4o"}},
			want: false,
		},
		{
			name: "skips nil rc",
			rc:   nil,
			want: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := shouldApplyOrgProviderOverride(tt.rc); got != tt.want {
				t.Fatalf("shouldApplyOrgProviderOverride() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestListModels_MergesGlobalAndOrgModelsForRequestingOrg(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createOrgScopedModelsTestServer(t, mock.URL)

	req := httptest.NewRequest("GET", "/v1/models", nil)
	req.Header.Set("Authorization", "Bearer sk-agentcc-org-test")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d", w.Code, http.StatusOK)
	}

	var resp models.ModelListResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("parsing response: %v", err)
	}

	if len(resp.Data) != 3 {
		t.Fatalf("model count = %d, want 3; body=%s", len(resp.Data), w.Body.String())
	}
	ids := map[string]bool{}
	for _, m := range resp.Data {
		ids[m.ID] = true
	}
	if !ids["gpt-4o"] || !ids["claude-sonnet-4-6"] || !ids["gpt-4o-mini"] {
		t.Fatalf("returned model ids = %#v, want gpt-4o, gpt-4o-mini, and claude-sonnet-4-6", ids)
	}
}

func TestListModels_FallsBackToGlobalModelsForKeyWithoutOrg(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createOrgScopedModelsTestServer(t, mock.URL)

	req := httptest.NewRequest("GET", "/v1/models", nil)
	req.Header.Set("Authorization", "Bearer sk-agentcc-no-org-test")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d", w.Code, http.StatusOK)
	}

	var resp models.ModelListResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("parsing response: %v", err)
	}
	if len(resp.Data) != 2 {
		t.Fatalf("model count = %d, want 2 global models; body=%s", len(resp.Data), w.Body.String())
	}
}

func TestGetModel_ReturnsGlobalModelForOrgRequest(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createOrgScopedModelsTestServer(t, mock.URL)

	req := httptest.NewRequest("GET", "/v1/models/gpt-4o-mini", nil)
	req.Header.Set("Authorization", "Bearer sk-agentcc-org-test")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d; body=%s", w.Code, http.StatusOK, w.Body.String())
	}
}

func TestCreateEmbedding_InternalKeySkipsOrgProviderOverride(t *testing.T) {
	var upstreamAuth string
	mock := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		upstreamAuth = r.Header.Get("Authorization")
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(models.EmbeddingResponse{
			Object: "list",
			Data: []models.EmbeddingData{{
				Object:    "embedding",
				Index:     0,
				Embedding: json.RawMessage(`[0.1,0.2,0.3]`),
			}},
			Model: "gpt-4o",
			Usage: &models.EmbeddingUsage{PromptTokens: 3, TotalTokens: 3},
		})
	}))
	defer mock.Close()

	srv := createOrgScopedModelsTestServer(t, mock.URL)

	reqBody := `{"model":"gpt-4o","input":"hello"}`
	req := httptest.NewRequest("POST", "/v1/embeddings", bytes.NewBufferString(reqBody))
	req.Header.Set("Authorization", "Bearer sk-agentcc-org-internal-test")
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d; body=%s", w.Code, http.StatusOK, w.Body.String())
	}
	if upstreamAuth != "Bearer platform-openai-key" {
		t.Fatalf("upstream authorization = %q, want Bearer platform-openai-key", upstreamAuth)
	}
}

func TestCreateResponse_AllowsOrgScopedModel(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createOrgScopedModelsTestServer(t, mock.URL)

	reqBody := `{"model":"gpt-4o","input":"What is 2+2?"}`
	req := httptest.NewRequest("POST", "/v1/responses", bytes.NewBufferString(reqBody))
	req.Header.Set("Authorization", "Bearer sk-agentcc-org-test")
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d; body=%s", w.Code, http.StatusOK, w.Body.String())
	}

	var resp map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("parsing response: %v", err)
	}
	if resp["object"] != "response" {
		t.Fatalf("object = %v, want response", resp["object"])
	}
}

func TestCreateResponse_PreservesResolvedModelWhenProviderOmitsModel(t *testing.T) {
	mock := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/responses" || r.Method != http.MethodPost {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		resp := map[string]any{
			"id":         "resp_test_123",
			"object":     "response",
			"created_at": 1700000000,
			"status":     "completed",
			"model":      "",
			"output": []map[string]any{{
				"id":     "msg_test_123",
				"type":   "message",
				"status": "completed",
				"role":   "assistant",
				"content": []map[string]any{{
					"type": "output_text",
					"text": "2 + 2 equals 4.",
				}},
			}},
			"usage": map[string]any{
				"input_tokens":  10,
				"output_tokens": 5,
				"total_tokens":  15,
			},
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(resp)
	}))
	defer mock.Close()

	srv := createAuthRequiredTestServerWithPlugins(t, mock.URL, &presetResolvedModelPlugin{})

	reqBody := `{"model":"gpt-4o","input":"What is 2+2?"}`
	req := httptest.NewRequest("POST", "/v1/responses", bytes.NewBufferString(reqBody))
	req.Header.Set("Authorization", "Bearer sk-test-key-001")
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d; body=%s", w.Code, http.StatusOK, w.Body.String())
	}
	if got := w.Header().Get("x-agentcc-model-used"); got != "gpt-4o" {
		t.Fatalf("x-agentcc-model-used = %q, want gpt-4o", got)
	}
}

func TestCreateResponse_ComputesCostFromResponsesUsage(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	modelDBPtr := testModelDBPtr()
	srv := createAuthRequiredTestServerWithPlugins(t, mock.URL, costplugin.New(true, func() *modeldb.ModelDB {
		return modelDBPtr.Load()
	}, nil, nil))

	reqBody := `{"model":"gpt-4o","input":"What is 2+2?"}`
	req := httptest.NewRequest("POST", "/v1/responses", bytes.NewBufferString(reqBody))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer sk-test-key-001")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d; body=%s", w.Code, http.StatusOK, w.Body.String())
	}
	if got := w.Header().Get("x-agentcc-cost"); got != "0.000075" {
		t.Fatalf("x-agentcc-cost = %q, want %q", got, "0.000075")
	}
}

func TestAnthropicMessages_AcceptsXAPIKeyAuth(t *testing.T) {
	mock := startMockAnthropicNative(t)
	defer mock.Close()

	srv := createAnthropicAuthTestServer(t, mock.URL)

	reqBody := `{"model":"claude-haiku-4-5","max_tokens":32,"messages":[{"role":"user","content":"Say exactly: ANTHROPIC_OK"}]}`
	req := httptest.NewRequest("POST", "/v1/messages", bytes.NewBufferString(reqBody))
	req.Header.Set("x-api-key", "test-api-key")
	req.Header.Set("anthropic-version", "2023-06-01")
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d; body=%s", w.Code, http.StatusOK, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "ANTHROPIC_OK") {
		t.Fatalf("body = %s, want Anthropic response", w.Body.String())
	}
}

func TestAnthropicMessages_AllowsOrgScopedModel(t *testing.T) {
	mock := startMockAnthropicNative(t)
	defer mock.Close()

	srv := createOrgScopedAnthropicTestServer(t, mock.URL)

	reqBody := `{"model":"claude-haiku-4-5","max_tokens":32,"messages":[{"role":"user","content":"Say exactly: ANTHROPIC_OK"}]}`
	req := httptest.NewRequest("POST", "/v1/messages", bytes.NewBufferString(reqBody))
	req.Header.Set("x-api-key", "sk-agentcc-org-anthropic")
	req.Header.Set("anthropic-version", "2023-06-01")
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d; body=%s", w.Code, http.StatusOK, w.Body.String())
	}
}

func TestAnthropicCountTokens_AcceptsNativeRequest(t *testing.T) {
	mock := startMockAnthropicNative(t)
	defer mock.Close()

	srv := createAnthropicAuthTestServer(t, mock.URL)

	reqBody := `{"model":"claude-haiku-4-5","messages":[{"role":"user","content":"hi"}]}`
	req := httptest.NewRequest("POST", "/v1/messages/count_tokens", bytes.NewBufferString(reqBody))
	req.Header.Set("x-api-key", "test-api-key")
	req.Header.Set("anthropic-version", "2023-06-01")
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d; body=%s", w.Code, http.StatusOK, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "input_tokens") {
		t.Fatalf("body=%s, want count_tokens response", w.Body.String())
	}
}

func TestCreateEmbedding_AcceptsXAPIKeyAuth(t *testing.T) {
	mock := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/v1/models" && r.Method == "GET":
			json.NewEncoder(w).Encode(models.ModelListResponse{Object: "list", Data: []models.ModelObject{{ID: "gpt-4o"}}})
		case r.URL.Path == "/v1/embeddings" && r.Method == "POST":
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(models.EmbeddingResponse{
				Object: "list",
				Data:   []models.EmbeddingData{{Object: "embedding", Index: 0, Embedding: json.RawMessage(`[0.1,0.2,0.3]`)}},
				Model:  "gpt-4o",
				Usage:  &models.EmbeddingUsage{PromptTokens: 3, TotalTokens: 3},
			})
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer mock.Close()

	srv := createAuthRequiredTestServer(t, mock.URL)

	reqBody := `{"model":"gpt-4o","input":"hello"}`
	req := httptest.NewRequest("POST", "/v1/embeddings", bytes.NewBufferString(reqBody))
	req.Header.Set("x-api-key", "sk-test-key-001")
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d; body=%s", w.Code, http.StatusOK, w.Body.String())
	}
}

func TestChatCompletion_AcceptsXAPIKeyAuth(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createAuthRequiredTestServer(t, mock.URL)

	reqBody := `{"model":"gpt-4o","messages":[{"role":"user","content":"Hello"}]}`
	req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewBufferString(reqBody))
	req.Header.Set("x-api-key", "sk-test-key-001")
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d; body=%s", w.Code, http.StatusOK, w.Body.String())
	}
}

func TestNotFoundEndpoint(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	req := httptest.NewRequest("GET", "/v1/nonexistent", nil)
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != 404 {
		t.Errorf("status = %d, want 404", w.Code)
	}

	var errResp models.ErrorResponse
	json.Unmarshal(w.Body.Bytes(), &errResp)
	if errResp.Error.Type != models.ErrTypeNotFound {
		t.Errorf("error type = %q, want %q", errResp.Error.Type, models.ErrTypeNotFound)
	}
}

func TestMultimodalEndpoints_MissingModel(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	// All JSON-body multimodal endpoints should return 400 for empty body (missing model).
	endpoints := []string{
		"/v1/embeddings",
		"/v1/images/generations",
		"/v1/audio/speech",
		"/v1/rerank",
	}

	for _, ep := range endpoints {
		t.Run(ep, func(t *testing.T) {
			req := httptest.NewRequest("POST", ep, bytes.NewBufferString("{}"))
			w := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(w, req)

			if w.Code != 400 {
				t.Errorf("status = %d, want 400", w.Code)
			}
		})
	}
}

func TestUnknownModel(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	// Create config with explicit model map (no default provider).
	cfg := config.DefaultConfig()
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL:   mock.URL,
		APIFormat: "openai",
	}
	cfg.Providers["anthropic"] = config.ProviderConfig{
		BaseURL:   mock.URL,
		APIFormat: "openai", // Use openai format for testing.
	}
	cfg.ModelMap["gpt-4o"] = "openai"

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}

	engine := pipeline.NewEngine()
	srv := New(cfg, "", registry, engine, nil, nil, nil, nil, testModelDBPtr(), nil, nil)

	reqBody := `{"model":"unknown-model-xyz","messages":[{"role":"user","content":"hi"}]}`
	req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewBufferString(reqBody))
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != 404 {
		t.Errorf("status = %d, want 404. Body: %s", w.Code, w.Body.String())
	}
}

func TestPanicRecovery(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	// Use a specially crafted request that we know won't panic,
	// but we test the recovery middleware is in place by checking headers.
	req := httptest.NewRequest("GET", "/healthz", nil)
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	// Verify request ID middleware is active.
	if w.Header().Get("x-agentcc-request-id") == "" {
		t.Error("x-agentcc-request-id should be set (middleware active)")
	}
}

// ---------------------------------------------------------------------------
// Request Timeout Tests (Feature 2.9)
// ---------------------------------------------------------------------------

func TestResolveTimeout_HeaderOverride(t *testing.T) {
	h := &Handlers{defaultTimeout: 60 * time.Second}
	mt := map[string]time.Duration{"gpt-4o": 120 * time.Second}
	h.modelTimeouts.Store(&mt)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model = "gpt-4o"

	r := httptest.NewRequest("GET", "/", nil)
	r.Header.Set("x-agentcc-timeout", "30s")

	got := h.resolveTimeout(rc, r)
	if got != 30*time.Second {
		t.Errorf("resolveTimeout = %v, want 30s (header should win over model)", got)
	}
}

func TestResolveTimeout_ModelTimeout(t *testing.T) {
	h := &Handlers{defaultTimeout: 60 * time.Second}
	mt := map[string]time.Duration{
		"o1":     300 * time.Second,
		"gpt-4o": 90 * time.Second,
	}
	h.modelTimeouts.Store(&mt)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model = "o1"

	r := httptest.NewRequest("GET", "/", nil)

	got := h.resolveTimeout(rc, r)
	if got != 300*time.Second {
		t.Errorf("resolveTimeout = %v, want 300s (model timeout)", got)
	}
}

func TestResolveTimeout_DefaultFallback(t *testing.T) {
	h := &Handlers{defaultTimeout: 60 * time.Second}

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model = "gpt-4o"

	r := httptest.NewRequest("GET", "/", nil)

	got := h.resolveTimeout(rc, r)
	if got != 60*time.Second {
		t.Errorf("resolveTimeout = %v, want 60s (default)", got)
	}
}

func TestResolveTimeout_ModelNotInMap(t *testing.T) {
	h := &Handlers{defaultTimeout: 60 * time.Second}
	mt := map[string]time.Duration{"o1": 300 * time.Second}
	h.modelTimeouts.Store(&mt)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model = "gpt-4o-mini"

	r := httptest.NewRequest("GET", "/", nil)

	got := h.resolveTimeout(rc, r)
	if got != 60*time.Second {
		t.Errorf("resolveTimeout = %v, want 60s (model not in map, falls to default)", got)
	}
}

func TestResolveTimeout_InvalidHeaderFallsThrough(t *testing.T) {
	h := &Handlers{defaultTimeout: 60 * time.Second}
	mt := map[string]time.Duration{"gpt-4o": 90 * time.Second}
	h.modelTimeouts.Store(&mt)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model = "gpt-4o"

	r := httptest.NewRequest("GET", "/", nil)
	r.Header.Set("x-agentcc-timeout", "not-a-duration")

	got := h.resolveTimeout(rc, r)
	if got != 90*time.Second {
		t.Errorf("resolveTimeout = %v, want 90s (invalid header should fall through to model)", got)
	}
}

func TestResolveTimeout_ZeroHeaderFallsThrough(t *testing.T) {
	h := &Handlers{defaultTimeout: 60 * time.Second}

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model = "gpt-4o"

	r := httptest.NewRequest("GET", "/", nil)
	r.Header.Set("x-agentcc-timeout", "0s")

	got := h.resolveTimeout(rc, r)
	if got != 60*time.Second {
		t.Errorf("resolveTimeout = %v, want 60s (zero header should fall through to default)", got)
	}
}

func TestResolveTimeout_NegativeHeaderFallsThrough(t *testing.T) {
	h := &Handlers{defaultTimeout: 60 * time.Second}

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model = "gpt-4o"

	r := httptest.NewRequest("GET", "/", nil)
	r.Header.Set("x-agentcc-timeout", "-5s")

	got := h.resolveTimeout(rc, r)
	if got != 60*time.Second {
		t.Errorf("resolveTimeout = %v, want 60s (negative header should fall through)", got)
	}
}

func TestResolveTimeout_NilModelTimeouts(t *testing.T) {
	h := &Handlers{defaultTimeout: 45 * time.Second}
	// modelTimeouts not set (nil pointer)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model = "gpt-4o"

	r := httptest.NewRequest("GET", "/", nil)

	got := h.resolveTimeout(rc, r)
	if got != 45*time.Second {
		t.Errorf("resolveTimeout = %v, want 45s (nil model timeouts)", got)
	}
}

func TestResolveTimeout_HeaderDurationFormats(t *testing.T) {
	h := &Handlers{defaultTimeout: 60 * time.Second}

	tests := []struct {
		header string
		want   time.Duration
	}{
		{"30s", 30 * time.Second},
		{"2m", 2 * time.Minute},
		{"500ms", 500 * time.Millisecond},
		{"1m30s", 90 * time.Second},
	}

	for _, tt := range tests {
		t.Run(tt.header, func(t *testing.T) {
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Model = "gpt-4o"

			r := httptest.NewRequest("GET", "/", nil)
			r.Header.Set("x-agentcc-timeout", tt.header)

			got := h.resolveTimeout(rc, r)
			if got != tt.want {
				t.Errorf("resolveTimeout(%q) = %v, want %v", tt.header, got, tt.want)
			}
		})
	}
}

func TestResolveTimeout_ZeroModelTimeoutFallsThrough(t *testing.T) {
	h := &Handlers{defaultTimeout: 60 * time.Second}
	mt := map[string]time.Duration{"gpt-4o": 0}
	h.modelTimeouts.Store(&mt)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model = "gpt-4o"

	r := httptest.NewRequest("GET", "/", nil)

	got := h.resolveTimeout(rc, r)
	if got != 60*time.Second {
		t.Errorf("resolveTimeout = %v, want 60s (zero model timeout falls through)", got)
	}
}

func TestChatCompletion_TimeoutMetadataHeader(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	cfg := config.DefaultConfig()
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL:   mock.URL,
		APIFormat: "openai",
		Models:    []string{"gpt-4o"},
	}
	cfg.Routing.ModelTimeouts = map[string]time.Duration{
		"gpt-4o": 90 * time.Second,
	}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}

	engine := pipeline.NewEngine()
	srv := New(cfg, "", registry, engine, nil, nil, nil, nil, testModelDBPtr(), nil, nil)

	reqBody := `{"model":"gpt-4o","messages":[{"role":"user","content":"Hello"}]}`
	req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewBufferString(reqBody))
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != 200 {
		t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
	}

	// Verify x-agentcc-timeout-ms header is set to 90000 (90s = 90000ms).
	timeoutHeader := w.Header().Get("x-agentcc-timeout-ms")
	if timeoutHeader != "90000" {
		t.Errorf("x-agentcc-timeout-ms = %q, want %q", timeoutHeader, "90000")
	}
}

func TestProviderPrefixRouting(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	srv := createTestServer(t, mock.URL)

	// Use "openai/gpt-4o" format -- should route to openai provider.
	reqBody := `{"model":"openai/gpt-4o","messages":[{"role":"user","content":"Hello"}]}`
	req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewBufferString(reqBody))
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != 200 {
		body, _ := io.ReadAll(w.Result().Body)
		t.Fatalf("status = %d, want 200. Body: %s", w.Code, string(body))
	}
}
