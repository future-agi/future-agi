//go:build integration

package server

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// createRealServer builds a gateway server wired to real provider APIs.
func createRealServer(t *testing.T) *Server {
	t.Helper()

	cfg := config.DefaultConfig()

	// OpenAI
	if key := os.Getenv("OPENAI_API_KEY"); key != "" {
		cfg.Providers["openai"] = config.ProviderConfig{
			BaseURL:   "https://api.openai.com",
			APIKey:    key,
			APIFormat: "openai",
			Models:    []string{"gpt-4o-mini", "gpt-4o"},
		}
	}

	// Anthropic — skipped in v1 (needs native Anthropic adapter, not OpenAI-compat).
	// Will be enabled when provider-adapters feature (1.2b) lands.

	// Groq (OpenAI-compatible)
	if key := os.Getenv("GROQ_API_KEY"); key != "" {
		cfg.Providers["groq"] = config.ProviderConfig{
			BaseURL:   "https://api.groq.com/openai",
			APIKey:    key,
			APIFormat: "openai",
			Models:    []string{"llama-3.3-70b-versatile", "gemma2-9b-it"},
		}
	}

	// xAI (OpenAI-compatible)
	if key := os.Getenv("XAI_API_KEY"); key != "" {
		cfg.Providers["xai"] = config.ProviderConfig{
			BaseURL:   "https://api.x.ai",
			APIKey:    key,
			APIFormat: "openai",
			Models:    []string{"grok-3-mini-fast"},
		}
	}

	// OpenRouter (OpenAI-compatible)
	if key := os.Getenv("OPENROUTER_API_KEY"); key != "" {
		cfg.Providers["openrouter"] = config.ProviderConfig{
			BaseURL:   "https://openrouter.ai/api",
			APIKey:    key,
			APIFormat: "openai",
			Models:    []string{"meta-llama/llama-3.3-70b-instruct"},
		}
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

func skipIfNoKey(t *testing.T, envVar string) {
	t.Helper()
	if os.Getenv(envVar) == "" {
		t.Skipf("skipping: %s not set", envVar)
	}
}

// --- OpenAI ---

func TestIntegration_OpenAI_NonStreaming(t *testing.T) {
	skipIfNoKey(t, "OPENAI_API_KEY")
	srv := createRealServer(t)

	reqBody := `{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Say hello in exactly 3 words."}],"max_tokens":20}`
	req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewBufferString(reqBody))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != 200 {
		t.Fatalf("OpenAI non-streaming: status=%d body=%s", w.Code, w.Body.String())
	}

	var resp models.ChatCompletionResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("parsing response: %v", err)
	}

	t.Logf("OpenAI response: id=%s model=%s choices=%d usage=%+v", resp.ID, resp.Model, len(resp.Choices), resp.Usage)

	if len(resp.Choices) == 0 {
		t.Error("expected at least one choice")
	}
	if resp.Usage == nil || resp.Usage.TotalTokens == 0 {
		t.Error("expected usage data")
	}
	if w.Header().Get("x-agentcc-provider") != "openai" {
		t.Errorf("x-agentcc-provider = %q, want openai", w.Header().Get("x-agentcc-provider"))
	}
}

func TestIntegration_OpenAI_Streaming(t *testing.T) {
	skipIfNoKey(t, "OPENAI_API_KEY")
	srv := createRealServer(t)

	reqBody := `{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Say hello in exactly 3 words."}],"max_tokens":20,"stream":true}`
	req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewBufferString(reqBody))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != 200 {
		t.Fatalf("OpenAI streaming: status=%d body=%s", w.Code, w.Body.String())
	}

	ct := w.Header().Get("Content-Type")
	if !strings.HasPrefix(ct, "text/event-stream") {
		t.Errorf("Content-Type = %q, want text/event-stream", ct)
	}

	body := w.Body.String()
	lines := strings.Split(body, "\n")
	var dataLines []string
	var content strings.Builder
	for _, line := range lines {
		if strings.HasPrefix(line, "data: ") {
			data := strings.TrimPrefix(line, "data: ")
			dataLines = append(dataLines, data)
			if data != "[DONE]" {
				var chunk models.StreamChunk
				if err := json.Unmarshal([]byte(data), &chunk); err == nil {
					for _, c := range chunk.Choices {
						if c.Delta.Content != nil {
							content.WriteString(*c.Delta.Content)
						}
					}
				}
			}
		}
	}

	t.Logf("OpenAI stream: %d chunks, content=%q", len(dataLines), content.String())

	if len(dataLines) < 2 {
		t.Fatalf("expected at least 2 data lines, got %d", len(dataLines))
	}
	if dataLines[len(dataLines)-1] != "[DONE]" {
		t.Errorf("last data line = %q, want [DONE]", dataLines[len(dataLines)-1])
	}
}

func TestIntegration_OpenAI_PrefixRouting(t *testing.T) {
	skipIfNoKey(t, "OPENAI_API_KEY")
	srv := createRealServer(t)

	reqBody := `{"model":"openai/gpt-4o-mini","messages":[{"role":"user","content":"Say yes."}],"max_tokens":5}`
	req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewBufferString(reqBody))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != 200 {
		t.Fatalf("OpenAI prefix routing: status=%d body=%s", w.Code, w.Body.String())
	}
	t.Logf("Prefix routing OK: provider=%s", w.Header().Get("x-agentcc-provider"))
}

// --- Groq ---

