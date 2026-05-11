package azure

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

func newTestProvider(t *testing.T, baseURL string) *Provider {
	t.Helper()
	cfg := config.ProviderConfig{
		BaseURL:       baseURL,
		APIKey:        "test-api-key",
		MaxConcurrent: 10,
		ConnPoolSize:  5,
	}
	p, err := New("azure-test", cfg)
	if err != nil {
		t.Fatalf("failed to create provider: %v", err)
	}
	return p
}

// ---------------------------------------------------------------------------
// New() constructor tests
// ---------------------------------------------------------------------------

func TestNew_DefaultValues(t *testing.T) {
	cfg := config.ProviderConfig{
		BaseURL: "https://myresource.openai.azure.com",
		APIKey:  "az-key-123",
	}
	p, err := New("azure-1", cfg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	defer p.Close()

	if p.id != "azure-1" {
		t.Errorf("id = %q, want %q", p.id, "azure-1")
	}
	if p.baseURL != "https://myresource.openai.azure.com" {
		t.Errorf("baseURL = %q, want %q", p.baseURL, "https://myresource.openai.azure.com")
	}
	if p.apiKey != "az-key-123" {
		t.Errorf("apiKey = %q, want %q", p.apiKey, "az-key-123")
	}
	// Default timeout is 60s.
	if p.httpClient.Timeout != 60*time.Second {
		t.Errorf("timeout = %v, want %v", p.httpClient.Timeout, 60*time.Second)
	}
	// Default semaphore capacity is 100.
	if cap(p.semaphore) != 100 {
		t.Errorf("semaphore cap = %d, want 100", cap(p.semaphore))
	}
	// Default api-version is 2024-10-21.
	if p.apiVersion != "2024-10-21" {
		t.Errorf("apiVersion = %q, want %q", p.apiVersion, "2024-10-21")
	}
}

func TestNew_CustomValues(t *testing.T) {
	cfg := config.ProviderConfig{
		BaseURL:        "https://custom.openai.azure.com",
		APIKey:         "custom-key",
		DefaultTimeout: 30 * time.Second,
		MaxConcurrent:  50,
		ConnPoolSize:   25,
		Headers: map[string]string{
			"api-version": "2025-01-01",
		},
	}
	p, err := New("azure-custom", cfg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	defer p.Close()

	if p.httpClient.Timeout != 30*time.Second {
		t.Errorf("timeout = %v, want %v", p.httpClient.Timeout, 30*time.Second)
	}
	if cap(p.semaphore) != 50 {
		t.Errorf("semaphore cap = %d, want 50", cap(p.semaphore))
	}
	if p.apiVersion != "2025-01-01" {
		t.Errorf("apiVersion = %q, want %q", p.apiVersion, "2025-01-01")
	}
}

func TestNew_BearerAuthType(t *testing.T) {
	cfg := config.ProviderConfig{
		BaseURL: "https://myresource.openai.azure.com",
		APIKey:  "azure-ad-bearer-token",
		Headers: map[string]string{
			"auth-type": "bearer",
		},
	}
	p, err := New("azure-ad", cfg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	defer p.Close()

	if p.authType != "bearer" {
		t.Errorf("authType = %q, want %q", p.authType, "bearer")
	}
	if p.apiKey != "azure-ad-bearer-token" {
		t.Errorf("apiKey = %q, want %q", p.apiKey, "azure-ad-bearer-token")
	}
}

func TestNew_DefaultAuthType(t *testing.T) {
	cfg := config.ProviderConfig{
		BaseURL: "https://myresource.openai.azure.com",
		APIKey:  "az-key",
	}
	p, err := New("azure-default-auth", cfg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	defer p.Close()

	if p.authType != "api-key" {
		t.Errorf("authType = %q, want %q (default)", p.authType, "api-key")
	}
}

func TestNew_TrailingSlashTrimmed(t *testing.T) {
	cfg := config.ProviderConfig{
		BaseURL: "https://myresource.openai.azure.com/",
		APIKey:  "key",
	}
	p, err := New("test", cfg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	defer p.Close()

	if p.baseURL != "https://myresource.openai.azure.com" {
		t.Errorf("baseURL = %q, want trailing slash trimmed", p.baseURL)
	}
}

func TestProviderID(t *testing.T) {
	cfg := config.ProviderConfig{
		BaseURL: "https://myresource.openai.azure.com",
		APIKey:  "key",
	}
	p, err := New("my-azure", cfg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	defer p.Close()

	if p.ID() != "my-azure" {
		t.Errorf("ID() = %q, want %q", p.ID(), "my-azure")
	}
}

// ---------------------------------------------------------------------------
// deploymentURL() tests
// ---------------------------------------------------------------------------

func TestDeploymentURL(t *testing.T) {
	p := &Provider{
		baseURL:    "https://myresource.openai.azure.com",
		apiVersion: "2024-10-21",
	}

	got := p.deploymentURL("gpt-4o", "chat/completions")
	want := "https://myresource.openai.azure.com/openai/deployments/gpt-4o/chat/completions?api-version=2024-10-21"
	if got != want {
		t.Errorf("deploymentURL =\n  %q\nwant:\n  %q", got, want)
	}
}

func TestDeploymentURL_CustomVersion(t *testing.T) {
	p := &Provider{
		baseURL:    "https://east-us.openai.azure.com",
		apiVersion: "2025-01-01",
	}

	got := p.deploymentURL("my-deployment", "chat/completions")
	want := "https://east-us.openai.azure.com/openai/deployments/my-deployment/chat/completions?api-version=2025-01-01"
	if got != want {
		t.Errorf("deploymentURL =\n  %q\nwant:\n  %q", got, want)
	}
}

// ---------------------------------------------------------------------------
// resolveDeployment() tests
// ---------------------------------------------------------------------------

func TestResolveDeployment_WithPrefix(t *testing.T) {
	got := resolveDeployment("azure/gpt-4o")
	if got != "gpt-4o" {
		t.Errorf("resolveDeployment(%q) = %q, want %q", "azure/gpt-4o", got, "gpt-4o")
	}
}

func TestResolveDeployment_BareName(t *testing.T) {
	got := resolveDeployment("gpt-4o")
	if got != "gpt-4o" {
		t.Errorf("resolveDeployment(%q) = %q, want %q", "gpt-4o", got, "gpt-4o")
	}
}

func TestResolveDeployment_MultiSlash(t *testing.T) {
	// Only strips up to the first slash.
	got := resolveDeployment("azure/my/deployment")
	if got != "my/deployment" {
		t.Errorf("resolveDeployment(%q) = %q, want %q", "azure/my/deployment", got, "my/deployment")
	}
}

// ---------------------------------------------------------------------------
// setAuth() tests
// ---------------------------------------------------------------------------

func TestSetAuth_SetsAPIKeyHeader(t *testing.T) {
	p := &Provider{apiKey: "az-secret-key"}
	req := httptest.NewRequest("POST", "/test", nil)
	p.setAuth(req)

	if got := req.Header.Get("api-key"); got != "az-secret-key" {
		t.Errorf("api-key header = %q, want %q", got, "az-secret-key")
	}
	// Must NOT use Authorization: Bearer header (Azure-specific).
	if got := req.Header.Get("Authorization"); got != "" {
		t.Errorf("Authorization header should be empty for Azure, got %q", got)
	}
}

func TestSetAuth_NoKeyEmpty(t *testing.T) {
	p := &Provider{apiKey: ""}
	req := httptest.NewRequest("POST", "/test", nil)
	p.setAuth(req)

	if got := req.Header.Get("api-key"); got != "" {
		t.Errorf("api-key header should be empty when no key is set, got %q", got)
	}
}

func TestSetAuth_BearerAuthType(t *testing.T) {
	p := &Provider{apiKey: "azure-ad-token-xyz", authType: "bearer"}
	req := httptest.NewRequest("POST", "/test", nil)
	p.setAuth(req)

	if got := req.Header.Get("Authorization"); got != "Bearer azure-ad-token-xyz" {
		t.Errorf("Authorization header = %q, want %q", got, "Bearer azure-ad-token-xyz")
	}
	// When using bearer auth, api-key header should NOT be set.
	if got := req.Header.Get("api-key"); got != "" {
		t.Errorf("api-key header should be empty for bearer auth, got %q", got)
	}
}

func TestSetAuth_DefaultAuthType(t *testing.T) {
	// When authType is "api-key" (the default), should use api-key header.
	p := &Provider{apiKey: "az-key-123", authType: "api-key"}
	req := httptest.NewRequest("POST", "/test", nil)
	p.setAuth(req)

	if got := req.Header.Get("api-key"); got != "az-key-123" {
		t.Errorf("api-key header = %q, want %q", got, "az-key-123")
	}
	if got := req.Header.Get("Authorization"); got != "" {
		t.Errorf("Authorization header should be empty for api-key auth, got %q", got)
	}
}

func TestSetAuth_BearerNoKey(t *testing.T) {
	// When authType is "bearer" but apiKey is empty, no headers should be set.
	p := &Provider{apiKey: "", authType: "bearer"}
	req := httptest.NewRequest("POST", "/test", nil)
	p.setAuth(req)

	if got := req.Header.Get("Authorization"); got != "" {
		t.Errorf("Authorization header should be empty when no key, got %q", got)
	}
	if got := req.Header.Get("api-key"); got != "" {
		t.Errorf("api-key header should be empty when no key, got %q", got)
	}
}

// ---------------------------------------------------------------------------
// mapProviderStatus() tests
// ---------------------------------------------------------------------------

func TestMapProviderStatus(t *testing.T) {
	tests := []struct {
		name       string
		input      int
		wantStatus int
	}{
		{"429 maps to 429", 429, http.StatusTooManyRequests},
		{"500 maps to 502", 500, http.StatusBadGateway},
		{"502 maps to 502", 502, http.StatusBadGateway},
		{"503 maps to 502", 503, http.StatusBadGateway},
		{"400 passes through", 400, 400},
		{"401 passes through", 401, 401},
		{"403 passes through", 403, 403},
		{"404 passes through", 404, 404},
		{"200 maps to 502 (default)", 200, http.StatusBadGateway},
		{"301 maps to 502 (default)", 301, http.StatusBadGateway},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := mapProviderStatus(tt.input)
			if got != tt.wantStatus {
				t.Errorf("mapProviderStatus(%d) = %d, want %d", tt.input, got, tt.wantStatus)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// Integration: Non-streaming ChatCompletion
// ---------------------------------------------------------------------------

func TestIntegration_ChatCompletion_Success(t *testing.T) {
	var receivedAPIKey string
	var receivedAuth string
	var receivedPath string
	var receivedQuery string

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedAPIKey = r.Header.Get("api-key")
		receivedAuth = r.Header.Get("Authorization")
		receivedPath = r.URL.Path
		receivedQuery = r.URL.Query().Get("api-version")

		if r.Method != http.MethodPost {
			t.Errorf("method = %q, want POST", r.Method)
		}

		body, _ := io.ReadAll(r.Body)
		var reqBody map[string]json.RawMessage
		if err := json.Unmarshal(body, &reqBody); err != nil {
			t.Errorf("failed to unmarshal request: %v", err)
			http.Error(w, "bad request", 400)
			return
		}

		// Verify stream is false.
		var stream bool
		if raw, ok := reqBody["stream"]; ok {
			json.Unmarshal(raw, &stream)
		}
		if stream {
			t.Error("stream should be false for non-streaming")
		}

		resp := models.ChatCompletionResponse{
			ID:      "chatcmpl-azure-001",
			Object:  "chat.completion",
			Created: 1700000000,
			Model:   "gpt-4o",
			Choices: []models.Choice{
				{
					Index: 0,
					Message: models.Message{
						Role:    "assistant",
						Content: json.RawMessage(`"Hello from Azure!"`),
					},
					FinishReason: "stop",
				},
			},
			Usage: &models.Usage{
				PromptTokens:     12,
				CompletionTokens: 8,
				TotalTokens:      20,
			},
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model: "gpt-4o",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hello!"`)},
		},
	}

	result, err := p.ChatCompletion(context.Background(), req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// Verify Azure-specific: api-key header (not Authorization: Bearer).
	if receivedAPIKey != "test-api-key" {
		t.Errorf("api-key header = %q, want %q", receivedAPIKey, "test-api-key")
	}
	if receivedAuth != "" {
		t.Errorf("Authorization header should be empty for Azure, got %q", receivedAuth)
	}

	// Verify Azure URL format: /openai/deployments/{model}/chat/completions
	if !strings.Contains(receivedPath, "/openai/deployments/gpt-4o/chat/completions") {
		t.Errorf("path = %q, want it to contain /openai/deployments/gpt-4o/chat/completions", receivedPath)
	}

	// Verify api-version query param.
	if receivedQuery != "2024-10-21" {
		t.Errorf("api-version query = %q, want %q", receivedQuery, "2024-10-21")
	}

	// Verify response.
	if result.ID != "chatcmpl-azure-001" {
		t.Errorf("id = %q, want %q", result.ID, "chatcmpl-azure-001")
	}
	if result.Object != "chat.completion" {
		t.Errorf("object = %q, want %q", result.Object, "chat.completion")
	}
	if result.Model != "gpt-4o" {
		t.Errorf("model = %q, want %q", result.Model, "gpt-4o")
	}
	if len(result.Choices) != 1 {
		t.Fatalf("choices length = %d, want 1", len(result.Choices))
	}
	if result.Choices[0].FinishReason != "stop" {
		t.Errorf("finish_reason = %q, want %q", result.Choices[0].FinishReason, "stop")
	}

	var content string
	if err := json.Unmarshal(result.Choices[0].Message.Content, &content); err != nil {
		t.Fatalf("failed to unmarshal content: %v", err)
	}
	if content != "Hello from Azure!" {
		t.Errorf("content = %q, want %q", content, "Hello from Azure!")
	}

	if result.Usage == nil {
		t.Fatal("usage is nil")
	}
	if result.Usage.PromptTokens != 12 {
		t.Errorf("prompt_tokens = %d, want 12", result.Usage.PromptTokens)
	}
	if result.Usage.CompletionTokens != 8 {
		t.Errorf("completion_tokens = %d, want 8", result.Usage.CompletionTokens)
	}
	if result.Usage.TotalTokens != 20 {
		t.Errorf("total_tokens = %d, want 20", result.Usage.TotalTokens)
	}
}

func TestIntegration_ChatCompletion_ResolvesDeploymentFromPrefix(t *testing.T) {
	var receivedPath string

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedPath = r.URL.Path

		resp := models.ChatCompletionResponse{
			ID:     "chatcmpl-002",
			Object: "chat.completion",
			Model:  "gpt-4o",
			Choices: []models.Choice{
				{Index: 0, Message: models.Message{Role: "assistant", Content: json.RawMessage(`"ok"`)}, FinishReason: "stop"},
			},
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model: "azure/gpt-4o",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
	}

	_, err := p.ChatCompletion(context.Background(), req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// The prefix "azure/" should be stripped, deploying to "gpt-4o".
	if !strings.Contains(receivedPath, "/openai/deployments/gpt-4o/") {
		t.Errorf("path = %q, want deployment 'gpt-4o' after prefix strip", receivedPath)
	}
}

// ---------------------------------------------------------------------------
// Integration: ChatCompletion with bearer auth (Azure AD)
// ---------------------------------------------------------------------------

func TestIntegration_ChatCompletion_BearerAuth(t *testing.T) {
	var receivedAPIKeyHeader string
	var receivedAuthHeader string

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedAPIKeyHeader = r.Header.Get("api-key")
		receivedAuthHeader = r.Header.Get("Authorization")

		resp := models.ChatCompletionResponse{
			ID:      "chatcmpl-bearer-001",
			Object:  "chat.completion",
			Created: 1700000000,
			Model:   "gpt-4o",
			Choices: []models.Choice{
				{
					Index: 0,
					Message: models.Message{
						Role:    "assistant",
						Content: json.RawMessage(`"Hello from Azure AD!"`),
					},
					FinishReason: "stop",
				},
			},
			Usage: &models.Usage{
				PromptTokens:     10,
				CompletionTokens: 5,
				TotalTokens:      15,
			},
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	cfg := config.ProviderConfig{
		BaseURL:       server.URL,
		APIKey:        "azure-ad-token-abc123",
		MaxConcurrent: 10,
		ConnPoolSize:  5,
		Headers: map[string]string{
			"auth-type": "bearer",
		},
	}
	p, err := New("azure-bearer-test", cfg)
	if err != nil {
		t.Fatalf("failed to create provider: %v", err)
	}
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model: "gpt-4o",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hello!"`)},
		},
	}

	result, err := p.ChatCompletion(context.Background(), req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// Verify Bearer auth header is used, NOT api-key.
	if receivedAuthHeader != "Bearer azure-ad-token-abc123" {
		t.Errorf("Authorization header = %q, want %q", receivedAuthHeader, "Bearer azure-ad-token-abc123")
	}
	if receivedAPIKeyHeader != "" {
		t.Errorf("api-key header should be empty for bearer auth, got %q", receivedAPIKeyHeader)
	}

	// Verify response.
	if result.ID != "chatcmpl-bearer-001" {
		t.Errorf("id = %q, want %q", result.ID, "chatcmpl-bearer-001")
	}
	if len(result.Choices) != 1 {
		t.Fatalf("choices length = %d, want 1", len(result.Choices))
	}
	var content string
	if err := json.Unmarshal(result.Choices[0].Message.Content, &content); err != nil {
		t.Fatalf("failed to unmarshal content: %v", err)
	}
	if content != "Hello from Azure AD!" {
		t.Errorf("content = %q, want %q", content, "Hello from Azure AD!")
	}
}

func TestIntegration_StreamChatCompletion_BearerAuth(t *testing.T) {
	var receivedAPIKeyHeader string
	var receivedAuthHeader string

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedAPIKeyHeader = r.Header.Get("api-key")
		receivedAuthHeader = r.Header.Get("Authorization")

		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)

		flusher, ok := w.(http.Flusher)
		if !ok {
			t.Fatal("ResponseWriter does not support Flusher")
			return
		}

		events := []string{
			`data: {"id":"chatcmpl-bearer-stream","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}`,
			`data: {"id":"chatcmpl-bearer-stream","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"content":"Bearer streaming!"},"finish_reason":null}]}`,
			`data: {"id":"chatcmpl-bearer-stream","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}`,
			`data: [DONE]`,
		}

		for _, event := range events {
			fmt.Fprintf(w, "%s\n\n", event)
			flusher.Flush()
		}
	}))
	defer server.Close()

	cfg := config.ProviderConfig{
		BaseURL:       server.URL,
		APIKey:        "azure-ad-stream-token",
		MaxConcurrent: 10,
		ConnPoolSize:  5,
		Headers: map[string]string{
			"auth-type": "bearer",
		},
	}
	p, err := New("azure-bearer-stream-test", cfg)
	if err != nil {
		t.Fatalf("failed to create provider: %v", err)
	}
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model:  "gpt-4o",
		Stream: true,
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hello!"`)},
		},
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	chunks, errs := p.StreamChatCompletion(ctx, req)

	var received []models.StreamChunk
	for chunk := range chunks {
		received = append(received, chunk)
	}
	var streamErr error
	for e := range errs {
		streamErr = e
	}
	if streamErr != nil {
		t.Fatalf("unexpected stream error: %v", streamErr)
	}

	// Verify Bearer auth was used for streaming too.
	if receivedAuthHeader != "Bearer azure-ad-stream-token" {
		t.Errorf("Authorization header = %q, want %q", receivedAuthHeader, "Bearer azure-ad-stream-token")
	}
	if receivedAPIKeyHeader != "" {
		t.Errorf("api-key header should be empty for bearer auth, got %q", receivedAPIKeyHeader)
	}

	// Verify chunks received.
	if len(received) != 3 {
		t.Fatalf("received %d chunks, want 3", len(received))
	}
	if received[1].Choices[0].Delta.Content == nil || *received[1].Choices[0].Delta.Content != "Bearer streaming!" {
		t.Errorf("chunk[1] content = %v, want %q", received[1].Choices[0].Delta.Content, "Bearer streaming!")
	}
}

// ---------------------------------------------------------------------------
// Integration: ChatCompletion error handling
// ---------------------------------------------------------------------------

func TestIntegration_ChatCompletion_Error429(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusTooManyRequests)
		resp := models.ErrorResponse{
			Error: models.ErrorDetail{
				Message: "Rate limit exceeded",
				Type:    "rate_limit_error",
				Code:    "429",
			},
		}
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model:    "gpt-4o",
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"Hi"`)}},
	}

	_, err := p.ChatCompletion(context.Background(), req)
	if err == nil {
		t.Fatal("expected error, got nil")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T: %v", err, err)
	}
	if apiErr.Status != http.StatusTooManyRequests {
		t.Errorf("status = %d, want %d", apiErr.Status, http.StatusTooManyRequests)
	}
}

func TestIntegration_ChatCompletion_Error500(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		resp := models.ErrorResponse{
			Error: models.ErrorDetail{
				Message: "Internal server error",
				Type:    "server_error",
				Code:    "internal_error",
			},
		}
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model:    "gpt-4o",
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"Hi"`)}},
	}

	_, err := p.ChatCompletion(context.Background(), req)
	if err == nil {
		t.Fatal("expected error, got nil")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T: %v", err, err)
	}
	// 500 from upstream maps to 502.
	if apiErr.Status != http.StatusBadGateway {
		t.Errorf("status = %d, want %d", apiErr.Status, http.StatusBadGateway)
	}
}

