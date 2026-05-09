package cohere

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

// newTestProvider creates a Cohere provider pointing at the given test server.
func newTestProvider(t *testing.T, serverURL string) *Provider {
	t.Helper()
	p, err := New("test-cohere", config.ProviderConfig{
		BaseURL: serverURL,
		APIKey:  "test-key",
	})
	if err != nil {
		t.Fatalf("New() error: %v", err)
	}
	return p
}

// ---------------------------------------------------------------------------
// New() constructor tests
// ---------------------------------------------------------------------------

func TestNew_Defaults(t *testing.T) {
	p, err := New("cohere-1", config.ProviderConfig{
		BaseURL: "https://api.cohere.com",
		APIKey:  "test-key",
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	if p.ID() != "cohere-1" {
		t.Errorf("ID: got %q, want %q", p.ID(), "cohere-1")
	}
	if p.baseURL != "https://api.cohere.com" {
		t.Errorf("baseURL: got %q, want %q", p.baseURL, "https://api.cohere.com")
	}
	if p.apiKey != "test-key" {
		t.Errorf("apiKey: got %q, want %q", p.apiKey, "test-key")
	}
}

func TestNew_TrailingSlashStripped(t *testing.T) {
	p, err := New("c", config.ProviderConfig{
		BaseURL: "https://api.cohere.com/",
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if p.baseURL != "https://api.cohere.com" {
		t.Errorf("trailing slash not stripped: got %q", p.baseURL)
	}
}

func TestNew_CustomTimeout(t *testing.T) {
	p, err := New("c", config.ProviderConfig{
		BaseURL:        "https://api.cohere.com",
		DefaultTimeout: 10 * time.Second,
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if p.httpClient.Timeout != 10*time.Second {
		t.Errorf("Timeout: got %v, want %v", p.httpClient.Timeout, 10*time.Second)
	}
}

func TestNew_DefaultTimeout(t *testing.T) {
	p, err := New("c", config.ProviderConfig{
		BaseURL: "https://api.cohere.com",
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if p.httpClient.Timeout != 60*time.Second {
		t.Errorf("Timeout: got %v, want %v", p.httpClient.Timeout, 60*time.Second)
	}
}

func TestListModels_ReturnsNil(t *testing.T) {
	p, _ := New("c", config.ProviderConfig{BaseURL: "http://localhost"})
	m, err := p.ListModels(context.Background())
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
	if m != nil {
		t.Errorf("expected nil, got %v", m)
	}
}

// ---------------------------------------------------------------------------
// setHeaders tests
// ---------------------------------------------------------------------------

func TestSetHeaders_NoAPIKey(t *testing.T) {
	p, _ := New("c", config.ProviderConfig{BaseURL: "http://localhost"})
	req := httptest.NewRequest("POST", "/v2/chat", nil)
	p.setHeaders(req)

	if got := req.Header.Get("Content-Type"); got != "application/json" {
		t.Errorf("Content-Type: got %q, want %q", got, "application/json")
	}
	if got := req.Header.Get("Authorization"); got != "" {
		t.Errorf("Authorization should be empty, got %q", got)
	}
}

func TestSetHeaders_WithAPIKeyAndCustomHeaders(t *testing.T) {
	p, _ := New("c", config.ProviderConfig{
		BaseURL: "http://localhost",
		APIKey:  "my-key",
		Headers: map[string]string{
			"X-Custom": "value1",
		},
	})
	req := httptest.NewRequest("POST", "/v2/chat", nil)
	p.setHeaders(req)

	if got := req.Header.Get("Authorization"); got != "Bearer my-key" {
		t.Errorf("Authorization: got %q, want %q", got, "Bearer my-key")
	}
	if got := req.Header.Get("X-Custom"); got != "value1" {
		t.Errorf("X-Custom: got %q, want %q", got, "value1")
	}
}

// ---------------------------------------------------------------------------
// Integration: Non-streaming chat completion
// ---------------------------------------------------------------------------

func TestIntegration_NonStreamingRoundtrip(t *testing.T) {
	var capturedReq struct {
		method string
		path   string
		auth   string
		body   []byte
	}

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		capturedReq.method = r.Method
		capturedReq.path = r.URL.Path
		capturedReq.auth = r.Header.Get("Authorization")
		capturedReq.body, _ = io.ReadAll(r.Body)

		resp := cohereResponse{
			ID: "resp-001",
			Message: cohereRespMessage{
				Role: "assistant",
				Content: []cohereContentPart{
					{Type: "text", Text: "Hello from Cohere!"},
				},
			},
			FinishReason: "COMPLETE",
			Usage: &cohereUsage{
				Tokens: &cohereTokens{
					InputTokens:  12,
					OutputTokens: 5,
				},
			},
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	p, err := New("cohere-test", config.ProviderConfig{
		BaseURL: server.URL,
		APIKey:  "test-api-key",
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	req := &models.ChatCompletionRequest{
		Model: "command-r-plus",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hello"`)},
		},
	}

	resp, err := p.ChatCompletion(context.Background(), req)
	if err != nil {
		t.Fatalf("ChatCompletion: %v", err)
	}

	// Verify the request was sent correctly
	if capturedReq.method != "POST" {
		t.Errorf("Method: got %q, want POST", capturedReq.method)
	}
	if capturedReq.path != "/v2/chat" {
		t.Errorf("Path: got %q, want /v2/chat", capturedReq.path)
	}
	if capturedReq.auth != "Bearer test-api-key" {
		t.Errorf("Authorization: got %q, want %q", capturedReq.auth, "Bearer test-api-key")
	}

	// Verify request body has model and messages
	var bodyMap map[string]json.RawMessage
	if err := json.Unmarshal(capturedReq.body, &bodyMap); err != nil {
		t.Fatalf("unmarshal body: %v", err)
	}
	if _, ok := bodyMap["model"]; !ok {
		t.Error("request body missing 'model' field")
	}
	if _, ok := bodyMap["messages"]; !ok {
		t.Error("request body missing 'messages' field")
	}
	// stream field: since it's false with omitempty, it is NOT included in the JSON.
	// This is by design -- Cohere defaults to non-streaming when stream is absent.
	if _, ok := bodyMap["stream"]; ok {
		t.Log("stream field present in body (stream:false is omitted by omitempty, but may be explicitly set)")
	}

	var model string
	if err := json.Unmarshal(bodyMap["model"], &model); err != nil {
		t.Fatalf("unmarshal model: %v", err)
	}
	if model != "command-r-plus" {
		t.Errorf("body.model: got %q, want %q", model, "command-r-plus")
	}

	// Verify response
	if resp.ID != "resp-001" {
		t.Errorf("resp.ID: got %q, want %q", resp.ID, "resp-001")
	}
	if resp.Object != "chat.completion" {
		t.Errorf("resp.Object: got %q, want %q", resp.Object, "chat.completion")
	}
	if resp.Model != "command-r-plus" {
		t.Errorf("resp.Model: got %q, want %q", resp.Model, "command-r-plus")
	}
	if len(resp.Choices) != 1 {
		t.Fatalf("Choices length: got %d, want 1", len(resp.Choices))
	}

	var content string
	if err := json.Unmarshal(resp.Choices[0].Message.Content, &content); err != nil {
		t.Fatalf("unmarshal content: %v", err)
	}
	if content != "Hello from Cohere!" {
		t.Errorf("content: got %q, want %q", content, "Hello from Cohere!")
	}
	if resp.Choices[0].FinishReason != "stop" {
		t.Errorf("FinishReason: got %q, want %q", resp.Choices[0].FinishReason, "stop")
	}
	if resp.Usage == nil {
		t.Fatal("Usage is nil")
	}
	if resp.Usage.PromptTokens != 12 {
		t.Errorf("PromptTokens: got %d, want 12", resp.Usage.PromptTokens)
	}
	if resp.Usage.CompletionTokens != 5 {
		t.Errorf("CompletionTokens: got %d, want 5", resp.Usage.CompletionTokens)
	}
}

// ---------------------------------------------------------------------------
// Integration: Non-streaming with tools
// ---------------------------------------------------------------------------

func TestIntegration_NonStreamingWithTools(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		bodyBytes, _ := io.ReadAll(r.Body)
		var reqBody map[string]interface{}
		json.Unmarshal(bodyBytes, &reqBody)

		// Verify tools were sent
		tools, ok := reqBody["tools"].([]interface{})
		if !ok || len(tools) == 0 {
			t.Error("expected tools in request body")
		}

		resp := cohereResponse{
			ID: "resp-tools",
			Message: cohereRespMessage{
				Role: "assistant",
				ToolCalls: []cohereToolCall{
					{
						ID:   "call-1",
						Type: "function",
						Function: cohereToolCallFunc{
							Name:      "get_weather",
							Arguments: `{"city":"San Francisco"}`,
						},
					},
				},
			},
			FinishReason: "TOOL_CALL",
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	p, _ := New("cohere-test", config.ProviderConfig{
		BaseURL: server.URL,
		APIKey:  "key",
	})

	req := &models.ChatCompletionRequest{
		Model: "command-r-plus",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"What is the weather in SF?"`)},
		},
		Tools: []models.Tool{
			{
				Type: "function",
				Function: models.ToolFunction{
					Name:        "get_weather",
					Description: "Get weather info",
					Parameters:  json.RawMessage(`{"type":"object","properties":{"city":{"type":"string"}}}`),
				},
			},
		},
	}

	resp, err := p.ChatCompletion(context.Background(), req)
	if err != nil {
		t.Fatalf("ChatCompletion: %v", err)
	}

	if resp.Choices[0].FinishReason != "tool_calls" {
		t.Errorf("FinishReason: got %q, want %q", resp.Choices[0].FinishReason, "tool_calls")
	}
	if len(resp.Choices[0].Message.ToolCalls) != 1 {
		t.Fatalf("ToolCalls length: got %d, want 1", len(resp.Choices[0].Message.ToolCalls))
	}
	tc := resp.Choices[0].Message.ToolCalls[0]
	if tc.ID != "call-1" {
		t.Errorf("ToolCall.ID: got %q, want %q", tc.ID, "call-1")
	}
	if tc.Function.Name != "get_weather" {
		t.Errorf("ToolCall.Function.Name: got %q, want %q", tc.Function.Name, "get_weather")
	}
}

// ---------------------------------------------------------------------------
// Integration: Streaming chat completion
// ---------------------------------------------------------------------------

func TestIntegration_StreamingRoundtrip(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		bodyBytes, _ := io.ReadAll(r.Body)
		var reqBody map[string]interface{}
		json.Unmarshal(bodyBytes, &reqBody)

		// Verify stream is true
		if stream, ok := reqBody["stream"].(bool); !ok || !stream {
			t.Errorf("body.stream: got %v, want true", reqBody["stream"])
		}

		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("Connection", "keep-alive")
		flusher, ok := w.(http.Flusher)
		if !ok {
			t.Fatal("ResponseWriter does not implement Flusher")
		}

		events := []string{
			`data: {"type":"message-start","id":"stream-001"}`,
			`data: {"type":"content-delta","delta":{"message":{"content":{"text":"Hello"}}}}`,
			`data: {"type":"content-delta","delta":{"message":{"content":{"text":" world"}}}}`,
			`data: {"type":"message-end","finish_reason":"COMPLETE","usage":{"tokens":{"input_tokens":8,"output_tokens":3}}}`,
		}
		for _, e := range events {
			fmt.Fprintf(w, "%s\n\n", e)
			flusher.Flush()
		}
	}))
	defer server.Close()

	p, _ := New("cohere-stream", config.ProviderConfig{
		BaseURL: server.URL,
		APIKey:  "stream-key",
	})

	req := &models.ChatCompletionRequest{
		Model:  "command-r-plus",
		Stream: true,
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hello"`)},
		},
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	chunks, errs := p.StreamChatCompletion(ctx, req)

	var collectedChunks []models.StreamChunk
	var streamErr error

	for chunks != nil || errs != nil {
		select {
		case chunk, ok := <-chunks:
			if !ok {
				chunks = nil
				continue
			}
			collectedChunks = append(collectedChunks, chunk)
		case err, ok := <-errs:
			if !ok {
				errs = nil
				continue
			}
			if err != nil {
				streamErr = err
			}
		case <-ctx.Done():
			t.Fatal("test timed out")
		}
	}

	if streamErr != nil {
		t.Fatalf("stream error: %v", streamErr)
	}

	// Expect: message-start, 2x content-delta, message-end = 4 chunks
	if len(collectedChunks) != 4 {
		t.Fatalf("chunks count: got %d, want 4. chunks: %+v", len(collectedChunks), collectedChunks)
	}

	// First chunk: role
	if collectedChunks[0].Choices[0].Delta.Role != "assistant" {
		t.Errorf("chunk[0] role: got %q, want %q", collectedChunks[0].Choices[0].Delta.Role, "assistant")
	}
	if collectedChunks[0].ID != "stream-001" {
		t.Errorf("chunk[0] ID: got %q, want %q", collectedChunks[0].ID, "stream-001")
	}

	// Content chunks
	if collectedChunks[1].Choices[0].Delta.Content == nil || *collectedChunks[1].Choices[0].Delta.Content != "Hello" {
		t.Errorf("chunk[1] content: got %v", collectedChunks[1].Choices[0].Delta.Content)
	}
	if collectedChunks[2].Choices[0].Delta.Content == nil || *collectedChunks[2].Choices[0].Delta.Content != " world" {
		t.Errorf("chunk[2] content: got %v", collectedChunks[2].Choices[0].Delta.Content)
	}

	// Final chunk: finish reason + usage
	lastChunk := collectedChunks[3]
	if lastChunk.Choices[0].FinishReason == nil || *lastChunk.Choices[0].FinishReason != "stop" {
		t.Errorf("last chunk finish_reason: got %v", lastChunk.Choices[0].FinishReason)
	}
	if lastChunk.Usage == nil {
		t.Fatal("last chunk usage is nil")
	}
	if lastChunk.Usage.PromptTokens != 8 {
		t.Errorf("PromptTokens: got %d, want 8", lastChunk.Usage.PromptTokens)
	}
	if lastChunk.Usage.CompletionTokens != 3 {
		t.Errorf("CompletionTokens: got %d, want 3", lastChunk.Usage.CompletionTokens)
	}
}

// ---------------------------------------------------------------------------
// Integration: Error handling
// ---------------------------------------------------------------------------

func TestIntegration_Error429(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusTooManyRequests)
		w.Write([]byte(`{"message":"rate limit exceeded"}`))
	}))
	defer server.Close()

	p, _ := New("c", config.ProviderConfig{BaseURL: server.URL, APIKey: "k"})
	_, err := p.ChatCompletion(context.Background(), &models.ChatCompletionRequest{
		Model:    "command-r",
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}},
	})

	if err == nil {
		t.Fatal("expected error")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T", err)
	}
	if apiErr.Status != 429 {
		t.Errorf("Status: got %d, want 429", apiErr.Status)
	}
	if apiErr.Type != models.ErrTypeRateLimit {
		t.Errorf("Type: got %q, want %q", apiErr.Type, models.ErrTypeRateLimit)
	}
	if !strings.Contains(apiErr.Message, "rate limit") {
		t.Errorf("Message should contain 'rate limit', got %q", apiErr.Message)
	}
}

func TestIntegration_Error500(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		w.Write([]byte(`{"message":"internal server error"}`))
	}))
	defer server.Close()

	p, _ := New("c", config.ProviderConfig{BaseURL: server.URL, APIKey: "k"})
	_, err := p.ChatCompletion(context.Background(), &models.ChatCompletionRequest{
		Model:    "command-r",
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}},
	})

	if err == nil {
		t.Fatal("expected error")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T", err)
	}
	if apiErr.Status != 502 {
		t.Errorf("Status: got %d, want 502 (upstream 500 maps to 502)", apiErr.Status)
	}
	if apiErr.Type != models.ErrTypeUpstream {
		t.Errorf("Type: got %q, want %q", apiErr.Type, models.ErrTypeUpstream)
	}
}