func TestIntegration_Groq_NonStreaming(t *testing.T) {
	skipIfNoKey(t, "GROQ_API_KEY")
	srv := createRealServer(t)

	reqBody := `{"model":"llama-3.3-70b-versatile","messages":[{"role":"user","content":"Say hello in exactly 3 words."}],"max_tokens":20}`
	req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewBufferString(reqBody))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != 200 {
		t.Fatalf("Groq non-streaming: status=%d body=%s", w.Code, w.Body.String())
	}

	var resp models.ChatCompletionResponse
	json.Unmarshal(w.Body.Bytes(), &resp)
	t.Logf("Groq response: id=%s model=%s choices=%d", resp.ID, resp.Model, len(resp.Choices))

	if w.Header().Get("x-agentcc-provider") != "groq" {
		t.Errorf("x-agentcc-provider = %q, want groq", w.Header().Get("x-agentcc-provider"))
	}
}

func TestIntegration_Groq_Streaming(t *testing.T) {
	skipIfNoKey(t, "GROQ_API_KEY")
	srv := createRealServer(t)

	reqBody := `{"model":"groq/llama-3.3-70b-versatile","messages":[{"role":"user","content":"Say hello in exactly 3 words."}],"max_tokens":20,"stream":true}`
	req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewBufferString(reqBody))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != 200 {
		t.Fatalf("Groq streaming: status=%d body=%s", w.Code, w.Body.String())
	}

	body := w.Body.String()
	t.Logf("Groq stream response length: %d bytes", len(body))

	if !strings.Contains(body, "data: [DONE]") {
		t.Error("stream should end with [DONE]")
	}
}

// --- xAI ---

func TestIntegration_XAI_NonStreaming(t *testing.T) {
	skipIfNoKey(t, "XAI_API_KEY")
	srv := createRealServer(t)

	reqBody := `{"model":"grok-3-mini-fast","messages":[{"role":"user","content":"Say hello in exactly 3 words."}],"max_tokens":20}`
	req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewBufferString(reqBody))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != 200 {
		t.Fatalf("xAI non-streaming: status=%d body=%s", w.Code, w.Body.String())
	}

	var resp models.ChatCompletionResponse
	json.Unmarshal(w.Body.Bytes(), &resp)
	t.Logf("xAI response: id=%s model=%s choices=%d", resp.ID, resp.Model, len(resp.Choices))
}

// --- OpenRouter ---

func TestIntegration_OpenRouter_NonStreaming(t *testing.T) {
	skipIfNoKey(t, "OPENROUTER_API_KEY")
	srv := createRealServer(t)

	reqBody := `{"model":"openrouter/meta-llama/llama-3.3-70b-instruct","messages":[{"role":"user","content":"Say hello in exactly 3 words."}],"max_tokens":20}`
	req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewBufferString(reqBody))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code == 502 && strings.Contains(w.Body.String(), "401") {
		t.Skip("OpenRouter API key expired/invalid, skipping")
	}
	if w.Code != 200 {
		t.Fatalf("OpenRouter non-streaming: status=%d body=%s", w.Code, w.Body.String())
	}

	var resp models.ChatCompletionResponse
	json.Unmarshal(w.Body.Bytes(), &resp)
	t.Logf("OpenRouter response: id=%s model=%s choices=%d", resp.ID, resp.Model, len(resp.Choices))
}

// --- List Models ---

func TestIntegration_ListModels(t *testing.T) {
	skipIfNoKey(t, "OPENAI_API_KEY")
	srv := createRealServer(t)

	req := httptest.NewRequest("GET", "/v1/models", nil)
	w := httptest.NewRecorder()

	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != 200 {
		t.Fatalf("list models: status=%d body=%s", w.Code, w.Body.String())
	}

	var resp models.ModelListResponse
	json.Unmarshal(w.Body.Bytes(), &resp)
	t.Logf("Models listed: %d total", len(resp.Data))

	// Log first few model IDs.
	for i, m := range resp.Data {
		if i >= 10 {
			t.Logf("  ... and %d more", len(resp.Data)-10)
			break
		}
		t.Logf("  model: %s (owned by: %s)", m.ID, m.OwnedBy)
	}
}

// --- Multi-provider round trip ---

func TestIntegration_MultiProvider_SamePrompt(t *testing.T) {
	srv := createRealServer(t)

	providerModels := []struct {
		name   string
		model  string
		envKey string
	}{
		{"openai", "gpt-4o-mini", "OPENAI_API_KEY"},
		{"groq", "llama-3.3-70b-versatile", "GROQ_API_KEY"},
		{"xai", "grok-3-mini-fast", "XAI_API_KEY"},
	}

	for _, pm := range providerModels {
		t.Run(pm.name, func(t *testing.T) {
			if os.Getenv(pm.envKey) == "" {
				t.Skipf("skipping %s: %s not set", pm.name, pm.envKey)
			}

			body, _ := json.Marshal(map[string]interface{}{
				"model":      pm.model,
				"messages":   []map[string]string{{"role": "user", "content": "What is 2+2? Reply with just the number."}},
				"max_tokens": 5,
			})

			req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewReader(body))
			req.Header.Set("Content-Type", "application/json")
			w := httptest.NewRecorder()

			srv.httpServer.Handler.ServeHTTP(w, req)

			if w.Code != 200 {
				t.Fatalf("%s: status=%d body=%s", pm.name, w.Code, w.Body.String())
			}

			var resp models.ChatCompletionResponse
			json.Unmarshal(w.Body.Bytes(), &resp)

			var content string
			if len(resp.Choices) > 0 {
				raw := resp.Choices[0].Message.Content
				json.Unmarshal(raw, &content)
			}

			t.Logf("%s (%s): answer=%q latency=%sms provider=%s",
				pm.name, resp.Model, content,
				w.Header().Get("x-agentcc-latency-ms"),
				w.Header().Get("x-agentcc-provider"))

			_, _ = io.ReadAll(w.Result().Body)
		})
	}
}