func TestIntegration_ChatCompletion_Error401(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusUnauthorized)
		resp := models.ErrorResponse{
			Error: models.ErrorDetail{
				Message: "Invalid API key",
				Type:    "authentication_error",
				Code:    "invalid_api_key",
			},
		}
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model:    "gpt-4o",
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"Hi"`)}},
	}

	_, err := p.ChatCompletion(context.Background(), req)
	if err == nil {
		t.Fatal("expected error, got nil")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T: %v", err, err)
	}
	if apiErr.Status != 401 {
		t.Errorf("status = %d, want 401", apiErr.Status)
	}
}

func TestIntegration_ChatCompletion_ErrorNonJSON(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/plain")
		w.WriteHeader(http.StatusBadGateway)
		fmt.Fprint(w, "Bad Gateway: upstream failed")
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model:    "gpt-4o",
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"Hi"`)}},
	}

	_, err := p.ChatCompletion(context.Background(), req)
	if err == nil {
		t.Fatal("expected error, got nil")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T: %v", err, err)
	}
	if apiErr.Status != http.StatusBadGateway {
		t.Errorf("status = %d, want %d", apiErr.Status, http.StatusBadGateway)
	}
	if !strings.Contains(apiErr.Message, "Bad Gateway: upstream failed") {
		t.Errorf("message = %q, want it to contain raw body text", apiErr.Message)
	}
}

