package server

import (
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

func TestAnthropicMessagesRoutesClaudeAliasToGemini(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/models" {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"data":[]}`))
			return
		}
		if r.URL.Path != "/v1beta/models/gemini-3.7-flash:generateContent" {
			t.Errorf("upstream path = %q", r.URL.Path)
			http.Error(w, "wrong model", http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"candidates":[{"content":{"role":"model","parts":[{"text":"GEMINI_OK"}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":2,"totalTokenCount":7}}`))
	}))
	defer upstream.Close()

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name:    "alk-test",
		Key:     "test-virtual-key",
		Owner:   "test-org",
		KeyType: "internal",
		Metadata: map[string]string{
			"access_groups": "alk_authoring",
		},
	}}
	cfg.Routing.AccessGroups = config.AccessGroupsConfig{
		"alk_authoring": {
			Models:  []string{"vertex_ai/gemini-3.7-flash"},
			Aliases: map[string]string{"claude-sonnet-4-6": "vertex_ai/gemini-3.7-flash"},
		},
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
		`{"model":"claude-sonnet-4-6","max_tokens":32,"messages":[{"role":"user","content":"Say hi"}]}`,
	))
	request.Header.Set("Authorization", "Bearer test-virtual-key")
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("anthropic-version", "2023-06-01")
	response := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("status = %d; body=%s", response.Code, response.Body.String())
	}
	if !strings.Contains(response.Body.String(), "GEMINI_OK") || !strings.Contains(response.Body.String(), `"model":"claude-sonnet-4-6"`) {
		t.Fatalf("unexpected Anthropic response: %s", response.Body.String())
	}
}

func TestAnthropicNativeAliasForwardsResolvedModel(t *testing.T) {
	const alias = "claude-sonnet-4-6"
	const target = "claude-haiku-4-5"
	tests := []struct {
		name   string
		path   string
		body   string
		answer string
	}{
		{"messages", "/v1/messages", `{"model":"` + alias + `","max_tokens":32,"messages":[{"role":"user","content":"Hi"}]}`, `{"id":"msg_1","type":"message","role":"assistant","model":"` + target + `","content":[{"type":"text","text":"Hello"}],"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":1}}`},
		{"count_tokens", "/v1/messages/count_tokens", `{"model":"` + alias + `","messages":[{"role":"user","content":"Hi"}]}`, `{"input_tokens":1}`},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/v1/models" {
					w.Header().Set("Content-Type", "application/json")
					_, _ = w.Write([]byte(`{"data":[]}`))
					return
				}
				if r.URL.Path != tt.path {
					t.Errorf("upstream path = %q, want %q", r.URL.Path, tt.path)
					http.Error(w, "wrong path", http.StatusBadRequest)
					return
				}
				var request map[string]json.RawMessage
				if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
					t.Errorf("decode upstream request: %v", err)
				}
				if string(request["model"]) != `"`+target+`"` {
					t.Errorf("upstream model = %s, want %q", request["model"], target)
				}
				if _, ok := request["messages"]; !ok {
					t.Error("messages missing from upstream request")
				}
				w.Header().Set("Content-Type", "application/json")
				_, _ = w.Write([]byte(tt.answer))
			}))
			defer upstream.Close()

			cfg := config.DefaultConfig()
			cfg.Auth.Enabled = true
			cfg.Auth.Keys = []config.AuthKeyConfig{{
				Name: "alk-test", Key: "test-virtual-key", Owner: "test-org", KeyType: "internal",
				Metadata: map[string]string{"access_groups": "alk_authoring"},
			}}
			cfg.Routing.AccessGroups = config.AccessGroupsConfig{
				"alk_authoring": {Models: []string{target}, Aliases: map[string]string{alias: target}},
			}
			cfg.Providers["anthropic"] = config.ProviderConfig{
				BaseURL: upstream.URL, APIKey: "test-key", APIFormat: "anthropic", Models: []string{target},
			}
			registry, err := providers.NewRegistry(cfg)
			if err != nil {
				t.Fatalf("creating registry: %v", err)
			}
			srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
			srv.ready.Store(true)
			request := httptest.NewRequest("POST", tt.path, bytes.NewBufferString(tt.body))
			request.Header.Set("Authorization", "Bearer test-virtual-key")
			request.Header.Set("Content-Type", "application/json")
			response := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(response, request)
			if response.Code != http.StatusOK {
				t.Fatalf("status = %d; body=%s", response.Code, response.Body.String())
			}
		})
	}
}
