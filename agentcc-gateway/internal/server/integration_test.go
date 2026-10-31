//go:build integration

package server

import (
	"bytes"
	"encoding/json"
	"fmt"
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

	// Anthropic (native Messages API adapter).
	if key := os.Getenv("ANTHROPIC_API_KEY"); key != "" {
		cfg.Providers["anthropic"] = config.ProviderConfig{
			BaseURL:   "https://api.anthropic.com",
			APIKey:    key,
			APIFormat: "anthropic",
			Models:    []string{anthropicITModel},
		}
	}

	// Gemini (native generateContent adapter, Google AI Studio key).
	if key := os.Getenv("GEMINI_API_KEY"); key != "" {
		cfg.Providers["gemini"] = config.ProviderConfig{
			BaseURL:   "https://generativelanguage.googleapis.com",
			APIKey:    key,
			APIFormat: "gemini",
			Models:    []string{geminiITModel},
		}
	}

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

// --- Native adapters (Anthropic, Gemini) ---
//
// These go through the gateway's own translation to and from each provider's
// native API, so they catch a response shape the adapter no longer
// understands on a real model. Model IDs churn; override the defaults with
// AGENTCC_IT_ANTHROPIC_MODEL / AGENTCC_IT_GEMINI_MODEL. The defaults are
// aliases that track the provider's current small model.

var (
	anthropicITModel = envOr("AGENTCC_IT_ANTHROPIC_MODEL", "claude-haiku-4-5")
	geminiITModel    = envOr("AGENTCC_IT_GEMINI_MODEL", "gemini-flash-latest")
)

func envOr(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

// canonicalFinishReasons are the finish reasons the gateway emits; anything
// else means an adapter passed a provider value through untranslated.
var canonicalFinishReasons = map[string]bool{"stop": true, "length": true, "tool_calls": true, "content_filter": true}

// Thinking models (Gemini's Flash line) spend part of max_tokens on thoughts,
// so leave room for an answer.
const nativeMaxTokens = 1024

const weatherTool = `[{"type":"function","function":{"name":"get_weather","description":"Get the current weather for a city.",` +
	`"parameters":{"type":"object","properties":{"location":{"type":"string","description":"City name"}},"required":["location"]}}}]`

func postIntegrationChat(t *testing.T, srv *Server, body string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// nativeNonStreaming checks a plain completion: text back, a canonical
// finish reason, usage, and the provider header.
func nativeNonStreaming(t *testing.T, srv *Server, provider, model string) {
	w := postIntegrationChat(t, srv, fmt.Sprintf(`{"model":%q,"messages":[{"role":"user","content":"Say hello in exactly 3 words."}],"max_tokens":%d}`,
		model, nativeMaxTokens))
	if w.Code != 200 {
		t.Fatalf("%s non-streaming: status=%d body=%s", provider, w.Code, w.Body.String())
	}

	var resp models.ChatCompletionResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("parsing response: %v", err)
	}
	t.Logf("%s response: id=%s model=%s choices=%d usage=%+v", provider, resp.ID, resp.Model, len(resp.Choices), resp.Usage)

	if len(resp.Choices) == 0 {
		t.Fatal("expected at least one choice")
	}
	choice := resp.Choices[0]
	var content string
	_ = json.Unmarshal(choice.Message.Content, &content)
	if !canonicalFinishReasons[choice.FinishReason] {
		t.Errorf("finish_reason = %q, want one of stop/length/tool_calls/content_filter", choice.FinishReason)
	}
	if strings.TrimSpace(content) == "" && choice.FinishReason != "length" {
		t.Errorf("empty content with finish_reason %q: the adapter dropped the answer", choice.FinishReason)
	}
	if resp.Usage == nil || resp.Usage.PromptTokens == 0 || resp.Usage.TotalTokens == 0 {
		t.Errorf("expected usage with prompt and total tokens, got %+v", resp.Usage)
	}
	if got := w.Header().Get("x-agentcc-provider"); got != provider {
		t.Errorf("x-agentcc-provider = %q, want %s", got, provider)
	}
}

// streamResult is what an OpenAI client assembles from the gateway's SSE.
type streamResult struct {
	chunks       int
	done         bool
	content      string
	finishReason string
	toolCalls    map[int]*models.ToolCall
}

func readStream(t *testing.T, body string) streamResult {
	t.Helper()
	res := streamResult{toolCalls: map[int]*models.ToolCall{}}
	for _, line := range strings.Split(body, "\n") {
		data, ok := strings.CutPrefix(line, "data: ")
		if !ok {
			continue
		}
		if data == "[DONE]" {
			res.done = true
			continue
		}
		var chunk models.StreamChunk
		if err := json.Unmarshal([]byte(data), &chunk); err != nil {
			t.Errorf("chunk is not JSON: %v: %s", err, data)
			continue
		}
		res.chunks++
		for _, c := range chunk.Choices {
			if c.Delta.Content != nil {
				res.content += *c.Delta.Content
			}
			if c.FinishReason != nil && *c.FinishReason != "" {
				res.finishReason = *c.FinishReason
			}
			for _, d := range c.Delta.ToolCalls {
				tc := res.toolCalls[d.Index]
				if tc == nil {
					tc = &models.ToolCall{}
					res.toolCalls[d.Index] = tc
				}
				if d.ID != "" {
					tc.ID = d.ID
				}
				if d.Function != nil {
					if d.Function.Name != "" {
						tc.Function.Name = d.Function.Name
					}
					tc.Function.Arguments += d.Function.Arguments
				}
			}
		}
	}
	return res
}

func nativeStreaming(t *testing.T, srv *Server, provider, model string) {
	w := postIntegrationChat(t, srv, fmt.Sprintf(`{"model":%q,"messages":[{"role":"user","content":"Say hello in exactly 3 words."}],"max_tokens":%d,"stream":true}`,
		model, nativeMaxTokens))
	if w.Code != 200 {
		t.Fatalf("%s streaming: status=%d body=%s", provider, w.Code, w.Body.String())
	}
	if ct := w.Header().Get("Content-Type"); !strings.HasPrefix(ct, "text/event-stream") {
		t.Errorf("Content-Type = %q, want text/event-stream", ct)
	}

	res := readStream(t, w.Body.String())
	t.Logf("%s stream: %d chunks, finish_reason=%q, content=%q", provider, res.chunks, res.finishReason, res.content)

	if res.chunks == 0 || !res.done {
		t.Fatalf("expected chunks then [DONE], got %d chunks, done=%v", res.chunks, res.done)
	}
	if !canonicalFinishReasons[res.finishReason] {
		t.Errorf("finish_reason = %q, want one of stop/length/tool_calls/content_filter", res.finishReason)
	}
	if strings.TrimSpace(res.content) == "" && res.finishReason != "length" {
		t.Errorf("empty streamed content with finish_reason %q: the adapter dropped the answer", res.finishReason)
	}
}

// checkWeatherCall checks a forced get_weather call came back whole.
func checkWeatherCall(t *testing.T, finishReason string, calls []models.ToolCall) {
	t.Helper()
	if finishReason != "tool_calls" {
		t.Errorf("finish_reason = %q, want tool_calls", finishReason)
	}
	if len(calls) == 0 {
		t.Fatal("expected a tool call")
	}
	tc := calls[0]
	var args map[string]any
	if err := json.Unmarshal([]byte(tc.Function.Arguments), &args); err != nil {
		t.Errorf("tool call arguments are not JSON: %v: %q", err, tc.Function.Arguments)
	}
	if tc.ID == "" || tc.Function.Name != "get_weather" || args["location"] == nil {
		t.Errorf("tool call = %+v, want an id, get_weather and a location", tc)
	}
}

func weatherRequest(model string, stream bool) string {
	return fmt.Sprintf(`{"model":%q,"messages":[{"role":"user","content":"What is the weather in Paris right now?"}],"max_tokens":%d,`+
		`"tools":%s,"tool_choice":{"type":"function","function":{"name":"get_weather"}},"stream":%v}`,
		model, nativeMaxTokens, weatherTool, stream)
}

func nativeToolCall(t *testing.T, srv *Server, provider, model string) {
	w := postIntegrationChat(t, srv, weatherRequest(model, false))
	if w.Code != 200 {
		t.Fatalf("%s tool call: status=%d body=%s", provider, w.Code, w.Body.String())
	}
	var resp models.ChatCompletionResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("parsing response: %v", err)
	}
	if len(resp.Choices) == 0 {
		t.Fatal("expected at least one choice")
	}
	t.Logf("%s tool call: finish_reason=%q tool_calls=%+v", provider, resp.Choices[0].FinishReason, resp.Choices[0].Message.ToolCalls)
	checkWeatherCall(t, resp.Choices[0].FinishReason, resp.Choices[0].Message.ToolCalls)
}

func nativeStreamingToolCall(t *testing.T, srv *Server, provider, model string) {
	w := postIntegrationChat(t, srv, weatherRequest(model, true))
	if w.Code != 200 {
		t.Fatalf("%s streaming tool call: status=%d body=%s", provider, w.Code, w.Body.String())
	}
	res := readStream(t, w.Body.String())
	var calls []models.ToolCall
	for i := 0; i < len(res.toolCalls); i++ {
		if tc := res.toolCalls[i]; tc != nil {
			calls = append(calls, *tc)
		}
	}
	t.Logf("%s streaming tool call: %d chunks, finish_reason=%q tool_calls=%+v", provider, res.chunks, res.finishReason, calls)
	if !res.done {
		t.Error("stream did not end with [DONE]")
	}
	checkWeatherCall(t, res.finishReason, calls)
}

func TestIntegration_Anthropic_NonStreaming(t *testing.T) {
	skipIfNoKey(t, "ANTHROPIC_API_KEY")
	nativeNonStreaming(t, createRealServer(t), "anthropic", anthropicITModel)
}

func TestIntegration_Anthropic_Streaming(t *testing.T) {
	skipIfNoKey(t, "ANTHROPIC_API_KEY")
	nativeStreaming(t, createRealServer(t), "anthropic", anthropicITModel)
}

func TestIntegration_Anthropic_ToolCall(t *testing.T) {
	skipIfNoKey(t, "ANTHROPIC_API_KEY")
	nativeToolCall(t, createRealServer(t), "anthropic", anthropicITModel)
}

func TestIntegration_Anthropic_StreamingToolCall(t *testing.T) {
	skipIfNoKey(t, "ANTHROPIC_API_KEY")
	nativeStreamingToolCall(t, createRealServer(t), "anthropic", anthropicITModel)
}

func TestIntegration_Gemini_NonStreaming(t *testing.T) {
	skipIfNoKey(t, "GEMINI_API_KEY")
	nativeNonStreaming(t, createRealServer(t), "gemini", geminiITModel)
}

func TestIntegration_Gemini_Streaming(t *testing.T) {
	skipIfNoKey(t, "GEMINI_API_KEY")
	nativeStreaming(t, createRealServer(t), "gemini", geminiITModel)
}

func TestIntegration_Gemini_ToolCall(t *testing.T) {
	skipIfNoKey(t, "GEMINI_API_KEY")
	nativeToolCall(t, createRealServer(t), "gemini", geminiITModel)
}

func TestIntegration_Gemini_StreamingToolCall(t *testing.T) {
	skipIfNoKey(t, "GEMINI_API_KEY")
	nativeStreamingToolCall(t, createRealServer(t), "gemini", geminiITModel)
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
		{"anthropic", anthropicITModel, "ANTHROPIC_API_KEY"},
		{"gemini", geminiITModel, "GEMINI_API_KEY"},
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