// ---------------------------------------------------------------------------
// Integration: ChatCompletion timeout
// ---------------------------------------------------------------------------

func TestIntegration_ChatCompletion_ContextTimeout(t *testing.T) {
	unblock := make(chan struct{})

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-unblock:
		case <-r.Context().Done():
		}
	}))
	defer func() {
		close(unblock)
		server.Close()
	}()

	cfg := config.ProviderConfig{
		BaseURL:        server.URL,
		APIKey:         "test-key",
		DefaultTimeout: 30 * time.Second,
		MaxConcurrent:  10,
		ConnPoolSize:   5,
	}
	p, err := New("timeout-test", cfg)
	if err != nil {
		t.Fatalf("failed to create provider: %v", err)
	}
	defer p.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()

	req := &models.ChatCompletionRequest{
		Model:    "gpt-4o",
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"Hi"`)}},
	}

	_, err = p.ChatCompletion(ctx, req)
	if err == nil {
		t.Fatal("expected timeout error, got nil")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T: %v", err, err)
	}
	if apiErr.Status != http.StatusGatewayTimeout {
		t.Errorf("status = %d, want %d", apiErr.Status, http.StatusGatewayTimeout)
	}
}

func TestIntegration_ChatCompletion_HTTPClientTimeout(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(3 * time.Second)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	cfg := config.ProviderConfig{
		BaseURL:        server.URL,
		APIKey:         "test-key",
		DefaultTimeout: 100 * time.Millisecond,
		MaxConcurrent:  10,
		ConnPoolSize:   5,
	}
	p, err := New("timeout-test", cfg)
	if err != nil {
		t.Fatalf("failed to create provider: %v", err)
	}
	defer p.Close()

	// Use a long context so HTTP client timeout fires first.
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	req := &models.ChatCompletionRequest{
		Model:    "gpt-4o",
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"Hi"`)}},
	}

	_, err = p.ChatCompletion(ctx, req)
	if err == nil {
		t.Fatal("expected timeout error, got nil")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T: %v", err, err)
	}
	// HTTP client timeout fires while ctx.Err() is still nil, so goes to ErrUpstreamProvider.
	if apiErr.Status != http.StatusBadGateway {
		t.Errorf("status = %d, want %d", apiErr.Status, http.StatusBadGateway)
	}
}

