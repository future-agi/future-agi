package bedrock

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ===========================================================================
// New() tests
// ===========================================================================

func TestNew_WithEnvVars(t *testing.T) {
	t.Setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
	t.Setenv("AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY")

	cfg := config.ProviderConfig{
		BaseURL:   "https://bedrock-runtime.us-west-2.amazonaws.com",
		APIFormat: "bedrock",
	}

	p, err := New("test-bedrock", cfg)
	if err != nil {
		t.Fatalf("New() error: %v", err)
	}
	defer p.Close()

	if p.ID() != "test-bedrock" {
		t.Errorf("ID() = %q, want %q", p.ID(), "test-bedrock")
	}
	if p.region != "us-west-2" {
		t.Errorf("region = %q, want %q", p.region, "us-west-2")
	}
	if p.baseURL != "https://bedrock-runtime.us-west-2.amazonaws.com" {
		t.Errorf("baseURL = %q, want %q", p.baseURL, "https://bedrock-runtime.us-west-2.amazonaws.com")
	}
}

func TestNew_DefaultTimeout(t *testing.T) {
	t.Setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
	t.Setenv("AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY")

	cfg := config.ProviderConfig{
		BaseURL:   "https://bedrock-runtime.us-east-1.amazonaws.com",
		APIFormat: "bedrock",
	}

	p, err := New("test-bedrock", cfg)
	if err != nil {
		t.Fatalf("New() error: %v", err)
	}
	defer p.Close()

	if p.httpClient.Timeout != 120*time.Second {
		t.Errorf("timeout = %v, want %v", p.httpClient.Timeout, 120*time.Second)
	}
}

func TestNew_CustomTimeout(t *testing.T) {
	t.Setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
	t.Setenv("AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY")

	cfg := config.ProviderConfig{
		BaseURL:        "https://bedrock-runtime.us-east-1.amazonaws.com",
		APIFormat:      "bedrock",
		DefaultTimeout: 30 * time.Second,
	}

	p, err := New("test-bedrock", cfg)
	if err != nil {
		t.Fatalf("New() error: %v", err)
	}
	defer p.Close()

	if p.httpClient.Timeout != 30*time.Second {
		t.Errorf("timeout = %v, want %v", p.httpClient.Timeout, 30*time.Second)
	}
}

func TestNew_DefaultConcurrency(t *testing.T) {
	t.Setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
	t.Setenv("AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY")

	cfg := config.ProviderConfig{
		BaseURL:   "https://bedrock-runtime.us-east-1.amazonaws.com",
		APIFormat: "bedrock",
	}

	p, err := New("test-bedrock", cfg)
	if err != nil {
		t.Fatalf("New() error: %v", err)
	}
	defer p.Close()

	if cap(p.semaphore) != 100 {
		t.Errorf("semaphore capacity = %d, want 100", cap(p.semaphore))
	}
}

func TestNew_CustomConcurrency(t *testing.T) {
	t.Setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
	t.Setenv("AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY")

	cfg := config.ProviderConfig{
		BaseURL:       "https://bedrock-runtime.us-east-1.amazonaws.com",
		APIFormat:     "bedrock",
		MaxConcurrent: 10,
	}

	p, err := New("test-bedrock", cfg)
	if err != nil {
		t.Fatalf("New() error: %v", err)
	}
	defer p.Close()

	if cap(p.semaphore) != 10 {
		t.Errorf("semaphore capacity = %d, want 10", cap(p.semaphore))
	}
}

func TestNew_TrimsTrailingSlash(t *testing.T) {
	t.Setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
	t.Setenv("AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY")

	cfg := config.ProviderConfig{
		BaseURL:   "https://bedrock-runtime.us-east-1.amazonaws.com/",
		APIFormat: "bedrock",
	}

	p, err := New("test-bedrock", cfg)
	if err != nil {
		t.Fatalf("New() error: %v", err)
	}
	defer p.Close()

	if strings.HasSuffix(p.baseURL, "/") {
		t.Errorf("baseURL should not end with /, got %q", p.baseURL)
	}
}

