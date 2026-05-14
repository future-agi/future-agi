package gemini

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
// New() tests
// ---------------------------------------------------------------------------

func TestNew_DefaultValues(t *testing.T) {
	p, err := New("test-gemini", config.ProviderConfig{
		BaseURL: "https://generativelanguage.googleapis.com",
		APIKey:  "test-key",
	})
	if err != nil {
		t.Fatalf("New() error = %v", err)
	}

	if p.id != "test-gemini" {
		t.Errorf("id = %q, want %q", p.id, "test-gemini")
	}
	if p.baseURL != "https://generativelanguage.googleapis.com" {
		t.Errorf("baseURL = %q, want %q", p.baseURL, "https://generativelanguage.googleapis.com")
	}
	if p.apiKey != "test-key" {
		t.Errorf("apiKey = %q, want %q", p.apiKey, "test-key")
	}
	if p.httpClient.Timeout != 120*time.Second {
		t.Errorf("timeout = %v, want %v", p.httpClient.Timeout, 120*time.Second)
	}
	if cap(p.semaphore) != 100 {
		t.Errorf("semaphore capacity = %d, want 100", cap(p.semaphore))
	}
}

func TestNew_CustomValues(t *testing.T) {
	p, err := New("custom", config.ProviderConfig{
		BaseURL:        "https://custom.example.com/",
		APIKey:         "my-key",
		DefaultTimeout: 30 * time.Second,
		MaxConcurrent:  50,
		ConnPoolSize:   25,
		Headers:        map[string]string{"X-Custom": "value"},
	})
	if err != nil {
		t.Fatalf("New() error = %v", err)
	}

	// Trailing slash should be trimmed.
	if p.baseURL != "https://custom.example.com" {
		t.Errorf("baseURL = %q, want trailing slash trimmed", p.baseURL)
	}
	if p.httpClient.Timeout != 30*time.Second {
		t.Errorf("timeout = %v, want %v", p.httpClient.Timeout, 30*time.Second)
	}
	if cap(p.semaphore) != 50 {
		t.Errorf("semaphore capacity = %d, want 50", cap(p.semaphore))
	}
	if p.headers["X-Custom"] != "value" {
		t.Errorf("headers[X-Custom] = %q, want %q", p.headers["X-Custom"], "value")
	}
}

func TestProvider_ID(t *testing.T) {
	p, err := New("my-provider", config.ProviderConfig{
		BaseURL: "https://example.com",
	})
	if err != nil {
		t.Fatalf("New() error = %v", err)
	}
	if p.ID() != "my-provider" {
		t.Errorf("ID() = %q, want %q", p.ID(), "my-provider")
	}
}

func TestProvider_ListModels(t *testing.T) {
	p, err := New("test", config.ProviderConfig{
		BaseURL: "https://example.com",
	})
	if err != nil {
		t.Fatalf("New() error = %v", err)
	}
	result, err := p.ListModels(context.Background())
	if err != nil {
		t.Fatalf("ListModels() error = %v", err)
	}
	if result != nil {
		t.Errorf("ListModels() = %v, want nil", result)
	}
}

func TestProvider_Close(t *testing.T) {
	p, err := New("test", config.ProviderConfig{
		BaseURL: "https://example.com",
	})
	if err != nil {
		t.Fatalf("New() error = %v", err)
	}
	if err := p.Close(); err != nil {
		t.Errorf("Close() error = %v", err)
	}
}

// ---------------------------------------------------------------------------
// helpers for integration tests
// ---------------------------------------------------------------------------

func newTestProvider(t *testing.T, serverURL string) *Provider {
	t.Helper()
	p, err := New("test-gemini", config.ProviderConfig{
		BaseURL:        serverURL,
		APIKey:         "test-api-key",
		DefaultTimeout: 10 * time.Second,
		MaxConcurrent:  10,
	})
	if err != nil {
		t.Fatalf("New() error = %v", err)
	}
	return p
}

func basicRequest() *models.ChatCompletionRequest {
	return &models.ChatCompletionRequest{
		Model: "gemini-1.5-pro",
		Messages: []models.Message{
			{Role: "user", Content: mustJSON("Hello")},
		},
	}
}

// ---------------------------------------------------------------------------
// Non-streaming integration tests
// ---------------------------------------------------------------------------