// ---------------------------------------------------------------------------
// Integration: Streaming ChatCompletion
// ---------------------------------------------------------------------------

func TestIntegration_StreamChatCompletion_Success(t *testing.T) {
	var receivedAPIKey string
	var receivedPath string
	var receivedQuery string
	var receivedAccept string

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedAPIKey = r.Header.Get("api-key")
		receivedPath = r.URL.Path
		receivedQuery = r.URL.Query().Get("api-version")
		receivedAccept = r.Header.Get("Accept")

		// Verify stream=true in body.
		body, _ := io.ReadAll(r.Body)
		var reqBody map[string]json.RawMessage
		json.Unmarshal(body, &reqBody)
		var stream bool
		if raw, ok := reqBody["stream"]; ok {
			json.Unmarshal(raw, &stream)
		}
		if !stream {
			t.Error("stream should be true for streaming request")
		}

		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)

		flusher, ok := w.(http.Flusher)
		if !ok {
			t.Fatal("ResponseWriter does not support Flusher")
			return
		}

		events := []string{
			`data: {"id":"chatcmpl-stream","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}`,
			`data: {"id":"chatcmpl-stream","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}`,
			`data: {"id":"chatcmpl-stream","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"content":" from"},"finish_reason":null}]}`,
			`data: {"id":"chatcmpl-stream","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"content":" Azure!"},"finish_reason":null}]}`,
			`data: {"id":"chatcmpl-stream","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}`,
			`data: [DONE]`,
		}

		for _, event := range events {
			fmt.Fprintf(w, "%s\n\n", event)
			flusher.Flush()
		}
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model:  "gpt-4o",
		Stream: true,
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hello!"`)},
		},
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	chunks, errs := p.StreamChatCompletion(ctx, req)

	var received []models.StreamChunk
	for chunk := range chunks {
		received = append(received, chunk)
	}
	var streamErr error
	for e := range errs {
		streamErr = e
	}
	if streamErr != nil {
		t.Fatalf("unexpected stream error: %v", streamErr)
	}

	// Verify Azure-specific headers.
	if receivedAPIKey != "test-api-key" {
		t.Errorf("api-key header = %q, want %q", receivedAPIKey, "test-api-key")
	}
	if receivedAccept != "text/event-stream" {
		t.Errorf("Accept header = %q, want %q", receivedAccept, "text/event-stream")
	}
	if !strings.Contains(receivedPath, "/openai/deployments/gpt-4o/chat/completions") {
		t.Errorf("path = %q, want it to contain /openai/deployments/gpt-4o/chat/completions", receivedPath)
	}
	if receivedQuery != "2024-10-21" {
		t.Errorf("api-version query = %q, want %q", receivedQuery, "2024-10-21")
	}

	// Expected chunks: role, "Hello", " from", " Azure!", finish.
	if len(received) != 5 {
		t.Fatalf("received %d chunks, want 5", len(received))
	}

	// First chunk: role.
	if received[0].Choices[0].Delta.Role != "assistant" {
		t.Errorf("chunk[0] role = %q, want %q", received[0].Choices[0].Delta.Role, "assistant")
	}
	if received[0].ID != "chatcmpl-stream" {
		t.Errorf("chunk[0] ID = %q, want %q", received[0].ID, "chatcmpl-stream")
	}

	// Content chunks.
	if received[1].Choices[0].Delta.Content == nil || *received[1].Choices[0].Delta.Content != "Hello" {
		t.Errorf("chunk[1] content = %v, want %q", received[1].Choices[0].Delta.Content, "Hello")
	}
	if received[2].Choices[0].Delta.Content == nil || *received[2].Choices[0].Delta.Content != " from" {
		t.Errorf("chunk[2] content = %v, want %q", received[2].Choices[0].Delta.Content, " from")
	}
	if received[3].Choices[0].Delta.Content == nil || *received[3].Choices[0].Delta.Content != " Azure!" {
		t.Errorf("chunk[3] content = %v, want %q", received[3].Choices[0].Delta.Content, " Azure!")
	}

	// Final chunk: finish reason.
	last := received[len(received)-1]
	if last.Choices[0].FinishReason == nil || *last.Choices[0].FinishReason != "stop" {
		t.Errorf("last chunk finish_reason = %v, want %q", last.Choices[0].FinishReason, "stop")
	}
}