func TestIntegration_Error401(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
		w.Write([]byte(`{"message":"invalid api key"}`))
	}))
	defer server.Close()

	p, _ := New("c", config.ProviderConfig{BaseURL: server.URL, APIKey: "bad-key"})
	_, err := p.ChatCompletion(context.Background(), &models.ChatCompletionRequest{
		Model:    "command-r",
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}},
	})

	if err == nil {
		t.Fatal("expected error")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T", err)
	}
	if apiErr.Status != 401 {
		t.Errorf("Status: got %d, want 401", apiErr.Status)
	}
	if apiErr.Type != models.ErrTypeAuthentication {
		t.Errorf("Type: got %q, want %q", apiErr.Type, models.ErrTypeAuthentication)
	}
}

// ---------------------------------------------------------------------------
// Integration: Streaming errors
// ---------------------------------------------------------------------------

func TestIntegration_StreamingError429(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusTooManyRequests)
		w.Write([]byte(`{"message":"too many requests"}`))
	}))
	defer server.Close()

	p, _ := New("c", config.ProviderConfig{BaseURL: server.URL, APIKey: "k"})

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	chunks, errs := p.StreamChatCompletion(ctx, &models.ChatCompletionRequest{
		Model:    "command-r",
		Stream:   true,
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}},
	})

	var gotErr error
	for chunks != nil || errs != nil {
		select {
		case _, ok := <-chunks:
			if !ok {
				chunks = nil
			}
		case err, ok := <-errs:
			if !ok {
				errs = nil
			} else if err != nil {
				gotErr = err
			}
		case <-ctx.Done():
			t.Fatal("timed out")
		}
	}

	if gotErr == nil {
		t.Fatal("expected error")
	}
	apiErr, ok := gotErr.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T", gotErr)
	}
	if apiErr.Status != 429 {
		t.Errorf("Status: got %d, want 429", apiErr.Status)
	}
}