func TestIntegration_NonStreamingRoundTrip(t *testing.T) {
	geminiResp := geminiResponse{
		Candidates: []geminiCandidate{
			{
				Content: geminiContent{
					Role: "model",
					Parts: []geminiPart{
						{Text: "Hello from Gemini!"},
					},
				},
				FinishReason: "STOP",
			},
		},
		UsageMetadata: &geminiUsageMetadata{
			PromptTokenCount:     5,
			CandidatesTokenCount: 4,
			TotalTokenCount:      9,
		},
	}

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Verify request method and content type.
		if r.Method != "POST" {
			t.Errorf("Method = %q, want POST", r.Method)
		}
		if ct := r.Header.Get("Content-Type"); ct != "application/json" {
			t.Errorf("Content-Type = %q, want application/json", ct)
		}
		// Verify URL pattern.
		if !strings.Contains(r.URL.Path, "/v1beta/models/gemini-1.5-pro:generateContent") {
			t.Errorf("URL path = %q, want to contain generateContent", r.URL.Path)
		}
		// Verify API key in query.
		if r.URL.Query().Get("key") != "test-api-key" {
			t.Errorf("key param = %q, want %q", r.URL.Query().Get("key"), "test-api-key")
		}
		// Verify request body.
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("reading body: %v", err)
		}
		var gr geminiRequest
		if err := json.Unmarshal(body, &gr); err != nil {
			t.Fatalf("unmarshaling body: %v", err)
		}
		if len(gr.Contents) != 1 {
			t.Errorf("contents length = %d, want 1", len(gr.Contents))
		}
		if gr.Contents[0].Role != "user" {
			t.Errorf("role = %q, want user", gr.Contents[0].Role)
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(geminiResp)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	resp, err := p.ChatCompletion(context.Background(), basicRequest())
	if err != nil {
		t.Fatalf("ChatCompletion() error = %v", err)
	}

	if resp.Object != "chat.completion" {
		t.Errorf("Object = %q, want %q", resp.Object, "chat.completion")
	}
	if resp.Model != "gemini-1.5-pro" {
		t.Errorf("Model = %q, want %q", resp.Model, "gemini-1.5-pro")
	}
	if !strings.HasPrefix(resp.ID, "gen-") {
		t.Errorf("ID = %q, want prefix %q", resp.ID, "gen-")
	}
	if resp.Created == 0 {
		t.Fatal("Created should be non-zero")
	}
	if len(resp.Choices) != 1 {
		t.Fatalf("Choices length = %d, want 1", len(resp.Choices))
	}
	if resp.Choices[0].FinishReason != "stop" {
		t.Errorf("FinishReason = %q, want %q", resp.Choices[0].FinishReason, "stop")
	}
	if resp.Choices[0].Message.Role != "assistant" {
		t.Errorf("Role = %q, want %q", resp.Choices[0].Message.Role, "assistant")
	}

	var content string
	if err := json.Unmarshal(resp.Choices[0].Message.Content, &content); err != nil {
		t.Fatalf("unmarshal content: %v", err)
	}
	if content != "Hello from Gemini!" {
		t.Errorf("content = %q, want %q", content, "Hello from Gemini!")
	}

	if resp.Usage == nil {
		t.Fatal("Usage should not be nil")
	}
	if resp.Usage.PromptTokens != 5 {
		t.Errorf("PromptTokens = %d, want 5", resp.Usage.PromptTokens)
	}
	if resp.Usage.CompletionTokens != 4 {
		t.Errorf("CompletionTokens = %d, want 4", resp.Usage.CompletionTokens)
	}
	if resp.Usage.TotalTokens != 9 {
		t.Errorf("TotalTokens = %d, want 9", resp.Usage.TotalTokens)
	}
}

func TestIntegration_NonStreamingWithToolCalls(t *testing.T) {
	geminiResp := geminiResponse{
		Candidates: []geminiCandidate{
			{
				Content: geminiContent{
					Role: "model",
					Parts: []geminiPart{
						{
							FunctionCall: &geminiFunctionCall{
								Name: "get_weather",
								Args: json.RawMessage(`{"location":"Seattle"}`),
							},
						},
					},
				},
				FinishReason: "STOP",
			},
		},
	}

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(geminiResp)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	resp, err := p.ChatCompletion(context.Background(), basicRequest())
	if err != nil {
		t.Fatalf("ChatCompletion() error = %v", err)
	}

	if len(resp.Choices[0].Message.ToolCalls) != 1 {
		t.Fatalf("ToolCalls length = %d, want 1", len(resp.Choices[0].Message.ToolCalls))
	}
	tc := resp.Choices[0].Message.ToolCalls[0]
	if tc.Function.Name != "get_weather" {
		t.Errorf("Function.Name = %q, want %q", tc.Function.Name, "get_weather")
	}
	if tc.Function.Arguments != `{"location":"Seattle"}` {
		t.Errorf("Function.Arguments = %q, want %q", tc.Function.Arguments, `{"location":"Seattle"}`)
	}
}

// ---------------------------------------------------------------------------
// Streaming integration tests
// ---------------------------------------------------------------------------

