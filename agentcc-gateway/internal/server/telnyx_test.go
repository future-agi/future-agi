package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

func TestTelnyxGateway(t *testing.T) {
	const model = "MiniMaxAI/MiniMax-M3-MXFP8"
	for _, tc := range []struct {
		name   string
		stream bool
		status int
	}{
		{"chat", false, http.StatusOK},
		{"stream", true, http.StatusOK},
		{"upstream rejection", false, http.StatusUnauthorized},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Synthetic credentials never leave the local test server.
			key := t.Name()
			t.Setenv("TELNYX_TEST_API_KEY", key)
			t.Setenv("AGENTCC_INTERNAL_API_KEY", "")
			t.Setenv("AGENTCC_AUTH_ENABLED", "false")
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				// Pinned to Telnyx's OpenAI Chat API, not the preset under test.
				if r.Method != http.MethodPost || r.URL.Path != "/v2/ai/openai/chat/completions" {
					t.Errorf("upstream request = %s %s, want POST /v2/ai/openai/chat/completions", r.Method, r.URL.Path)
					http.NotFound(w, r)
					return
				}
				if r.Header.Get("Authorization") != "Bearer "+key {
					t.Error("upstream did not receive the configured synthetic credential")
					http.Error(w, "unauthorized", http.StatusUnauthorized)
					return
				}
				var req models.ChatCompletionRequest
				if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
					t.Errorf("decode upstream request: %v", err)
					http.Error(w, "invalid request", http.StatusBadRequest)
					return
				}
				if req.Model != model || req.Stream != tc.stream || len(req.Messages) != 1 || string(req.Messages[0].Content) != `"telnyx-synthetic-prompt"` {
					t.Error("gateway changed the model, stream flag, or prompt")
				}
				if tc.status != http.StatusOK {
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(tc.status)
					fmt.Fprint(w, `{"error":{"message":"synthetic upstream rejection","type":"invalid_request_error","code":"invalid_api_key"}}`)
					return
				}
				if req.Stream {
					w.Header().Set("Content-Type", "text/event-stream")
					fmt.Fprintf(w, "data: {\"id\":\"telnyx-fixture\",\"object\":\"chat.completion.chunk\",\"model\":%q,\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\",\"content\":\"telnyx-synthetic-ok\"},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n", model)
					return
				}
				w.Header().Set("Content-Type", "application/json")
				fmt.Fprintf(w, `{"id":"telnyx-fixture","object":"chat.completion","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"telnyx-synthetic-ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}`, model)
			}))
			defer upstream.Close()

			path := filepath.Join(t.TempDir(), "config.yaml")
			yaml := fmt.Sprintf("providers:\n  telnyx:\n    type: telnyx\n    base_url: %s/v2/ai\n    api_key: ${TELNYX_TEST_API_KEY}\n    local: false\n    models: [%s]\n", upstream.URL, model)
			if err := os.WriteFile(path, []byte(yaml), 0600); err != nil {
				t.Fatal(err)
			}
			cfg, err := config.Load(path)
			if err != nil {
				t.Fatalf("load Telnyx YAML: %v", err)
			}
			registry, err := providers.NewRegistry(cfg)
			if err != nil {
				t.Fatalf("register Telnyx provider: %v", err)
			}
			defer registry.Close()
			srv := New(cfg, path, registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
			gateway := httptest.NewServer(srv.httpServer.Handler)
			defer gateway.Close()

			body := fmt.Sprintf(`{"model":%q,"messages":[{"role":"user","content":"telnyx-synthetic-prompt"}],"stream":%t}`, model, tc.stream)
			resp, err := gateway.Client().Post(gateway.URL+"/v1/chat/completions", "application/json", bytes.NewBufferString(body))
			if err != nil {
				t.Fatal(err)
			}
			defer resp.Body.Close()
			data, err := io.ReadAll(resp.Body)
			if err != nil {
				t.Fatal(err)
			}
			if resp.StatusCode != tc.status {
				t.Fatalf("gateway status = %d, want %d", resp.StatusCode, tc.status)
			}
			if tc.status != http.StatusOK {
				if !bytes.Contains(data, []byte("synthetic upstream rejection")) {
					t.Error("gateway did not preserve upstream error message")
				}
				return
			}
			if tc.stream {
				if !strings.HasPrefix(resp.Header.Get("Content-Type"), "text/event-stream") || !bytes.Contains(data, []byte(`"content":"telnyx-synthetic-ok"`)) || !bytes.Contains(data, []byte("data: [DONE]")) {
					t.Error("gateway did not return the expected SSE content and terminator")
				}
				return
			}
			var result models.ChatCompletionResponse
			if err := json.Unmarshal(data, &result); err != nil {
				t.Fatal(err)
			}
			if result.Model != model || len(result.Choices) != 1 || string(result.Choices[0].Message.Content) != `"telnyx-synthetic-ok"` {
				t.Error("gateway changed the synthetic response model or content")
			}
			if result.Usage == nil || result.Usage.PromptTokens != 3 || result.Usage.CompletionTokens != 2 || result.Usage.TotalTokens != 5 {
				t.Error("gateway did not preserve token usage")
			}
		})
	}
}
