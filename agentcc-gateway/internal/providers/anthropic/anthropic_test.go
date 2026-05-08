package anthropic

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
	p, err := New("test-provider", cfg)
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
		BaseURL: "https://api.anthropic.com",
		APIKey:  "sk-test",
	}
	p, err := New("test", cfg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	defer p.Close()

	if p.id != "test" {
		t.Errorf("id = %q, want %q", p.id, "test")
	}
	if p.baseURL != "https://api.anthropic.com" {
		t.Errorf("baseURL = %q, want %q", p.baseURL, "https://api.anthropic.com")
	}
	if p.apiKey != "sk-test" {
		t.Errorf("apiKey = %q, want %q", p.apiKey, "sk-test")
	}
	// Default timeout is 120s.
	if p.httpClient.Timeout != 120*time.Second {
		t.Errorf("timeout = %v, want %v", p.httpClient.Timeout, 120*time.Second)
	}
	// Default semaphore capacity is 100.
	if cap(p.semaphore) != 100 {
		t.Errorf("semaphore cap = %d, want 100", cap(p.semaphore))
	}
}

func TestNew_TrailingSlashTrimmed(t *testing.T) {
	cfg := config.ProviderConfig{
		BaseURL: "https://api.anthropic.com/",
		APIKey:  "sk-test",
	}
	p, err := New("test", cfg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	defer p.Close()

	if p.baseURL != "https://api.anthropic.com" {
		t.Errorf("baseURL = %q, want trailing slash trimmed", p.baseURL)
	}
}

func TestNew_CustomConfig(t *testing.T) {
	cfg := config.ProviderConfig{
		BaseURL:        "https://custom.api.com",
		APIKey:         "custom-key",
		DefaultTimeout: 30 * time.Second,
		MaxConcurrent:  5,
		ConnPoolSize:   10,
		Headers: map[string]string{
			"anthropic-version": "2024-10-22",
		},
	}
	p, err := New("custom", cfg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	defer p.Close()

	if p.httpClient.Timeout != 30*time.Second {
		t.Errorf("timeout = %v, want %v", p.httpClient.Timeout, 30*time.Second)
	}
	if cap(p.semaphore) != 5 {
		t.Errorf("semaphore cap = %d, want 5", cap(p.semaphore))
	}
	if p.headers["anthropic-version"] != "2024-10-22" {
		t.Errorf("anthropic-version header = %q, want %q", p.headers["anthropic-version"], "2024-10-22")
	}
}

func TestProviderID(t *testing.T) {
	cfg := config.ProviderConfig{
		BaseURL: "https://api.anthropic.com",
		APIKey:  "sk-test",
	}
	p, err := New("my-id", cfg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	defer p.Close()

	if p.ID() != "my-id" {
		t.Errorf("ID() = %q, want %q", p.ID(), "my-id")
	}
}

func TestListModels_ReturnsNil(t *testing.T) {
	cfg := config.ProviderConfig{
		BaseURL: "https://api.anthropic.com",
		APIKey:  "sk-test",
	}
	p, err := New("test", cfg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	defer p.Close()

	models, err := p.ListModels(context.Background())
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
	if models != nil {
		t.Errorf("expected nil, got %v", models)
	}
}

// ---------------------------------------------------------------------------
// Integration: Non-streaming chat completion
// ---------------------------------------------------------------------------

func TestIntegration_NonStreamingChatCompletion(t *testing.T) {
	var receivedAPIKey string
	var receivedVersion string
	var receivedContentType string

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedAPIKey = r.Header.Get("x-api-key")
		receivedVersion = r.Header.Get("anthropic-version")
		receivedContentType = r.Header.Get("Content-Type")

		// Verify it is a POST to /v1/messages.
		if r.Method != http.MethodPost {
			t.Errorf("method = %q, want POST", r.Method)
		}
		if r.URL.Path != "/v1/messages" {
			t.Errorf("path = %q, want /v1/messages", r.URL.Path)
		}

		// Read and verify the request body.
		body, _ := io.ReadAll(r.Body)
		var ar anthropicRequest
		if err := json.Unmarshal(body, &ar); err != nil {
			t.Errorf("failed to unmarshal request: %v", err)
			http.Error(w, "bad request", 400)
			return
		}
		if ar.Model != "claude-3-sonnet-20240229" {
			t.Errorf("request model = %q, want %q", ar.Model, "claude-3-sonnet-20240229")
		}
		if ar.Stream {
			t.Error("stream should be false for non-streaming")
		}

		// Send valid response.
		resp := anthropicResponse{
			ID:    "msg_integration",
			Type:  "message",
			Model: "claude-3-sonnet-20240229",
			Role:  "assistant",
			Content: []anthropicContentBlock{
				{Type: "text", Text: "Hello from the mock!"},
			},
			StopReason: "end_turn",
			Usage:      anthropicUsage{InputTokens: 10, OutputTokens: 8},
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet-20240229",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hello!"`)},
		},
	}

	result, err := p.ChatCompletion(context.Background(), req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// Verify headers were sent.
	if receivedAPIKey != "test-api-key" {
		t.Errorf("x-api-key = %q, want %q", receivedAPIKey, "test-api-key")
	}
	if receivedVersion != "2023-06-01" {
		t.Errorf("anthropic-version = %q, want %q (default)", receivedVersion, "2023-06-01")
	}
	if receivedContentType != "application/json" {
		t.Errorf("content-type = %q, want %q", receivedContentType, "application/json")
	}

	// Verify response.
	if result.ID != "msg_integration" {
		t.Errorf("id = %q, want %q", result.ID, "msg_integration")
	}
	if result.Object != "chat.completion" {
		t.Errorf("object = %q, want %q", result.Object, "chat.completion")
	}
	if result.Model != "claude-3-sonnet-20240229" {
		t.Errorf("model = %q, want %q", result.Model, "claude-3-sonnet-20240229")
	}
	if result.Created == 0 {
		t.Fatal("created should be non-zero")
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
	if content != "Hello from the mock!" {
		t.Errorf("content = %q, want %q", content, "Hello from the mock!")
	}

	// Verify usage.
	if result.Usage == nil {
		t.Fatal("usage is nil")
	}
	if result.Usage.PromptTokens != 10 {
		t.Errorf("prompt_tokens = %d, want 10", result.Usage.PromptTokens)
	}
	if result.Usage.CompletionTokens != 8 {
		t.Errorf("completion_tokens = %d, want 8", result.Usage.CompletionTokens)
	}
	if result.Usage.TotalTokens != 18 {
		t.Errorf("total_tokens = %d, want 18", result.Usage.TotalTokens)
	}
}

// ---------------------------------------------------------------------------
// Integration: Streaming chat completion
// ---------------------------------------------------------------------------

func TestIntegration_StreamingChatCompletion(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Verify request.
		body, _ := io.ReadAll(r.Body)
		var ar anthropicRequest
		if err := json.Unmarshal(body, &ar); err != nil {
			t.Errorf("failed to unmarshal request: %v", err)
			http.Error(w, "bad request", 400)
			return
		}
		if !ar.Stream {
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
			"event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"id\":\"msg_stream\",\"model\":\"claude-3-sonnet-20240229\",\"usage\":{\"input_tokens\":15}}}\n\n",
			"event: content_block_start\ndata: {\"type\":\"content_block_start\",\"index\":0,\"content_block\":{\"type\":\"text\",\"text\":\"\"}}\n\n",
			"event: ping\ndata: {\"type\":\"ping\"}\n\n",
			"event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\"Hi\"}}\n\n",
			"event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\" there!\"}}\n\n",
			"event: content_block_stop\ndata: {\"type\":\"content_block_stop\",\"index\":0}\n\n",
			"event: message_delta\ndata: {\"type\":\"message_delta\",\"delta\":{\"stop_reason\":\"end_turn\"},\"usage\":{\"output_tokens\":5}}\n\n",
			"event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n",
		}

		for _, event := range events {
			fmt.Fprint(w, event)
			flusher.Flush()
		}
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model:  "claude-3-sonnet-20240229",
		Stream: true,
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hello!"`)},
		},
	}

	chunks, errs := p.StreamChatCompletion(context.Background(), req)

	var received []models.StreamChunk
	for chunk := range chunks {
		received = append(received, chunk)
	}
	// Drain errors.
	var streamErr error
	for e := range errs {
		streamErr = e
	}
	if streamErr != nil {
		t.Fatalf("unexpected stream error: %v", streamErr)
	}

	// Expected: message_start (role), text "Hi", text " there!", message_delta (finish)
	if len(received) < 4 {
		t.Fatalf("received %d chunks, want at least 4", len(received))
	}

	// First chunk: role.
	if received[0].Choices[0].Delta.Role != "assistant" {
		t.Errorf("chunk[0] role = %q, want %q", received[0].Choices[0].Delta.Role, "assistant")
	}
	if received[0].ID != "msg_stream" {
		t.Errorf("chunk[0] ID = %q, want %q", received[0].ID, "msg_stream")
	}
	if received[0].Model != "claude-3-sonnet-20240229" {
		t.Errorf("chunk[0] model = %q, want %q", received[0].Model, "claude-3-sonnet-20240229")
	}
	if received[0].Created == 0 {
		t.Fatal("chunk[0] created should be non-zero")
	}

	// Text chunks.
	if received[1].Choices[0].Delta.Content == nil || *received[1].Choices[0].Delta.Content != "Hi" {
		t.Errorf("chunk[1] content = %v, want %q", received[1].Choices[0].Delta.Content, "Hi")
	}
	if received[2].Choices[0].Delta.Content == nil || *received[2].Choices[0].Delta.Content != " there!" {
		t.Errorf("chunk[2] content = %v, want %q", received[2].Choices[0].Delta.Content, " there!")
	}

	// Finish reason chunk.
	last := received[len(received)-1]
	if last.Choices[0].FinishReason == nil || *last.Choices[0].FinishReason != "stop" {
		t.Errorf("last chunk finish_reason = %v, want %q", last.Choices[0].FinishReason, "stop")
	}
}

// ---------------------------------------------------------------------------
// Integration: Error handling - 429 rate limit
// ---------------------------------------------------------------------------

func TestIntegration_Error429RateLimit(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusTooManyRequests)
		resp := anthropicErrorResponse{
			Type: "error",
			Error: anthropicErrorDetail{
				Type:    "rate_limit_error",
				Message: "You have exceeded the rate limit.",
			},
		}
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet-20240229",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
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
	if apiErr.Type != models.ErrTypeRateLimit {
		t.Errorf("type = %q, want %q", apiErr.Type, models.ErrTypeRateLimit)
	}
	if !strings.Contains(apiErr.Message, "rate limit") {
		t.Errorf("message = %q, want it to mention rate limit", apiErr.Message)
	}
}