func TestIntegration_StreamingRoundTrip(t *testing.T) {
	chunk1 := `{"candidates":[{"content":{"role":"model","parts":[{"text":"Hello"}]}}]}`
	chunk2 := `{"candidates":[{"content":{"role":"model","parts":[{"text":" world"}]}}]}`
	chunk3 := `{"candidates":[{"content":{"role":"model","parts":[{"text":"!"}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":3,"totalTokenCount":8}}`

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Verify streaming URL pattern.
		if !strings.Contains(r.URL.Path, "streamGenerateContent") {
			t.Errorf("URL path = %q, want to contain streamGenerateContent", r.URL.Path)
		}
		if r.URL.Query().Get("alt") != "sse" {
			t.Errorf("alt param = %q, want %q", r.URL.Query().Get("alt"), "sse")
		}
		// Verify API key in query (appended with &).
		if r.URL.Query().Get("key") != "test-api-key" {
			t.Errorf("key param = %q, want %q", r.URL.Query().Get("key"), "test-api-key")
		}

		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		flusher, ok := w.(http.Flusher)
		if !ok {
			t.Fatal("response writer does not support flushing")
		}

		for _, c := range []string{chunk1, chunk2, chunk3} {
			fmt.Fprintf(w, "data: %s\n\n", c)
			flusher.Flush()
		}
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	req := basicRequest()
	req.Stream = true

	chunks, errs := p.StreamChatCompletion(context.Background(), req)

	var allContent []string
	var lastFinishReason *string
	var lastUsage *models.Usage

	for chunk := range chunks {
		if !strings.HasPrefix(chunk.ID, "gen-") {
			t.Errorf("chunk ID = %q, want prefix 'gen-'", chunk.ID)
		}
		if chunk.Object != "chat.completion.chunk" {
			t.Errorf("Object = %q, want %q", chunk.Object, "chat.completion.chunk")
		}
		if chunk.Model != "gemini-1.5-pro" {
			t.Errorf("Model = %q, want %q", chunk.Model, "gemini-1.5-pro")
		}
		if chunk.Created == 0 {
			t.Fatal("Created should be non-zero")
		}
		for _, sc := range chunk.Choices {
			if sc.Delta.Content != nil {
				allContent = append(allContent, *sc.Delta.Content)
			}
			if sc.FinishReason != nil {
				lastFinishReason = sc.FinishReason
			}
		}
		if chunk.Usage != nil {
			lastUsage = chunk.Usage
		}
	}

	// Check for errors from the error channel.
	for err := range errs {
		if err != nil {
			t.Fatalf("stream error: %v", err)
		}
	}

	combined := strings.Join(allContent, "")
	if combined != "Hello world!" {
		t.Errorf("combined content = %q, want %q", combined, "Hello world!")
	}
	if lastFinishReason == nil || *lastFinishReason != "stop" {
		t.Errorf("last FinishReason = %v, want %q", lastFinishReason, "stop")
	}
	if lastUsage == nil {
		t.Fatal("expected usage in final chunk")
	}
	if lastUsage.PromptTokens != 5 {
		t.Errorf("PromptTokens = %d, want 5", lastUsage.PromptTokens)
	}
	if lastUsage.CompletionTokens != 3 {
		t.Errorf("CompletionTokens = %d, want 3", lastUsage.CompletionTokens)
	}
	if lastUsage.TotalTokens != 8 {
		t.Errorf("TotalTokens = %d, want 8", lastUsage.TotalTokens)
	}
}

func TestIntegration_StreamingWithFunctionCall(t *testing.T) {
	chunk := `{"candidates":[{"content":{"role":"model","parts":[{"functionCall":{"name":"search","args":{"q":"test"}}}]},"finishReason":"STOP"}]}`

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		fmt.Fprintf(w, "data: %s\n\n", chunk)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	req := basicRequest()
	req.Stream = true

	chunks, errs := p.StreamChatCompletion(context.Background(), req)

	var gotToolCall bool
	for chunk := range chunks {
		for _, sc := range chunk.Choices {
			if len(sc.Delta.ToolCalls) > 0 {
				gotToolCall = true
				if sc.Delta.ToolCalls[0].Function.Name != "search" {
					t.Errorf("Function.Name = %q, want %q", sc.Delta.ToolCalls[0].Function.Name, "search")
				}
			}
		}
	}
	for err := range errs {
		if err != nil {
			t.Fatalf("stream error: %v", err)
		}
	}

	if !gotToolCall {
		t.Error("expected to receive a tool call in stream")
	}
}

// ---------------------------------------------------------------------------
// Error handling tests
// ---------------------------------------------------------------------------

func TestIntegration_Error429(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusTooManyRequests)
		w.Write([]byte(`{"error":{"code":429,"message":"Quota exceeded","status":"RESOURCE_EXHAUSTED"}}`))
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	_, err := p.ChatCompletion(context.Background(), basicRequest())

	if err == nil {
		t.Fatal("expected error for 429 response")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("error type = %T, want *models.APIError", err)
	}
	if apiErr.Status != http.StatusTooManyRequests {
		t.Errorf("Status = %d, want %d", apiErr.Status, http.StatusTooManyRequests)
	}
	if apiErr.Type != models.ErrTypeRateLimit {
		t.Errorf("Type = %q, want %q", apiErr.Type, models.ErrTypeRateLimit)
	}
	if apiErr.Message != "Quota exceeded" {
		t.Errorf("Message = %q, want %q", apiErr.Message, "Quota exceeded")
	}
}

func TestIntegration_Error500(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		w.Write([]byte(`{"error":{"code":500,"message":"Internal server error","status":"INTERNAL"}}`))
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	_, err := p.ChatCompletion(context.Background(), basicRequest())

	if err == nil {
		t.Fatal("expected error for 500 response")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("error type = %T, want *models.APIError", err)
	}
	// Internal errors map to Bad Gateway (upstream error).
	if apiErr.Status != http.StatusBadGateway {
		t.Errorf("Status = %d, want %d", apiErr.Status, http.StatusBadGateway)
	}
	if apiErr.Type != models.ErrTypeUpstream {
		t.Errorf("Type = %q, want %q", apiErr.Type, models.ErrTypeUpstream)
	}
}