func TestIntegration_StreamChatCompletion_DoneTermination(t *testing.T) {
	// Verify that [DONE] properly terminates the stream without error.
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)

		flusher, _ := w.(http.Flusher)
		fmt.Fprint(w, "data: {\"id\":\"done-test\",\"object\":\"chat.completion.chunk\",\"created\":1700000000,\"model\":\"gpt-4o\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"Hi\"},\"finish_reason\":null}]}\n\n")
		flusher.Flush()
		fmt.Fprint(w, "data: [DONE]\n\n")
		flusher.Flush()
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model:    "gpt-4o",
		Stream:   true,
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"Hi"`)}},
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	chunks, errs := p.StreamChatCompletion(ctx, req)

	var received []models.StreamChunk
	for chunk := range chunks {
		received = append(received, chunk)
	}
	var streamErr error
	for e := range errs {
		streamErr = e
	}

	if streamErr != nil {
		t.Fatalf("unexpected stream error: %v", streamErr)
	}
	if len(received) != 1 {
		t.Fatalf("received %d chunks, want 1", len(received))
	}
	if received[0].Choices[0].Delta.Content == nil || *received[0].Choices[0].Delta.Content != "Hi" {
		t.Errorf("chunk content = %v, want %q", received[0].Choices[0].Delta.Content, "Hi")
	}
}

// ---------------------------------------------------------------------------
// Integration: Streaming error handling
// ---------------------------------------------------------------------------

func TestIntegration_StreamChatCompletion_Error429(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusTooManyRequests)
		resp := models.ErrorResponse{
			Error: models.ErrorDetail{
				Message: "Rate limit exceeded",
				Type:    "rate_limit_error",
				Code:    "429",
			},
		}
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model:    "gpt-4o",
		Stream:   true,
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"Hi"`)}},
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	chunks, errs := p.StreamChatCompletion(ctx, req)

	for range chunks {
	}

	var streamErr error
	for e := range errs {
		streamErr = e
	}

	if streamErr == nil {
		t.Fatal("expected stream error, got nil")
	}
	apiErr, ok := streamErr.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T: %v", streamErr, streamErr)
	}
	if apiErr.Status != http.StatusTooManyRequests {
		t.Errorf("status = %d, want %d", apiErr.Status, http.StatusTooManyRequests)
	}
}