func TestIntegration_StreamingError500(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		w.Write([]byte(`{"message":"server down"}`))
	}))
	defer server.Close()

	p, _ := New("c", config.ProviderConfig{BaseURL: server.URL, APIKey: "k"})

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	chunks, errs := p.StreamChatCompletion(ctx, &models.ChatCompletionRequest{
		Model:    "command-r",
		Stream:   true,
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}},
	})

	var gotErr error
	for chunks != nil || errs != nil {
		select {
		case _, ok := <-chunks:
			if !ok {
				chunks = nil
			}
		case err, ok := <-errs:
			if !ok {
				errs = nil
			} else if err != nil {
				gotErr = err
			}
		case <-ctx.Done():
			t.Fatal("timed out")
		}
	}

	if gotErr == nil {
		t.Fatal("expected error")
	}
	apiErr, ok := gotErr.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T", gotErr)
	}
	if apiErr.Status != 502 {
		t.Errorf("Status: got %d, want 502", apiErr.Status)
	}
}

// ---------------------------------------------------------------------------
// Integration: Timeout handling
// ---------------------------------------------------------------------------

func TestIntegration_Timeout(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(3 * time.Second)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	p, _ := New("c", config.ProviderConfig{
		BaseURL:        server.URL,
		APIKey:         "k",
		DefaultTimeout: 100 * time.Millisecond,
	})

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	_, err := p.ChatCompletion(ctx, &models.ChatCompletionRequest{
		Model:    "command-r",
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}},
	})

	if err == nil {
		t.Fatal("expected timeout error")
	}

	// Should be an upstream error since the HTTP client timeout fires
	// before the context deadline, and ctx.Err() is still nil at that point.
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T: %v", err, err)
	}
	if apiErr.Status != http.StatusBadGateway {
		t.Errorf("Status: got %d, want %d", apiErr.Status, http.StatusBadGateway)
	}
}