func TestIntegration_Error400(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		w.Write([]byte(`{"error":{"code":400,"message":"Invalid argument provided","status":"INVALID_ARGUMENT"}}`))
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	_, err := p.ChatCompletion(context.Background(), basicRequest())

	if err == nil {
		t.Fatal("expected error for 400 response")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("error type = %T, want *models.APIError", err)
	}
	if apiErr.Status != http.StatusBadRequest {
		t.Errorf("Status = %d, want %d", apiErr.Status, http.StatusBadRequest)
	}
	if apiErr.Type != models.ErrTypeInvalidRequest {
		t.Errorf("Type = %q, want %q", apiErr.Type, models.ErrTypeInvalidRequest)
	}
}

func TestIntegration_StreamError429(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusTooManyRequests)
		w.Write([]byte(`{"error":{"code":429,"message":"Quota exceeded","status":"RESOURCE_EXHAUSTED"}}`))
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	req := basicRequest()
	req.Stream = true

	chunks, errs := p.StreamChatCompletion(context.Background(), req)

	// Drain chunks.
	for range chunks {
	}

	var gotErr error
	for err := range errs {
		if err != nil {
			gotErr = err
		}
	}
	if gotErr == nil {
		t.Fatal("expected error for 429 streaming response")
	}
	apiErr, ok := gotErr.(*models.APIError)
	if !ok {
		t.Fatalf("error type = %T, want *models.APIError", gotErr)
	}
	if apiErr.Status != http.StatusTooManyRequests {
		t.Errorf("Status = %d, want %d", apiErr.Status, http.StatusTooManyRequests)
	}
}

func TestIntegration_StreamError500(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		w.Write([]byte(`{"error":{"code":500,"message":"Server error","status":"INTERNAL"}}`))
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	req := basicRequest()
	req.Stream = true

	chunks, errs := p.StreamChatCompletion(context.Background(), req)
	for range chunks {
	}

	var gotErr error
	for err := range errs {
		if err != nil {
			gotErr = err
		}
	}
	if gotErr == nil {
		t.Fatal("expected error for 500 streaming response")
	}
	apiErr, ok := gotErr.(*models.APIError)
	if !ok {
		t.Fatalf("error type = %T, want *models.APIError", gotErr)
	}
	if apiErr.Status != http.StatusBadGateway {
		t.Errorf("Status = %d, want %d", apiErr.Status, http.StatusBadGateway)
	}
}

// ---------------------------------------------------------------------------
// API key in URL tests
// ---------------------------------------------------------------------------

func TestIntegration_APIKeyInQueryParam(t *testing.T) {
	var capturedKey string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		capturedKey = r.URL.Query().Get("key")
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(geminiResponse{
			Candidates: []geminiCandidate{
				{
					Content:      geminiContent{Parts: []geminiPart{{Text: "ok"}}},
					FinishReason: "STOP",
				},
			},
		})
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	_, err := p.ChatCompletion(context.Background(), basicRequest())
	if err != nil {
		t.Fatalf("ChatCompletion() error = %v", err)
	}
	if capturedKey != "test-api-key" {
		t.Errorf("API key in query = %q, want %q", capturedKey, "test-api-key")
	}
}

func TestIntegration_NoAPIKey(t *testing.T) {
	var gotKeyParam bool
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("key") != "" {
			gotKeyParam = true
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(geminiResponse{
			Candidates: []geminiCandidate{
				{
					Content:      geminiContent{Parts: []geminiPart{{Text: "ok"}}},
					FinishReason: "STOP",
				},
			},
		})
	}))
	defer server.Close()

	p, err := New("test", config.ProviderConfig{
		BaseURL:       server.URL,
		APIKey:        "", // No API key.
		MaxConcurrent: 10,
	})
	if err != nil {
		t.Fatalf("New() error = %v", err)
	}

	_, err = p.ChatCompletion(context.Background(), basicRequest())
	if err != nil {
		t.Fatalf("ChatCompletion() error = %v", err)
	}
	if gotKeyParam {
		t.Error("should not include key query param when API key is empty")
	}
}

func TestIntegration_StreamAPIKeyAppendedWithAmpersand(t *testing.T) {
	var capturedURL string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		capturedURL = r.URL.String()
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		fmt.Fprintf(w, "data: %s\n\n", `{"candidates":[{"content":{"parts":[{"text":"ok"}]},"finishReason":"STOP"}]}`)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	req := basicRequest()
	req.Stream = true

	chunks, errs := p.StreamChatCompletion(context.Background(), req)
	for range chunks {
	}
	for err := range errs {
		if err != nil {
			t.Fatalf("stream error: %v", err)
		}
	}

	// Streaming URL should have ?alt=sse&key=...
	if !strings.Contains(capturedURL, "alt=sse") {
		t.Errorf("URL = %q, should contain alt=sse", capturedURL)
	}
	if !strings.Contains(capturedURL, "key=test-api-key") {
		t.Errorf("URL = %q, should contain key=test-api-key", capturedURL)
	}
}