func TestIntegration_StreamChatCompletion_Error500(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		resp := models.ErrorResponse{
			Error: models.ErrorDetail{
				Message: "Internal server error",
				Type:    "server_error",
				Code:    "internal_error",
			},
		}
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model:    "gpt-4o",
		Stream:   true,
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"Hi"`)}},
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	chunks, errs := p.StreamChatCompletion(ctx, req)

	for range chunks {
	}

	var streamErr error
	for e := range errs {
		streamErr = e
	}

	if streamErr == nil {
		t.Fatal("expected stream error, got nil")
	}
	apiErr, ok := streamErr.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T: %v", streamErr, streamErr)
	}
	if apiErr.Status != http.StatusBadGateway {
		t.Errorf("status = %d, want %d (upstream 500 maps to 502)", apiErr.Status, http.StatusBadGateway)
	}
}

// ---------------------------------------------------------------------------
// Integration: Streaming context cancellation
// ---------------------------------------------------------------------------

func TestIntegration_StreamChatCompletion_ContextCancellation(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)

		flusher, ok := w.(http.Flusher)
		if !ok {
			return
		}

		fmt.Fprint(w, "data: {\"id\":\"cancel-test\",\"object\":\"chat.completion.chunk\",\"created\":1700000000,\"model\":\"gpt-4o\",\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\"},\"finish_reason\":null}]}\n\n")
		flusher.Flush()

		// Wait for client disconnect.
		<-r.Context().Done()
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	req := &models.ChatCompletionRequest{
		Model:    "gpt-4o",
		Stream:   true,
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"Hi"`)}},
	}

	chunks, errs := p.StreamChatCompletion(ctx, req)

	var received []models.StreamChunk
	for chunk := range chunks {
		received = append(received, chunk)
	}
	for range errs {
	}

	if len(received) == 0 {
		t.Error("expected at least 1 chunk before cancellation")
	}
}