func TestIntegration_ContextCancellation(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(5 * time.Second)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	p, _ := New("c", config.ProviderConfig{
		BaseURL: server.URL,
		APIKey:  "k",
	})

	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	_, err := p.ChatCompletion(ctx, &models.ChatCompletionRequest{
		Model:    "command-r",
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}},
	})

	if err == nil {
		t.Fatal("expected error on context cancellation")
	}

	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T: %v", err, err)
	}
	if apiErr.Status != http.StatusGatewayTimeout {
		t.Errorf("Status: got %d, want %d", apiErr.Status, http.StatusGatewayTimeout)
	}
}

// ---------------------------------------------------------------------------
// CreateEmbedding tests
// ---------------------------------------------------------------------------

func TestCreateEmbedding_Success(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" {
			t.Errorf("method = %s, want POST", r.Method)
		}
		if r.URL.Path != "/v2/embed" {
			t.Errorf("path = %s, want /v2/embed", r.URL.Path)
		}
		if got := r.Header.Get("Authorization"); got != "Bearer test-key" {
			t.Errorf("auth = %q, want %q", got, "Bearer test-key")
		}

		// Verify the request was translated to Cohere format.
		var req cohereEmbedRequest
		json.NewDecoder(r.Body).Decode(&req)
		if req.Model != "embed-english-v3.0" {
			t.Errorf("model = %q, want embed-english-v3.0", req.Model)
		}
		if len(req.Texts) != 1 || req.Texts[0] != "hello" {
			t.Errorf("texts = %v, want [hello]", req.Texts)
		}
		if req.InputType != "search_document" {
			t.Errorf("input_type = %q, want search_document", req.InputType)
		}
		if len(req.EmbeddingTypes) != 1 || req.EmbeddingTypes[0] != "float" {
			t.Errorf("embedding_types = %v, want [float]", req.EmbeddingTypes)
		}

		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{
			"id": "emb-123",
			"embeddings": {"float": [[0.1, 0.2, 0.3]]},
			"texts": ["hello"],
			"meta": {"billed_units": {"input_tokens": 1}}
		}`))
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	resp, err := p.CreateEmbedding(context.Background(), &models.EmbeddingRequest{
		Model: "embed-english-v3.0",
		Input: json.RawMessage(`"hello"`),
	})
	if err != nil {
		t.Fatalf("CreateEmbedding error: %v", err)
	}
	if resp.Object != "list" {
		t.Errorf("object = %q, want list", resp.Object)
	}
	if len(resp.Data) != 1 {
		t.Fatalf("data length = %d, want 1", len(resp.Data))
	}
	if resp.Data[0].Object != "embedding" {
		t.Errorf("data[0].object = %q, want embedding", resp.Data[0].Object)
	}
	if resp.Data[0].Index != 0 {
		t.Errorf("data[0].index = %d, want 0", resp.Data[0].Index)
	}
	if resp.Usage == nil {
		t.Fatal("usage is nil")
	}
	if resp.Usage.PromptTokens != 1 {
		t.Errorf("usage.prompt_tokens = %d, want 1", resp.Usage.PromptTokens)
	}
}

func TestCreateEmbedding_ArrayInput(t *testing.T) {
	var receivedTexts []string
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req cohereEmbedRequest
		json.NewDecoder(r.Body).Decode(&req)
		receivedTexts = req.Texts
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"id":"e","embeddings":{"float":[[0.1],[0.2]]},"texts":["a","b"],"meta":{"billed_units":{"input_tokens":2}}}`))
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	resp, err := p.CreateEmbedding(context.Background(), &models.EmbeddingRequest{
		Model: "embed-english-v3.0",
		Input: json.RawMessage(`["hello","world"]`),
	})
	if err != nil {
		t.Fatalf("error: %v", err)
	}
	if len(receivedTexts) != 2 {
		t.Errorf("texts length = %d, want 2", len(receivedTexts))
	}
	if len(resp.Data) != 2 {
		t.Errorf("data length = %d, want 2", len(resp.Data))
	}
}