// ---------------------------------------------------------------------------
// URL pattern tests
// ---------------------------------------------------------------------------

func TestIntegration_URLPattern(t *testing.T) {
	var capturedPath string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		capturedPath = r.URL.Path
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(geminiResponse{
			Candidates: []geminiCandidate{
				{
					Content:      geminiContent{Parts: []geminiPart{{Text: "ok"}}},
					FinishReason: "STOP",
				},
			},
		})
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	_, err := p.ChatCompletion(context.Background(), basicRequest())
	if err != nil {
		t.Fatalf("ChatCompletion() error = %v", err)
	}

	expected := "/v1beta/models/gemini-1.5-pro:generateContent"
	if capturedPath != expected {
		t.Errorf("URL path = %q, want %q", capturedPath, expected)
	}
}

// ---------------------------------------------------------------------------
// Custom headers test
// ---------------------------------------------------------------------------

func TestIntegration_CustomHeaders(t *testing.T) {
	var capturedHeaders http.Header
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		capturedHeaders = r.Header.Clone()
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(geminiResponse{
			Candidates: []geminiCandidate{
				{
					Content:      geminiContent{Parts: []geminiPart{{Text: "ok"}}},
					FinishReason: "STOP",
				},
			},
		})
	}))
	defer server.Close()

	p, err := New("test", config.ProviderConfig{
		BaseURL:       server.URL,
		APIKey:        "key",
		MaxConcurrent: 10,
		Headers: map[string]string{
			"X-Request-ID": "abc-123",
			"X-Trace":      "trace-456",
		},
	})
	if err != nil {
		t.Fatalf("New() error = %v", err)
	}

	_, err = p.ChatCompletion(context.Background(), basicRequest())
	if err != nil {
		t.Fatalf("ChatCompletion() error = %v", err)
	}

	if capturedHeaders.Get("X-Request-ID") != "abc-123" {
		t.Errorf("X-Request-ID = %q, want %q", capturedHeaders.Get("X-Request-ID"), "abc-123")
	}
	if capturedHeaders.Get("X-Trace") != "trace-456" {
		t.Errorf("X-Trace = %q, want %q", capturedHeaders.Get("X-Trace"), "trace-456")
	}
}

// ---------------------------------------------------------------------------
// Timeout handling tests
// ---------------------------------------------------------------------------

func TestIntegration_TimeoutHandling(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Sleep longer than the provider timeout.
		time.Sleep(3 * time.Second)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(geminiResponse{
			Candidates: []geminiCandidate{
				{
					Content:      geminiContent{Parts: []geminiPart{{Text: "too late"}}},
					FinishReason: "STOP",
				},
			},
		})
	}))
	defer server.Close()

	p, err := New("test", config.ProviderConfig{
		BaseURL:        server.URL,
		APIKey:         "key",
		DefaultTimeout: 500 * time.Millisecond, // Very short timeout.
		MaxConcurrent:  10,
	})
	if err != nil {
		t.Fatalf("New() error = %v", err)
	}

	_, err = p.ChatCompletion(context.Background(), basicRequest())
	if err == nil {
		t.Fatal("expected error due to timeout, got nil")
	}

	// The error should be some kind of timeout/upstream error.
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("error type = %T, want *models.APIError", err)
	}
	// Could be gateway timeout or upstream error depending on whether context deadline
	// was exceeded or the HTTP client timed out.
	if apiErr.Status != http.StatusBadGateway && apiErr.Status != http.StatusGatewayTimeout {
		t.Errorf("Status = %d, want 502 or 504", apiErr.Status)
	}
}

func TestIntegration_ContextCanceled(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(5 * time.Second)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(geminiResponse{})
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)

	ctx, cancel := context.WithCancel(context.Background())
	// Cancel immediately.
	cancel()

	_, err := p.ChatCompletion(ctx, basicRequest())
	if err == nil {
		t.Fatal("expected error for canceled context")
	}
}

func TestIntegration_StreamContextCanceled(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(5 * time.Second)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	req := basicRequest()
	req.Stream = true

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // Cancel immediately.

	chunks, errs := p.StreamChatCompletion(ctx, req)
	for range chunks {
	}

	var gotErr error
	for err := range errs {
		if err != nil {
			gotErr = err
		}
	}
	if gotErr == nil {
		t.Fatal("expected error for canceled context in streaming")
	}
}

func TestIntegration_StreamTimeout(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(3 * time.Second)
	}))
	defer server.Close()

	p, err := New("test", config.ProviderConfig{
		BaseURL:        server.URL,
		APIKey:         "key",
		DefaultTimeout: 500 * time.Millisecond,
		MaxConcurrent:  10,
	})
	if err != nil {
		t.Fatalf("New() error = %v", err)
	}

	req := basicRequest()
	req.Stream = true

	chunks, errs := p.StreamChatCompletion(context.Background(), req)
	for range chunks {
	}

	var gotErr error
	for err := range errs {
		if err != nil {
			gotErr = err
		}
	}
	if gotErr == nil {
		t.Fatal("expected error due to stream timeout, got nil")
	}
}