func TestNew_MissingCredentials(t *testing.T) {
	t.Setenv("AWS_ACCESS_KEY_ID", "")
	t.Setenv("AWS_SECRET_ACCESS_KEY", "")

	cfg := config.ProviderConfig{
		BaseURL:   "https://bedrock-runtime.us-east-1.amazonaws.com",
		APIFormat: "bedrock",
	}

	_, err := New("test-bedrock", cfg)
	if err == nil {
		t.Fatal("expected error when credentials are missing")
	}
}

func TestNew_RegionFromHeaders(t *testing.T) {
	t.Setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
	t.Setenv("AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY")

	cfg := config.ProviderConfig{
		BaseURL:   "https://bedrock-runtime.us-east-1.amazonaws.com",
		APIFormat: "bedrock",
		Headers: map[string]string{
			"x-aws-region": "ap-southeast-1",
		},
	}

	p, err := New("test-bedrock", cfg)
	if err != nil {
		t.Fatalf("New() error: %v", err)
	}
	defer p.Close()

	if p.region != "ap-southeast-1" {
		t.Errorf("region = %q, want %q (from header)", p.region, "ap-southeast-1")
	}
}

// ===========================================================================
// ListModels tests
// ===========================================================================

func TestListModels_ReturnsNil(t *testing.T) {
	t.Setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
	t.Setenv("AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY")

	cfg := config.ProviderConfig{
		BaseURL:   "https://bedrock-runtime.us-east-1.amazonaws.com",
		APIFormat: "bedrock",
	}

	p, err := New("test-bedrock", cfg)
	if err != nil {
		t.Fatalf("New() error: %v", err)
	}
	defer p.Close()

	models, err := p.ListModels(context.Background())
	if err != nil {
		t.Fatalf("ListModels() error: %v", err)
	}
	if models != nil {
		t.Errorf("ListModels() should return nil, got %v", models)
	}
}

// ===========================================================================
// Integration tests: non-streaming with httptest.Server
// ===========================================================================

// newTestProvider creates a Provider pointing at the given mock server URL.
func newTestProvider(t *testing.T, serverURL string) *Provider {
	t.Helper()
	t.Setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
	t.Setenv("AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY")

	cfg := config.ProviderConfig{
		BaseURL:        serverURL,
		APIFormat:      "bedrock",
		DefaultTimeout: 5 * time.Second,
		MaxConcurrent:  10,
	}

	p, err := New("integration-test", cfg)
	if err != nil {
		t.Fatalf("New() error: %v", err)
	}
	return p
}