// ---------------------------------------------------------------------------
// Integration: Error handling - 500 server error
// ---------------------------------------------------------------------------

func TestIntegration_Error500ServerError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		resp := anthropicErrorResponse{
			Type: "error",
			Error: anthropicErrorDetail{
				Type:    "api_error",
				Message: "Internal server error occurred.",
			},
		}
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet-20240229",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
	}

	_, err := p.ChatCompletion(context.Background(), req)
	if err == nil {
		t.Fatal("expected error, got nil")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T: %v", err, err)
	}
	// "api_error" type maps to default branch -> StatusBadGateway.
	if apiErr.Status != http.StatusBadGateway {
		t.Errorf("status = %d, want %d", apiErr.Status, http.StatusBadGateway)
	}
}

// ---------------------------------------------------------------------------
// Integration: Error handling - non-JSON error body
// ---------------------------------------------------------------------------

func TestIntegration_ErrorNonJSON(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/plain")
		w.WriteHeader(http.StatusBadGateway)
		fmt.Fprint(w, "Bad Gateway: upstream failed")
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet-20240229",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
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
		t.Errorf("message = %q, want it to contain the raw body text", apiErr.Message)
	}
}

// ---------------------------------------------------------------------------
// Integration: Streaming error handling - 429 on stream
// ---------------------------------------------------------------------------