// ---------------------------------------------------------------------------
// Request body validation tests
// ---------------------------------------------------------------------------

func TestIntegration_RequestBodyContainsSystemInstruction(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		var gr geminiRequest
		if err := json.Unmarshal(body, &gr); err != nil {
			t.Fatalf("unmarshal: %v", err)
		}
		if gr.SystemInstruction == nil {
			t.Error("expected SystemInstruction in request body")
		}
		if len(gr.SystemInstruction.Parts) != 1 || gr.SystemInstruction.Parts[0].Text != "Be helpful." {
			t.Errorf("SystemInstruction = %+v, want parts with 'Be helpful.'", gr.SystemInstruction)
		}
		// Only user message in contents (system excluded).
		if len(gr.Contents) != 1 || gr.Contents[0].Role != "user" {
			t.Errorf("contents = %+v, want single user message", gr.Contents)
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(geminiResponse{
			Candidates: []geminiCandidate{
				{
					Content:      geminiContent{Parts: []geminiPart{{Text: "ok"}}},
					FinishReason: "STOP",
				},
			},
		})
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	req := &models.ChatCompletionRequest{
		Model: "gemini-1.5-pro",
		Messages: []models.Message{
			{Role: "system", Content: mustJSON("Be helpful.")},
			{Role: "user", Content: mustJSON("Hello")},
		},
	}
	_, err := p.ChatCompletion(context.Background(), req)
	if err != nil {
		t.Fatalf("ChatCompletion() error = %v", err)
	}
}

func TestIntegration_RequestBodyContainsGenerationConfig(t *testing.T) {
	temp := 0.5
	topP := 0.8
	maxTokens := 256

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		var gr geminiRequest
		if err := json.Unmarshal(body, &gr); err != nil {
			t.Fatalf("unmarshal: %v", err)
		}
		gc := gr.GenerationConfig
		if gc == nil {
			t.Fatal("GenerationConfig should not be nil")
		}
		if gc.Temperature == nil || *gc.Temperature != 0.5 {
			t.Errorf("Temperature = %v, want 0.5", gc.Temperature)
		}
		if gc.TopP == nil || *gc.TopP != 0.8 {
			t.Errorf("TopP = %v, want 0.8", gc.TopP)
		}
		if gc.MaxOutputTokens == nil || *gc.MaxOutputTokens != 256 {
			t.Errorf("MaxOutputTokens = %v, want 256", gc.MaxOutputTokens)
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(geminiResponse{
			Candidates: []geminiCandidate{
				{
					Content:      geminiContent{Parts: []geminiPart{{Text: "ok"}}},
					FinishReason: "STOP",
				},
			},
		})
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	req := &models.ChatCompletionRequest{
		Model:       "gemini-1.5-pro",
		Messages:    []models.Message{{Role: "user", Content: mustJSON("Hello")}},
		Temperature: &temp,
		TopP:        &topP,
		MaxTokens:   &maxTokens,
	}
	_, err := p.ChatCompletion(context.Background(), req)
	if err != nil {
		t.Fatalf("ChatCompletion() error = %v", err)
	}
}

// ---------------------------------------------------------------------------
// Vertex AI: New() constructor tests
// ---------------------------------------------------------------------------

func TestNew_VertexAI_DetectsVertexURL(t *testing.T) {
	p, err := New("vertex-test", config.ProviderConfig{
		BaseURL: "https://us-central1-aiplatform.googleapis.com",
		APIKey:  "vertex-access-token",
	})
	if err != nil {
		t.Fatalf("New() error = %v", err)
	}

	if !p.vertexAI {
		t.Error("vertexAI should be true for Vertex AI base URL")
	}
	if p.apiKey != "vertex-access-token" {
		t.Errorf("apiKey = %q, want %q", p.apiKey, "vertex-access-token")
	}
}

func TestNew_VertexAI_NonVertexURL(t *testing.T) {
	p, err := New("aistudio-test", config.ProviderConfig{
		BaseURL: "https://generativelanguage.googleapis.com",
		APIKey:  "ai-studio-key",
	})
	if err != nil {
		t.Fatalf("New() error = %v", err)
	}

	if p.vertexAI {
		t.Error("vertexAI should be false for AI Studio URL")
	}
}

func TestNew_VertexAI_EmbedProjectLocation(t *testing.T) {
	p, err := New("vertex-with-project", config.ProviderConfig{
		BaseURL: "https://us-central1-aiplatform.googleapis.com",
		APIKey:  "access-token",
		Headers: map[string]string{
			"x-gcp-project":  "my-project-123",
			"x-gcp-location": "us-central1",
		},
	})
	if err != nil {
		t.Fatalf("New() error = %v", err)
	}

	if !p.vertexAI {
		t.Error("vertexAI should be true")
	}

	wantBaseURL := "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/my-project-123/locations/us-central1"
	if p.baseURL != wantBaseURL {
		t.Errorf("baseURL = %q, want %q", p.baseURL, wantBaseURL)
	}
}

func TestNew_VertexAI_NoEmbedIfProjectPathAlreadyPresent(t *testing.T) {
	existingURL := "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/existing/locations/us-central1"
	p, err := New("vertex-existing", config.ProviderConfig{
		BaseURL: existingURL,
		APIKey:  "access-token",
		Headers: map[string]string{
			"x-gcp-project":  "different-project",
			"x-gcp-location": "us-east4",
		},
	})
	if err != nil {
		t.Fatalf("New() error = %v", err)
	}

	// Should NOT overwrite existing /projects/ path.
	if p.baseURL != existingURL {
		t.Errorf("baseURL = %q, want %q (should not modify when /projects/ already present)", p.baseURL, existingURL)
	}
}

func TestNew_VertexAI_NoEmbedIfMissingProjectOrLocation(t *testing.T) {
	baseURL := "https://us-central1-aiplatform.googleapis.com"

	// Missing location header.
	p, err := New("vertex-no-loc", config.ProviderConfig{
		BaseURL: baseURL,
		APIKey:  "access-token",
		Headers: map[string]string{
			"x-gcp-project": "my-project",
		},
	})
	if err != nil {
		t.Fatalf("New() error = %v", err)
	}

	if p.baseURL != baseURL {
		t.Errorf("baseURL = %q, want %q (should not embed without both project and location)", p.baseURL, baseURL)
	}
}

// ---------------------------------------------------------------------------
// Vertex AI: Integration tests
// ---------------------------------------------------------------------------

func TestIntegration_VertexAI_ChatCompletion_BearerAuth(t *testing.T) {
	var capturedAuthHeader string
	var capturedKeyParam string
	var capturedPath string

	geminiResp := geminiResponse{
		Candidates: []geminiCandidate{
			{
				Content: geminiContent{
					Role:  "model",
					Parts: []geminiPart{{Text: "Hello from Vertex AI!"}},
				},
				FinishReason: "STOP",
			},
		},
		UsageMetadata: &geminiUsageMetadata{
			PromptTokenCount:     5,
			CandidatesTokenCount: 4,
			TotalTokenCount:      9,
		},
	}

	// We use a handler that accepts any path, since the mock server URL
	// does not contain "aiplatform.googleapis.com" (buildURL falls back to v1beta format).
	// The key assertions here are about auth headers (Bearer vs key param).
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		capturedAuthHeader = r.Header.Get("Authorization")
		capturedKeyParam = r.URL.Query().Get("key")
		capturedPath = r.URL.Path

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(geminiResp)
	}))
	defer server.Close()

	// Manually construct a provider that simulates Vertex AI behavior.
	// The vertexAI=true field controls Bearer auth and suppresses ?key= param.
	// Note: buildURL uses isVertexAI(baseURL) which checks for "aiplatform.googleapis.com"
	// in the URL; since we use a mock server URL, URL format assertions are tested
	// separately in the unit tests (TestBuildURL_VertexAI_*).
	p := &Provider{
		id:       "vertex-test",
		baseURL:  server.URL,
		apiKey:   "vertex-access-token",
		vertexAI: true,
		httpClient: &http.Client{
			Timeout: 10 * time.Second,
		},
		semaphore: make(chan struct{}, 10),
		headers:   map[string]string{},
	}

	resp, err := p.ChatCompletion(context.Background(), basicRequest())
	if err != nil {
		t.Fatalf("ChatCompletion() error = %v", err)
	}

	// Verify Bearer auth header is used (not ?key=).
	if capturedAuthHeader != "Bearer vertex-access-token" {
		t.Errorf("Authorization header = %q, want %q", capturedAuthHeader, "Bearer vertex-access-token")
	}
	if capturedKeyParam != "" {
		t.Errorf("key query param should be empty for Vertex AI, got %q", capturedKeyParam)
	}

	// Verify the URL path contains generateContent (exact format tested in unit tests).
	if !strings.Contains(capturedPath, "generateContent") {
		t.Errorf("URL path = %q, should contain 'generateContent'", capturedPath)
	}

	// Verify response is correctly translated.
	if resp.Object != "chat.completion" {
		t.Errorf("Object = %q, want %q", resp.Object, "chat.completion")
	}
	if len(resp.Choices) != 1 {
		t.Fatalf("Choices length = %d, want 1", len(resp.Choices))
	}
	var content string
	if err := json.Unmarshal(resp.Choices[0].Message.Content, &content); err != nil {
		t.Fatalf("unmarshal content: %v", err)
	}
	if content != "Hello from Vertex AI!" {
		t.Errorf("content = %q, want %q", content, "Hello from Vertex AI!")
	}
}