func TestIntegration_NonStreamingRoundtrip(t *testing.T) {
	modelID := "anthropic.claude-3-sonnet-20240229-v1:0"

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Verify the endpoint path.
		expectedPath := "/model/" + modelID + "/invoke"
		if r.URL.Path != expectedPath {
			t.Errorf("request path = %q, want %q", r.URL.Path, expectedPath)
		}

		// Verify request method.
		if r.Method != "POST" {
			t.Errorf("method = %q, want POST", r.Method)
		}

		// Verify required headers.
		if ct := r.Header.Get("Content-Type"); ct != "application/json" {
			t.Errorf("Content-Type = %q, want %q", ct, "application/json")
		}
		if auth := r.Header.Get("Authorization"); auth == "" {
			t.Error("Authorization header should be set (SigV4)")
		}
		if amzDate := r.Header.Get("x-amz-date"); amzDate == "" {
			t.Error("x-amz-date header should be set")
		}
		if contentSha := r.Header.Get("x-amz-content-sha256"); contentSha == "" {
			t.Error("x-amz-content-sha256 header should be set")
		}

		// Verify request body is valid Bedrock format.
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("reading request body: %v", err)
		}

		var br bedrockRequest
		if err := json.Unmarshal(body, &br); err != nil {
			t.Fatalf("parsing request body: %v", err)
		}
		if br.AnthropicVersion != "bedrock-2023-05-31" {
			t.Errorf("anthropic_version = %q, want %q", br.AnthropicVersion, "bedrock-2023-05-31")
		}
		if br.MaxTokens != 4096 {
			t.Errorf("max_tokens = %d, want 4096", br.MaxTokens)
		}

		// Return a valid Bedrock response.
		resp := bedrockResponse{
			ID:    "msg_test_123",
			Type:  "message",
			Model: "claude-3-sonnet-20240229",
			Role:  "assistant",
			Content: []bedrockContentBlock{
				{Type: "text", Text: "Hello from mock Bedrock!"},
			},
			StopReason: "end_turn",
			Usage: bedrockUsage{
				InputTokens:  15,
				OutputTokens: 7,
			},
		}

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model: modelID,
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hello, Claude!"`)},
		},
	}

	resp, err := p.ChatCompletion(context.Background(), req)
	if err != nil {
		t.Fatalf("ChatCompletion() error: %v", err)
	}

	if resp.ID != "msg_test_123" {
		t.Errorf("ID = %q, want %q", resp.ID, "msg_test_123")
	}
	if resp.Object != "chat.completion" {
		t.Errorf("Object = %q, want %q", resp.Object, "chat.completion")
	}
	if resp.Model != "claude-3-sonnet-20240229" {
		t.Errorf("Model = %q, want %q", resp.Model, "claude-3-sonnet-20240229")
	}
	if len(resp.Choices) != 1 {
		t.Fatalf("expected 1 choice, got %d", len(resp.Choices))
	}
	if resp.Choices[0].FinishReason != "stop" {
		t.Errorf("finish_reason = %q, want %q", resp.Choices[0].FinishReason, "stop")
	}

	var content string
	if err := json.Unmarshal(resp.Choices[0].Message.Content, &content); err != nil {
		t.Fatalf("unmarshal content: %v", err)
	}
	if content != "Hello from mock Bedrock!" {
		t.Errorf("content = %q, want %q", content, "Hello from mock Bedrock!")
	}

	if resp.Usage == nil {
		t.Fatal("usage should not be nil")
	}
	if resp.Usage.PromptTokens != 15 {
		t.Errorf("prompt_tokens = %d, want 15", resp.Usage.PromptTokens)
	}
	if resp.Usage.CompletionTokens != 7 {
		t.Errorf("completion_tokens = %d, want 7", resp.Usage.CompletionTokens)
	}
	if resp.Usage.TotalTokens != 22 {
		t.Errorf("total_tokens = %d, want 22", resp.Usage.TotalTokens)
	}
}

func TestIntegration_SystemMessageInRequest(t *testing.T) {
	modelID := "claude-3-sonnet"

	var receivedSystem string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		var br bedrockRequest
		json.Unmarshal(body, &br)
		receivedSystem = br.System

		resp := bedrockResponse{
			ID:    "msg_sys",
			Type:  "message",
			Model: "claude-3-sonnet",
			Role:  "assistant",
			Content: []bedrockContentBlock{
				{Type: "text", Text: "Acknowledged."},
			},
			StopReason: "end_turn",
			Usage:      bedrockUsage{InputTokens: 20, OutputTokens: 3},
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model: modelID,
		Messages: []models.Message{
			{Role: "system", Content: json.RawMessage(`"You are a pirate."`)},
			{Role: "user", Content: json.RawMessage(`"Hello"`)},
		},
	}

	_, err := p.ChatCompletion(context.Background(), req)
	if err != nil {
		t.Fatalf("ChatCompletion() error: %v", err)
	}

	if receivedSystem != "You are a pirate." {
		t.Errorf("system message = %q, want %q", receivedSystem, "You are a pirate.")
	}
}