func TestCreateEmbedding_ModelPrefixStripped(t *testing.T) {
	var receivedModel string
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req cohereEmbedRequest
		json.NewDecoder(r.Body).Decode(&req)
		receivedModel = req.Model
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"id":"e","embeddings":{"float":[]},"texts":[]}`))
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	_, err := p.CreateEmbedding(context.Background(), &models.EmbeddingRequest{
		Model: "cohere/embed-english-v3.0",
		Input: json.RawMessage(`"test"`),
	})
	if err != nil {
		t.Fatalf("error: %v", err)
	}
	if receivedModel != "embed-english-v3.0" {
		t.Errorf("model = %q, want embed-english-v3.0", receivedModel)
	}
}

func TestCreateEmbedding_ProviderError(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		w.Write([]byte(`{"message":"invalid model"}`))
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	_, err := p.CreateEmbedding(context.Background(), &models.EmbeddingRequest{
		Model: "bad",
		Input: json.RawMessage(`"test"`),
	})
	if err == nil {
		t.Fatal("expected error")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T", err)
	}
	if apiErr.Message != "invalid model" {
		t.Errorf("message = %q, want %q", apiErr.Message, "invalid model")
	}
}

// ---------------------------------------------------------------------------
// Rerank tests
// ---------------------------------------------------------------------------

func TestRerank_Success(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" {
			t.Errorf("method = %s, want POST", r.Method)
		}
		if r.URL.Path != "/v2/rerank" {
			t.Errorf("path = %s, want /v2/rerank", r.URL.Path)
		}
		if got := r.Header.Get("Authorization"); got != "Bearer test-key" {
			t.Errorf("auth = %q, want %q", got, "Bearer test-key")
		}

		var req cohereRerankRequest
		json.NewDecoder(r.Body).Decode(&req)
		if req.Model != "rerank-english-v3.0" {
			t.Errorf("model = %q, want rerank-english-v3.0", req.Model)
		}
		if req.Query != "what is AI?" {
			t.Errorf("query = %q, want 'what is AI?'", req.Query)
		}
		if len(req.Documents) != 2 {
			t.Errorf("documents length = %d, want 2", len(req.Documents))
		}

		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{
			"id": "rerank-123",
			"results": [
				{"index": 1, "relevance_score": 0.95, "document": {"text": "AI is artificial intelligence"}},
				{"index": 0, "relevance_score": 0.30, "document": {"text": "The weather is nice"}}
			],
			"meta": {"billed_units": {"input_tokens": 10}}
		}`))
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	topN := 2
	resp, err := p.Rerank(context.Background(), &models.RerankRequest{
		Model:           "rerank-english-v3.0",
		Query:           "what is AI?",
		Documents:       []string{"The weather is nice", "AI is artificial intelligence"},
		TopN:            &topN,
		ReturnDocuments: true,
	})
	if err != nil {
		t.Fatalf("Rerank error: %v", err)
	}
	if resp.ID != "rerank-123" {
		t.Errorf("id = %q, want rerank-123", resp.ID)
	}
	if len(resp.Results) != 2 {
		t.Fatalf("results length = %d, want 2", len(resp.Results))
	}
	// First result should be the highest scored.
	if resp.Results[0].RelevanceScore != 0.95 {
		t.Errorf("results[0].relevance_score = %f, want 0.95", resp.Results[0].RelevanceScore)
	}
	if resp.Results[0].Document == nil || resp.Results[0].Document.Text != "AI is artificial intelligence" {
		t.Errorf("results[0].document.text unexpected")
	}
}

