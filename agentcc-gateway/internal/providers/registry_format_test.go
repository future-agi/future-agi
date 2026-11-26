package providers

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The api_format persisted by the dialog must select the native transport.
func TestSavedAPIFormatSelectsNativeAdapter(t *testing.T) {
	for _, tc := range []struct {
		format string
		path   string
		body   string
	}{
		{"openai", "/v1/chat/completions", `{"choices":[{"message":{"role":"assistant","content":"synthetic reply"},"finish_reason":"stop"}]}`},
		{"anthropic", "/v1/messages", `{"id":"synthetic","type":"message","role":"assistant","content":[{"type":"text","text":"synthetic reply"}],"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":2}}`},
		{"google", "/v1beta/models/synthetic-model:generateContent", `{"candidates":[{"content":{"role":"model","parts":[{"text":"synthetic reply"}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":1,"candidatesTokenCount":2}}`},
		{"azure", "/openai/deployments/synthetic-model/chat/completions", `{"choices":[{"message":{"role":"assistant","content":"synthetic reply"},"finish_reason":"stop"}]}`},
	} {
		t.Run(tc.format, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path != tc.path {
					t.Errorf("upstream path = %q, want %q", r.URL.Path, tc.path)
				}
				if r.Method != http.MethodPost {
					t.Errorf("upstream method = %q, want POST", r.Method)
				}
				switch tc.format {
				case "openai":
					if r.Header.Get("Authorization") != "Bearer synthetic-key" {
						t.Error("missing OpenAI bearer authentication")
					}
				case "anthropic":
					if r.Header.Get("x-api-key") != "synthetic-key" || r.Header.Get("anthropic-version") == "" {
						t.Error("missing native Anthropic authentication or version")
					}
				case "google":
					if r.Header.Get("x-goog-api-key") != "synthetic-key" {
						t.Error("missing native Google authentication")
					}
				case "azure":
					if r.Header.Get("api-key") != "synthetic-key" || r.URL.Query().Get("api-version") != "2024-10-21" {
						t.Error("missing native Azure authentication or API version")
					}
				}
				if tc.format != "openai" && r.Header.Get("Authorization") != "" {
					t.Error("native adapter unexpectedly sent OpenAI bearer authentication")
				}
				w.Header().Set("Content-Type", "application/json")
				_, _ = w.Write([]byte(tc.body))
			}))
			defer server.Close()

			wire, err := json.Marshal(map[string]string{
				"api_format": tc.format,
				"base_url":   server.URL,
			})
			if err != nil {
				t.Fatal(err)
			}
			var saved config.ProviderConfig
			if err := json.Unmarshal(wire, &saved); err != nil {
				t.Fatal(err)
			}
			// Secrets are supplied separately; ProviderConfig excludes them from JSON.
			saved.APIKey = "synthetic-key"
			provider, err := createProvider(tc.format, saved)
			if err != nil {
				t.Fatal(err)
			}
			defer provider.Close()
			response, err := provider.ChatCompletion(context.Background(), &models.ChatCompletionRequest{
				Model:    "synthetic-model",
				Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"synthetic request"`)}},
			})
			if err != nil {
				t.Fatal(err)
			}
			if len(response.Choices) != 1 || string(response.Choices[0].Message.Content) != `"synthetic reply"` {
				t.Fatalf("native response was not translated: %+v", response)
			}
		})
	}
}
