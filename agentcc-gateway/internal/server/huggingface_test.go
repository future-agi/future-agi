package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

func TestHuggingFacePresetChat(t *testing.T) {
	const model = "test-org/test-model:fastest"
	for _, tc := range []struct {
		name       string
		baseURL    string
		pathPrefix *string
		wantHost   string
		wantPath   string
		stream     bool
		// fromFile loads a type-only provider through config.Load, the path an
		// operator's config file takes, instead of building ProviderConfig in code.
		fromFile bool
	}{
		{name: "default", fromFile: true, wantHost: "router.huggingface.co", wantPath: "/v1/chat/completions"},
		{name: "default_stream", fromFile: true, wantHost: "router.huggingface.co", wantPath: "/v1/chat/completions", stream: true},
		{name: "explicit_endpoint", baseURL: "https://dedicated.example.test", wantHost: "dedicated.example.test", wantPath: "/v1/chat/completions"},
		{name: "explicit_prefix", baseURL: "https://dedicated.example.test", pathPrefix: testStrPtr("/custom"), wantHost: "dedicated.example.test", wantPath: "/custom/chat/completions"},
		{name: "explicit_empty_prefix", baseURL: "https://dedicated.example.test", pathPrefix: testStrPtr(""), wantHost: "dedicated.example.test", wantPath: "/chat/completions"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			mock := startMockOpenAI(t)
			defer mock.Close()
			upstream := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Host != tc.wantHost {
					t.Errorf("upstream host = %q, want %q", r.Host, tc.wantHost)
					http.Error(w, "unexpected upstream host", http.StatusGone)
					return
				}
				if r.Method != http.MethodPost || r.URL.Path != tc.wantPath {
					t.Errorf("upstream request = %s %s, want POST %s", r.Method, r.URL.Path, tc.wantPath)
					http.Error(w, "unexpected upstream path", http.StatusNotFound)
					return
				}
				if r.Header.Get("Authorization") != "Bearer synthetic-provider-credential" {
					t.Error("provider bearer credential was not forwarded")
				}
				if r.Header.Get("Content-Type") != "application/json" {
					t.Error("upstream request is not JSON")
				}
				body, err := io.ReadAll(r.Body)
				if err != nil {
					t.Errorf("read upstream request: %v", err)
					w.WriteHeader(http.StatusBadRequest)
					return
				}
				var req models.ChatCompletionRequest
				if err := json.Unmarshal(body, &req); err != nil {
					t.Errorf("decode upstream request: %v", err)
					w.WriteHeader(http.StatusBadRequest)
					return
				}
				if req.Model != model || req.Stream != tc.stream {
					t.Errorf("upstream model/stream = %q/%v, want %q/%v", req.Model, req.Stream, model, tc.stream)
				}
				if len(req.Messages) != 1 || string(req.Messages[0].Content) != `"Hello"` {
					t.Error("user message was not forwarded intact")
				}
				// Reuse the existing mock's chat response after checking the real wire path.
				r.Body = io.NopCloser(bytes.NewReader(body))
				r.URL.Path = "/v1/chat/completions"
				mock.Config.Handler.ServeHTTP(w, r)
			}))
			defer upstream.Close()

			cfg := config.DefaultConfig()
			provider := config.ProviderConfig{
				Type:          "huggingface",
				BaseURL:       tc.baseURL,
				APIPathPrefix: tc.pathPrefix,
				APIKey:        "synthetic-provider-credential",
				Models:        []string{model},
			}
			if tc.fromFile {
				// Only type and api_key: base_url and api_format must come from the preset.
				path := filepath.Join(t.TempDir(), "config.yaml")
				file := "providers:\n  huggingface:\n    type: huggingface\n    api_key: synthetic-provider-credential\n"
				if err := os.WriteFile(path, []byte(file), 0600); err != nil {
					t.Fatal(err)
				}
				loaded, err := config.Load(path)
				if err != nil {
					t.Fatalf("load type-only config: %v", err)
				}
				cfg = loaded
				provider = cfg.Providers["huggingface"]
			}
			provider.SkipTLS = true // Only the in-process test server's self-signed certificate.
			// Keep the preset URL intact; intercept only the network connection.
			provider.DialContext = func(ctx context.Context, network, addr string) (net.Conn, error) {
				return (&net.Dialer{}).DialContext(ctx, network, upstream.Listener.Addr().String())
			}
			cfg.Providers["huggingface"] = provider
			registry, err := providers.NewRegistry(cfg)
			if err != nil {
				t.Fatalf("create registry: %v", err)
			}
			defer registry.Close()
			srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
			srv.ready.Store(true)
			gateway := httptest.NewServer(srv.httpServer.Handler)
			defer gateway.Close()

			body := fmt.Sprintf(`{"model":%q,"messages":[{"role":"user","content":"Hello"}],"stream":%t}`, model, tc.stream)
			client := gateway.Client()
			client.Timeout = 5 * time.Second
			resp, err := client.Post(gateway.URL+"/v1/chat/completions", "application/json", strings.NewReader(body))
			if err != nil {
				t.Fatalf("call gateway: %v", err)
			}
			defer resp.Body.Close()
			if resp.StatusCode != http.StatusOK {
				t.Fatalf("gateway status = %d, want 200", resp.StatusCode)
			}
			if !tc.stream {
				var completion models.ChatCompletionResponse
				if err := json.NewDecoder(resp.Body).Decode(&completion); err != nil {
					t.Fatalf("decode completion: %v", err)
				}
				if completion.Model != model || len(completion.Choices) != 1 || string(completion.Choices[0].Message.Content) != `"Hello! How can I help you today?"` {
					t.Fatal("gateway did not return the upstream completion intact")
				}
				return
			}
			if !strings.HasPrefix(resp.Header.Get("Content-Type"), "text/event-stream") {
				t.Fatal("gateway did not return an SSE stream")
			}
			stream, err := io.ReadAll(resp.Body)
			if err != nil {
				t.Fatalf("read stream: %v", err)
			}
			var content strings.Builder
			for _, line := range strings.Split(string(stream), "\n") {
				if !strings.HasPrefix(line, "data: ") || line == "data: [DONE]" {
					continue
				}
				var chunk models.StreamChunk
				if err := json.Unmarshal([]byte(strings.TrimPrefix(line, "data: ")), &chunk); err != nil {
					t.Fatalf("decode chunk: %v", err)
				}
				if chunk.Model != model {
					t.Errorf("stream model = %q, want %q", chunk.Model, model)
				}
				for _, choice := range chunk.Choices {
					if choice.Delta.Content != nil {
						content.WriteString(*choice.Delta.Content)
					}
				}
			}
			if content.String() != "Hello! How can I help you?" || !strings.HasSuffix(string(stream), "data: [DONE]\n\n") {
				t.Fatal("gateway did not return the complete upstream stream")
			}
		})
	}
}