func TestRerank_ModelPrefixStripped(t *testing.T) {
	var receivedModel string
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req cohereRerankRequest
		json.NewDecoder(r.Body).Decode(&req)
		receivedModel = req.Model
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"id":"r","results":[]}`))
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	_, err := p.Rerank(context.Background(), &models.RerankRequest{
		Model:     "cohere/rerank-english-v3.0",
		Query:     "q",
		Documents: []string{"d"},
	})
	if err != nil {
		t.Fatalf("error: %v", err)
	}
	if receivedModel != "rerank-english-v3.0" {
		t.Errorf("model = %q, want rerank-english-v3.0", receivedModel)
	}
}

func TestRerank_TopNPassthrough(t *testing.T) {
	var receivedTopN *int
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req cohereRerankRequest
		json.NewDecoder(r.Body).Decode(&req)
		receivedTopN = req.TopN
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"id":"r","results":[{"index":0,"relevance_score":0.9}]}`))
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	topN := 3
	_, err := p.Rerank(context.Background(), &models.RerankRequest{
		Model:     "rerank-english-v3.0",
		Query:     "q",
		Documents: []string{"d1", "d2", "d3", "d4"},
		TopN:      &topN,
	})
	if err != nil {
		t.Fatalf("error: %v", err)
	}
	if receivedTopN == nil || *receivedTopN != 3 {
		t.Errorf("top_n = %v, want 3", receivedTopN)
	}
}

func TestRerank_ProviderError(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		w.Write([]byte(`{"message":"invalid model"}`))
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	_, err := p.Rerank(context.Background(), &models.RerankRequest{
		Model:     "bad-model",
		Query:     "q",
		Documents: []string{"d"},
	})
	if err == nil {
		t.Fatal("expected error")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T", err)
	}
	if apiErr.Message != "invalid model" {
		t.Errorf("message = %q, want %q", apiErr.Message, "invalid model")
	}
}

func TestRerank_WithoutDocuments(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req cohereRerankRequest
		json.NewDecoder(r.Body).Decode(&req)
		if req.ReturnDocuments {
			t.Error("return_documents should be false")
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"id":"r","results":[{"index":0,"relevance_score":0.5}]}`))
	}))
	defer ts.Close()

	p := newTestProvider(t, ts.URL)
	resp, err := p.Rerank(context.Background(), &models.RerankRequest{
		Model:     "rerank-english-v3.0",
		Query:     "q",
		Documents: []string{"d"},
	})
	if err != nil {
		t.Fatalf("error: %v", err)
	}
	if resp.Results[0].Document != nil {
		t.Error("expected nil document when return_documents=false")
	}
}