func TestIntegration_ToolUseRoundtrip(t *testing.T) {
	modelID := "claude-3-sonnet"

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		var br bedrockRequest
		json.Unmarshal(body, &br)

		// Verify tools were sent.
		if len(br.Tools) != 1 {
			t.Errorf("expected 1 tool, got %d", len(br.Tools))
		}

		resp := bedrockResponse{
			ID:    "msg_tool",
			Type:  "message",
			Model: "claude-3-sonnet",
			Role:  "assistant",
			Content: []bedrockContentBlock{
				{
					Type:  "tool_use",
					ID:    "toolu_abc123",
					Name:  "get_weather",
					Input: json.RawMessage(`{"city":"Tokyo"}`),
				},
			},
			StopReason: "tool_use",
			Usage:      bedrockUsage{InputTokens: 30, OutputTokens: 20},
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model: modelID,
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"What is the weather in Tokyo?"`)},
		},
		Tools: []models.Tool{
			{
				Type: "function",
				Function: models.ToolFunction{
					Name:        "get_weather",
					Description: "Get weather for a city",
					Parameters:  json.RawMessage(`{"type":"object","properties":{"city":{"type":"string"}}}`),
				},
			},
		},
	}

	resp, err := p.ChatCompletion(context.Background(), req)
	if err != nil {
		t.Fatalf("ChatCompletion() error: %v", err)
	}

	if resp.Choices[0].FinishReason != "tool_calls" {
		t.Errorf("finish_reason = %q, want %q", resp.Choices[0].FinishReason, "tool_calls")
	}
	if len(resp.Choices[0].Message.ToolCalls) != 1 {
		t.Fatalf("expected 1 tool call, got %d", len(resp.Choices[0].Message.ToolCalls))
	}
	tc := resp.Choices[0].Message.ToolCalls[0]
	if tc.ID != "toolu_abc123" {
		t.Errorf("tool call id = %q, want %q", tc.ID, "toolu_abc123")
	}
	if tc.Function.Name != "get_weather" {
		t.Errorf("tool call name = %q, want %q", tc.Function.Name, "get_weather")
	}
}

// ===========================================================================
// Error handling tests
// ===========================================================================

func TestIntegration_Error429(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusTooManyRequests)
		json.NewEncoder(w).Encode(map[string]string{
			"message": "Rate limit exceeded",
			"type":    "rate_limit_error",
		})
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
	}

	_, err := p.ChatCompletion(context.Background(), req)
	if err == nil {
		t.Fatal("expected error for 429 response")
	}

	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T", err)
	}
	if apiErr.Status != 429 {
		t.Errorf("status = %d, want 429", apiErr.Status)
	}
	if apiErr.Message != "Rate limit exceeded" {
		t.Errorf("message = %q, want %q", apiErr.Message, "Rate limit exceeded")
	}
}

func TestIntegration_Error500(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{
			"message": "Internal server error",
			"type":    "server_error",
		})
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
	}

	_, err := p.ChatCompletion(context.Background(), req)
	if err == nil {
		t.Fatal("expected error for 500 response")
	}

	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T", err)
	}
	// 500 should be mapped to 502 (bad gateway)
	if apiErr.Status != 502 {
		t.Errorf("status = %d, want 502 (mapped from 500)", apiErr.Status)
	}
}

func TestIntegration_Error400(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{
			"message": "Invalid request body",
			"type":    "invalid_request_error",
		})
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
	}

	_, err := p.ChatCompletion(context.Background(), req)
	if err == nil {
		t.Fatal("expected error for 400 response")
	}

	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T", err)
	}
	if apiErr.Status != 400 {
		t.Errorf("status = %d, want 400", apiErr.Status)
	}
	if apiErr.Message != "Invalid request body" {
		t.Errorf("message = %q, want %q", apiErr.Message, "Invalid request body")
	}
}

func TestIntegration_ErrorNonJSONBody(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
		w.Write([]byte("Service Unavailable"))
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
	}

	_, err := p.ChatCompletion(context.Background(), req)
	if err == nil {
		t.Fatal("expected error for non-JSON error response")
	}

	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T", err)
	}
	// 503 should map to 502
	if apiErr.Status != 502 {
		t.Errorf("status = %d, want 502", apiErr.Status)
	}
}

// ===========================================================================
// Timeout tests
// ===========================================================================

func TestIntegration_ContextTimeout(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Simulate a slow response
		time.Sleep(2 * time.Second)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
	}

	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()

	_, err := p.ChatCompletion(ctx, req)
	if err == nil {
		t.Fatal("expected error for timed-out request")
	}

	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T", err)
	}
	// Context cancellation should result in gateway timeout
	if apiErr.Status != http.StatusGatewayTimeout {
		t.Errorf("status = %d, want %d", apiErr.Status, http.StatusGatewayTimeout)
	}
}

func TestIntegration_AlreadyCancelledContext(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Error("server should not receive request when context is already cancelled")
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
	}

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // cancel immediately

	_, err := p.ChatCompletion(ctx, req)
	if err == nil {
		t.Fatal("expected error for cancelled context")
	}
}

// ===========================================================================
// Provider ID test
// ===========================================================================

func TestIntegration_ProviderID(t *testing.T) {
	t.Setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
	t.Setenv("AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY")

	cfg := config.ProviderConfig{
		BaseURL:   "https://bedrock-runtime.us-east-1.amazonaws.com",
		APIFormat: "bedrock",
	}

	p, err := New("my-bedrock-provider", cfg)
	if err != nil {
		t.Fatalf("New() error: %v", err)
	}
	defer p.Close()

	if p.ID() != "my-bedrock-provider" {
		t.Errorf("ID() = %q, want %q", p.ID(), "my-bedrock-provider")
	}
}

// ===========================================================================
// Verify request body format in integration
// ===========================================================================

func TestIntegration_RequestBodyFormat(t *testing.T) {
	modelID := "anthropic.claude-3-haiku-20240307-v1:0"
	temp := 0.5
	maxTok := 2048

	var receivedBody map[string]interface{}

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		json.Unmarshal(body, &receivedBody)

		resp := bedrockResponse{
			ID:    "msg_fmt",
			Type:  "message",
			Model: "claude-3-haiku-20240307",
			Role:  "assistant",
			Content: []bedrockContentBlock{
				{Type: "text", Text: "OK"},
			},
			StopReason: "end_turn",
			Usage:      bedrockUsage{InputTokens: 5, OutputTokens: 1},
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model: modelID,
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hello"`)},
		},
		Temperature: &temp,
		MaxTokens:   &maxTok,
		Stop:        json.RawMessage(`["END","STOP"]`),
	}

	_, err := p.ChatCompletion(context.Background(), req)
	if err != nil {
		t.Fatalf("ChatCompletion() error: %v", err)
	}

	// Verify anthropic_version
	if v, ok := receivedBody["anthropic_version"]; !ok || v != "bedrock-2023-05-31" {
		t.Errorf("anthropic_version = %v, want %q", v, "bedrock-2023-05-31")
	}

	// Verify max_tokens
	if v, ok := receivedBody["max_tokens"]; !ok || v != float64(2048) {
		t.Errorf("max_tokens = %v, want 2048", v)
	}

	// Verify temperature
	if v, ok := receivedBody["temperature"]; !ok || v != 0.5 {
		t.Errorf("temperature = %v, want 0.5", v)
	}

	// Verify stop_sequences
	stops, ok := receivedBody["stop_sequences"].([]interface{})
	if !ok {
		t.Fatalf("stop_sequences not found or not array")
	}
	if len(stops) != 2 {
		t.Errorf("expected 2 stop sequences, got %d", len(stops))
	}

	// Verify model is NOT in body (it's in the URL path)
	if _, ok := receivedBody["model"]; ok {
		t.Error("model should not be in request body")
	}

	// Verify stream is NOT in body
	if _, ok := receivedBody["stream"]; ok {
		t.Error("stream should not be in request body")
	}
}

// ===========================================================================
// Close test
// ===========================================================================

func TestIntegration_Close(t *testing.T) {
	t.Setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
	t.Setenv("AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY")

	cfg := config.ProviderConfig{
		BaseURL:   "https://bedrock-runtime.us-east-1.amazonaws.com",
		APIFormat: "bedrock",
	}

	p, err := New("test-bedrock", cfg)
	if err != nil {
		t.Fatalf("New() error: %v", err)
	}

	// Close should not panic or error
	if err := p.Close(); err != nil {
		t.Errorf("Close() error: %v", err)
	}
}