func TestIntegration_StreamingError429(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusTooManyRequests)
		resp := anthropicErrorResponse{
			Type: "error",
			Error: anthropicErrorDetail{
				Type:    "rate_limit_error",
				Message: "Rate limited on stream.",
			},
		}
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model:  "claude-3-sonnet-20240229",
		Stream: true,
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
	}

	chunks, errs := p.StreamChatCompletion(context.Background(), req)

	// Drain chunks - should be empty.
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

// ---------------------------------------------------------------------------
// Integration: Timeout handling
// ---------------------------------------------------------------------------

func TestIntegration_Timeout(t *testing.T) {
	// Use a channel to unblock the handler so httptest.Server.Close() does not hang.
	unblock := make(chan struct{})

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Block until either the test signals us or the server-side request
		// context is cancelled (which happens when the connection is closed).
		select {
		case <-unblock:
		case <-r.Context().Done():
		}
	}))
	defer func() {
		close(unblock) // Unblock any handler still waiting.
		server.Close()
	}()

	// Use a context-based timeout to avoid http.Client.Timeout issues with
	// httptest.Server.Close() waiting for active connections.
	cfg := config.ProviderConfig{
		BaseURL:        server.URL,
		APIKey:         "test-key",
		DefaultTimeout: 30 * time.Second, // Long http client timeout.
		MaxConcurrent:  10,
		ConnPoolSize:   5,
	}
	p, err := New("timeout-test", cfg)
	if err != nil {
		t.Fatalf("failed to create provider: %v", err)
	}
	defer p.Close()

	// Use a context with a very short deadline to trigger timeout.
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()

	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet-20240229",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
	}

	_, err = p.ChatCompletion(ctx, req)
	if err == nil {
		t.Fatal("expected timeout error, got nil")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T: %v", err, err)
	}
	// Context timeout path yields ErrGatewayTimeout.
	if apiErr.Status != http.StatusGatewayTimeout {
		t.Errorf("status = %d, want %d", apiErr.Status, http.StatusGatewayTimeout)
	}
}