// ---------------------------------------------------------------------------
// Integration: ListModels
// ---------------------------------------------------------------------------

func TestIntegration_ListModels_Success(t *testing.T) {
	var receivedAPIKey string
	var receivedPath string
	var receivedQuery string

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedAPIKey = r.Header.Get("api-key")
		receivedPath = r.URL.Path
		receivedQuery = r.URL.Query().Get("api-version")

		if r.Method != http.MethodGet {
			t.Errorf("method = %q, want GET", r.Method)
		}

		resp := models.ModelListResponse{
			Object: "list",
			Data: []models.ModelObject{
				{ID: "gpt-4o", Object: "model", Created: 1700000000, OwnedBy: "azure"},
				{ID: "gpt-4", Object: "model", Created: 1700000001, OwnedBy: "azure"},
			},
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	result, err := p.ListModels(context.Background())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if receivedAPIKey != "test-api-key" {
		t.Errorf("api-key header = %q, want %q", receivedAPIKey, "test-api-key")
	}
	if receivedPath != "/openai/models" {
		t.Errorf("path = %q, want /openai/models", receivedPath)
	}
	if receivedQuery != "2024-10-21" {
		t.Errorf("api-version query = %q, want %q", receivedQuery, "2024-10-21")
	}

	if len(result) != 2 {
		t.Fatalf("models count = %d, want 2", len(result))
	}
	if result[0].ID != "gpt-4o" {
		t.Errorf("models[0].ID = %q, want %q", result[0].ID, "gpt-4o")
	}
	if result[1].ID != "gpt-4" {
		t.Errorf("models[1].ID = %q, want %q", result[1].ID, "gpt-4")
	}
}

func TestIntegration_ListModels_Error(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusForbidden)
		fmt.Fprint(w, "Forbidden")
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	_, err := p.ListModels(context.Background())
	if err == nil {
		t.Fatal("expected error, got nil")
	}
	if !strings.Contains(err.Error(), "403") {
		t.Errorf("error = %q, want it to mention status 403", err.Error())
	}
}