func TestIntegration_VertexAI_StreamChatCompletion_BearerAuth(t *testing.T) {
	var capturedAuthHeader string
	var capturedKeyParam string
	var capturedPath string
	var capturedAltParam string

	chunk1 := `{"candidates":[{"content":{"role":"model","parts":[{"text":"Streaming"}]}}]}`
	chunk2 := `{"candidates":[{"content":{"role":"model","parts":[{"text":" from Vertex!"}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":3,"totalTokenCount":8}}`

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		capturedAuthHeader = r.Header.Get("Authorization")
		capturedKeyParam = r.URL.Query().Get("key")
		capturedPath = r.URL.Path
		capturedAltParam = r.URL.Query().Get("alt")

		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		flusher, ok := w.(http.Flusher)
		if !ok {
			t.Fatal("response writer does not support flushing")
		}

		for _, c := range []string{chunk1, chunk2} {
			fmt.Fprintf(w, "data: %s\n\n", c)
			flusher.Flush()
		}
	}))
	defer server.Close()

	// Same note as ChatCompletion test: buildURL uses isVertexAI(baseURL) which
	// won't match mock server URL. The key assertion is Bearer auth header usage.
	p := &Provider{
		id:       "vertex-stream-test",
		baseURL:  server.URL,
		apiKey:   "vertex-stream-token",
		vertexAI: true,
		httpClient: &http.Client{
			Timeout: 10 * time.Second,
		},
		semaphore: make(chan struct{}, 10),
		headers:   map[string]string{},
	}

	req := basicRequest()
	req.Stream = true

	chunks, errs := p.StreamChatCompletion(context.Background(), req)

	var allContent []string
	for chunk := range chunks {
		for _, sc := range chunk.Choices {
			if sc.Delta.Content != nil {
				allContent = append(allContent, *sc.Delta.Content)
			}
		}
	}
	for err := range errs {
		if err != nil {
			t.Fatalf("stream error: %v", err)
		}
	}

	// Verify Bearer auth header is used.
	if capturedAuthHeader != "Bearer vertex-stream-token" {
		t.Errorf("Authorization header = %q, want %q", capturedAuthHeader, "Bearer vertex-stream-token")
	}
	if capturedKeyParam != "" {
		t.Errorf("key query param should be empty for Vertex AI streaming, got %q", capturedKeyParam)
	}

	// Verify the streaming URL uses streamGenerateContent with alt=sse.
	if !strings.Contains(capturedPath, "streamGenerateContent") {
		t.Errorf("URL path = %q, should contain 'streamGenerateContent'", capturedPath)
	}
	if capturedAltParam != "sse" {
		t.Errorf("alt param = %q, want %q", capturedAltParam, "sse")
	}

	// Verify content was received.
	combined := strings.Join(allContent, "")
	if combined != "Streaming from Vertex!" {
		t.Errorf("combined content = %q, want %q", combined, "Streaming from Vertex!")
	}
}