// ---------------------------------------------------------------------------
// Integration: Context cancellation on stream
// ---------------------------------------------------------------------------

func TestIntegration_StreamingContextCancellation(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)

		flusher, ok := w.(http.Flusher)
		if !ok {
			return
		}

		// Send initial event then wait for disconnect.
		fmt.Fprint(w, "event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"id\":\"msg_cancel\",\"model\":\"claude-3\",\"usage\":{\"input_tokens\":5}}}\n\n")
		flusher.Flush()

		// Wait for client to disconnect.
		<-r.Context().Done()
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	req := &models.ChatCompletionRequest{
		Model:  "claude-3",
		Stream: true,
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
	}

	chunks, errs := p.StreamChatCompletion(ctx, req)

	var received []models.StreamChunk
	for chunk := range chunks {
		received = append(received, chunk)
	}
	// Drain errors.
	for range errs {
	}

	// Should have received the initial message_start chunk.
	if len(received) == 0 {
		t.Error("expected at least 1 chunk before cancellation")
	}
}

// ---------------------------------------------------------------------------
// Integration: Custom headers
// ---------------------------------------------------------------------------

func TestIntegration_SetHeaders_CustomAnthropicVersion(t *testing.T) {
	var receivedVersion string
	var receivedCustom string

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedVersion = r.Header.Get("anthropic-version")
		receivedCustom = r.Header.Get("x-custom-header")

		resp := anthropicResponse{
			ID:    "msg_hdr",
			Model: "claude-3",
			Role:  "assistant",
			Content: []anthropicContentBlock{
				{Type: "text", Text: "ok"},
			},
			StopReason: "end_turn",
			Usage:      anthropicUsage{InputTokens: 1, OutputTokens: 1},
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	cfg := config.ProviderConfig{
		BaseURL:       server.URL,
		APIKey:        "test-key",
		MaxConcurrent: 10,
		ConnPoolSize:  5,
		Headers: map[string]string{
			"anthropic-version": "2024-10-22",
			"x-custom-header":   "custom-value",
		},
	}
	p, err := New("header-test", cfg)
	if err != nil {
		t.Fatalf("failed to create provider: %v", err)
	}
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model: "claude-3",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
	}

	_, err = p.ChatCompletion(context.Background(), req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if receivedVersion != "2024-10-22" {
		t.Errorf("anthropic-version = %q, want %q", receivedVersion, "2024-10-22")
	}
	if receivedCustom != "custom-value" {
		t.Errorf("x-custom-header = %q, want %q", receivedCustom, "custom-value")
	}
}

// ---------------------------------------------------------------------------
// Integration: Non-streaming tool use roundtrip
// ---------------------------------------------------------------------------

func TestIntegration_NonStreamingToolUseRoundtrip(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		var ar anthropicRequest
		if err := json.Unmarshal(body, &ar); err != nil {
			t.Errorf("failed to parse request: %v", err)
			http.Error(w, "bad", 400)
			return
		}

		// Verify tools were translated.
		if len(ar.Tools) != 1 {
			t.Errorf("tools length = %d, want 1", len(ar.Tools))
		}
		if ar.Tools[0].Name != "get_weather" {
			t.Errorf("tool name = %q, want %q", ar.Tools[0].Name, "get_weather")
		}

		// Return a tool_use response.
		resp := anthropicResponse{
			ID:    "msg_tool_roundtrip",
			Type:  "message",
			Model: "claude-3-sonnet-20240229",
			Role:  "assistant",
			Content: []anthropicContentBlock{
				{
					Type:  "tool_use",
					ID:    "toolu_abc123",
					Name:  "get_weather",
					Input: json.RawMessage(`{"location":"NYC"}`),
				},
			},
			StopReason: "tool_use",
			Usage:      anthropicUsage{InputTokens: 30, OutputTokens: 20},
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet-20240229",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"What's the weather in NYC?"`)},
		},
		Tools: []models.Tool{
			{
				Type: "function",
				Function: models.ToolFunction{
					Name:        "get_weather",
					Description: "Get weather",
					Parameters:  json.RawMessage(`{"type":"object","properties":{"location":{"type":"string"}}}`),
				},
			},
		},
		ToolChoice: json.RawMessage(`"auto"`),
	}

	resp, err := p.ChatCompletion(context.Background(), req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if resp.Choices[0].FinishReason != "tool_calls" {
		t.Errorf("finish_reason = %q, want %q", resp.Choices[0].FinishReason, "tool_calls")
	}
	if len(resp.Choices[0].Message.ToolCalls) != 1 {
		t.Fatalf("tool_calls length = %d, want 1", len(resp.Choices[0].Message.ToolCalls))
	}
	tc := resp.Choices[0].Message.ToolCalls[0]
	if tc.ID != "toolu_abc123" {
		t.Errorf("tool call ID = %q, want %q", tc.ID, "toolu_abc123")
	}
	if tc.Function.Name != "get_weather" {
		t.Errorf("tool call name = %q, want %q", tc.Function.Name, "get_weather")
	}
}
