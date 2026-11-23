package server

import (
	"bufio"
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// A streamed /v1/messages turn on a Gemini model must report its input
// tokens. Gemini sends usageMetadata only on the last chunk, after
// message_start has gone out, so the closing message_delta has to carry it.
func TestAnthropicMessagesStreamedGeminiReportsInputTokens(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/models" {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"data":[]}`))
			return
		}
		if r.URL.Path != "/v1beta/models/gemini-3.7-flash:streamGenerateContent" {
			t.Errorf("upstream path = %q", r.URL.Path)
			http.Error(w, "wrong model", http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = w.Write([]byte(
			"data: {\"candidates\":[{\"content\":{\"role\":\"model\",\"parts\":[{\"text\":\"Hel\"}]}}]}\n\n" +
				"data: {\"candidates\":[{\"content\":{\"role\":\"model\",\"parts\":[{\"text\":\"lo\"}]},\"finishReason\":\"STOP\"}]," +
				"\"usageMetadata\":{\"promptTokenCount\":21000,\"candidatesTokenCount\":900,\"thoughtsTokenCount\":50,\"totalTokenCount\":21950}}\n\n",
		))
	}))
	defer upstream.Close()

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name:     "alk-test",
		Key:      "test-virtual-key",
		Owner:    "test-org",
		KeyType:  "internal",
		Metadata: map[string]string{"access_groups": "alk_authoring"},
	}}
	cfg.Routing.AccessGroups = config.AccessGroupsConfig{
		"alk_authoring": {Models: []string{"vertex_ai/gemini-3.7-flash"}},
	}
	cfg.Providers["vertex"] = config.ProviderConfig{
		BaseURL:   upstream.URL,
		APIKey:    "test-key",
		APIFormat: "gemini",
		Models:    []string{"vertex_ai/gemini-3.7-flash"},
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)

	request := httptest.NewRequest("POST", "/v1/messages", bytes.NewBufferString(
		`{"model":"vertex_ai/gemini-3.7-flash","max_tokens":32,"stream":true,`+
			`"messages":[{"role":"user","content":"Say hi"}]}`,
	))
	request.Header.Set("Authorization", "Bearer test-virtual-key")
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("anthropic-version", "2023-06-01")
	response := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("status = %d; body=%s", response.Code, response.Body.String())
	}

	type usage struct {
		InputTokens  *int `json:"input_tokens"`
		OutputTokens *int `json:"output_tokens"`
	}
	var deltas []usage
	scanner := bufio.NewScanner(strings.NewReader(response.Body.String()))
	event := ""
	for scanner.Scan() {
		line := scanner.Text()
		if name, ok := strings.CutPrefix(line, "event: "); ok {
			event = name
			continue
		}
		data, ok := strings.CutPrefix(line, "data: ")
		if !ok || event != "message_delta" {
			continue
		}
		var frame struct {
			Usage usage `json:"usage"`
		}
		if err := json.Unmarshal([]byte(data), &frame); err != nil {
			t.Fatalf("message_delta is not JSON: %v: %s", err, data)
		}
		deltas = append(deltas, frame.Usage)
	}
	if len(deltas) != 1 {
		t.Fatalf("want one message_delta, got %d; body=%s", len(deltas), response.Body.String())
	}
	got := deltas[0]
	if got.InputTokens == nil || *got.InputTokens != 21000 {
		t.Errorf("message_delta input_tokens = %v, want 21000; body=%s", deref(got.InputTokens), response.Body.String())
	}
	if got.OutputTokens == nil || *got.OutputTokens != 950 {
		t.Errorf("message_delta output_tokens = %v, want 950", deref(got.OutputTokens))
	}
}

func deref(n *int) any {
	if n == nil {
		return nil
	}
	return *n
}