func TestIntegration_VertexAI_NoKeyQueryParam(t *testing.T) {
	// When vertexAI=true, even in non-streaming, the API key should NOT be appended as ?key=.
	var capturedURL string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		capturedURL = r.URL.String()
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(geminiResponse{
			Candidates: []geminiCandidate{
				{
					Content:      geminiContent{Parts: []geminiPart{{Text: "ok"}}},
					FinishReason: "STOP",
				},
			},
		})
	}))
	defer server.Close()

	p := &Provider{
		id:       "vertex-no-key",
		baseURL:  server.URL + "/v1beta1/projects/p/locations/l",
		apiKey:   "my-token",
		vertexAI: true,
		httpClient: &http.Client{
			Timeout: 10 * time.Second,
		},
		semaphore: make(chan struct{}, 10),
		headers:   map[string]string{},
	}

	_, err := p.ChatCompletion(context.Background(), basicRequest())
	if err != nil {
		t.Fatalf("ChatCompletion() error = %v", err)
	}

	if strings.Contains(capturedURL, "key=") {
		t.Errorf("URL should not contain key= for Vertex AI, got %q", capturedURL)
	}
}

// ---------------------------------------------------------------------------
// Request body validation tests
// ---------------------------------------------------------------------------

func TestIntegration_RequestBodyContainsTools(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		var gr geminiRequest
		if err := json.Unmarshal(body, &gr); err != nil {
			t.Fatalf("unmarshal: %v", err)
		}
		if len(gr.Tools) != 1 {
			t.Fatalf("Tools length = %d, want 1", len(gr.Tools))
		}
		if len(gr.Tools[0].FunctionDeclarations) != 1 {
			t.Fatalf("FunctionDeclarations length = %d, want 1", len(gr.Tools[0].FunctionDeclarations))
		}
		if gr.Tools[0].FunctionDeclarations[0].Name != "get_info" {
			t.Errorf("Name = %q, want %q", gr.Tools[0].FunctionDeclarations[0].Name, "get_info")
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(geminiResponse{
			Candidates: []geminiCandidate{
				{
					Content:      geminiContent{Parts: []geminiPart{{Text: "ok"}}},
					FinishReason: "STOP",
				},
			},
		})
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	req := &models.ChatCompletionRequest{
		Model:    "gemini-1.5-pro",
		Messages: []models.Message{{Role: "user", Content: mustJSON("Hello")}},
		Tools: []models.Tool{
			{
				Type: "function",
				Function: models.ToolFunction{
					Name:        "get_info",
					Description: "Gets info",
					Parameters:  json.RawMessage(`{"type":"object"}`),
				},
			},
		},
	}
	_, err := p.ChatCompletion(context.Background(), req)
	if err != nil {
		t.Fatalf("ChatCompletion() error = %v", err)
	}
}