// ---------------------------------------------------------------------------
// parseProviderError() tests
// ---------------------------------------------------------------------------

func TestParseProviderError_ValidJSON(t *testing.T) {
	body := `{"error":{"message":"Model not found","type":"not_found","code":"model_not_found"}}`
	apiErr := parseProviderError(404, []byte(body))

	if apiErr.Status != 404 {
		t.Errorf("status = %d, want 404", apiErr.Status)
	}
	if apiErr.Message != "Model not found" {
		t.Errorf("message = %q, want %q", apiErr.Message, "Model not found")
	}
	if apiErr.Type != "not_found" {
		t.Errorf("type = %q, want %q", apiErr.Type, "not_found")
	}
	if apiErr.Code != "model_not_found" {
		t.Errorf("code = %q, want %q", apiErr.Code, "model_not_found")
	}
}

func TestParseProviderError_InvalidJSON(t *testing.T) {
	body := "Bad Gateway: upstream failed"
	apiErr := parseProviderError(502, []byte(body))

	if apiErr.Status != http.StatusBadGateway {
		t.Errorf("status = %d, want %d", apiErr.Status, http.StatusBadGateway)
	}
	if !strings.Contains(apiErr.Message, "Bad Gateway: upstream failed") {
		t.Errorf("message = %q, want it to contain raw body text", apiErr.Message)
	}
}

func TestParseProviderError_LongBodyTruncated(t *testing.T) {
	body := strings.Repeat("a", 600)
	apiErr := parseProviderError(500, []byte(body))

	if !strings.HasSuffix(apiErr.Message, "...") {
		t.Errorf("long body should be truncated with '...', got message of length %d", len(apiErr.Message))
	}
}

// ---------------------------------------------------------------------------
// Close() test
// ---------------------------------------------------------------------------

func TestClose(t *testing.T) {
	p := newTestProvider(t, "http://localhost")
	if err := p.Close(); err != nil {
		t.Errorf("Close() returned error: %v", err)
	}
}
