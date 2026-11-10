package providers

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"gopkg.in/yaml.v3"
)

func TestGreenPTExampleConfiguration(t *testing.T) {
	content, err := os.ReadFile("../../config.example.yaml")
	if err != nil {
		t.Fatal(err)
	}
	_, after, ok := strings.Cut(string(content), "  # greenpt:\n")
	if !ok {
		t.Fatal("GreenPT example configuration is missing")
	}
	block, _, _ := strings.Cut(after, "\n\n")
	lines := strings.Split(block, "\n")
	for i := range lines {
		lines[i] = strings.TrimPrefix(lines[i], "  # ")
	}
	example := "greenpt:\n" + strings.Join(lines, "\n")
	var providers map[string]config.ProviderConfig
	if err := yaml.Unmarshal([]byte(example), &providers); err != nil {
		t.Fatalf("uncommented GreenPT example is invalid YAML: %v", err)
	}
	cfg := providers["greenpt"]
	if cfg.Type != "greenpt" || cfg.AutoDiscover == nil || !*cfg.AutoDiscover || len(cfg.Models) != 0 {
		t.Fatal("GreenPT example must enable discovery without an active model allow-list")
	}
}

func TestGreenPTRegistryDiscoveryAndInference(t *testing.T) {
	var discovered atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer synthetic-greenpt-key" {
			http.Error(w, "authentication required", http.StatusUnauthorized)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		switch r.Method + " " + r.URL.Path {
		case "GET /v1/models":
			discovered.Add(1)
			io.WriteString(w, `{"data":[{"id":"synthetic-chat"},{"id":"synthetic-embedding"}]}`)
		case "POST /v1/chat/completions":
			var request models.ChatCompletionRequest
			if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
				t.Error(err)
				w.WriteHeader(http.StatusBadRequest)
				return
			}
			if request.Model != "synthetic-chat" {
				t.Errorf("chat model = %q", request.Model)
			}
			io.WriteString(w, `{"id":"synthetic-chat-id","model":"synthetic-chat","choices":[{"index":0,"message":{"role":"assistant","content":"synthetic answer"},"finish_reason":"stop"}]}`)
		case "POST /v1/embeddings":
			var request models.EmbeddingRequest
			if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
				t.Error(err)
				w.WriteHeader(http.StatusBadRequest)
				return
			}
			if request.Model != "synthetic-embedding" || string(request.Input) != `"synthetic input"` {
				t.Error("embedding request did not preserve the model and input")
			}
			io.WriteString(w, `{"object":"list","model":"synthetic-embedding","data":[{"object":"embedding","index":0,"embedding":[0.25,0.75]}],"usage":{"prompt_tokens":2,"total_tokens":2}}`)
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()

	registry, err := NewRegistry(&config.Config{Providers: map[string]config.ProviderConfig{
		"greenpt": {Type: "greenpt", BaseURL: server.URL, APIKey: "synthetic-greenpt-key"},
	}})
	if err != nil {
		t.Fatal(err)
	}
	defer registry.Close()
	if discovered.Load() != 1 {
		t.Fatalf("authenticated discovery calls = %d, want 1", discovered.Load())
	}
	provider, err := registry.Resolve("synthetic-chat")
	if err != nil {
		t.Fatal(err)
	}
	chat, err := provider.ChatCompletion(context.Background(), &models.ChatCompletionRequest{
		Model:    "synthetic-chat",
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"synthetic input"`)}},
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(chat.Choices) != 1 || string(chat.Choices[0].Message.Content) != `"synthetic answer"` {
		t.Fatalf("chat response = %+v", chat)
	}
	embeddingProvider, err := registry.Resolve("synthetic-embedding")
	if err != nil {
		t.Fatal(err)
	}
	embeddings, ok := embeddingProvider.(EmbeddingProvider)
	if !ok {
		t.Fatal("GreenPT does not implement the existing embeddings contract")
	}
	result, err := embeddings.CreateEmbedding(context.Background(), &models.EmbeddingRequest{
		Model: "synthetic-embedding", Input: json.RawMessage(`"synthetic input"`),
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Data) != 1 || string(result.Data[0].Embedding) != "[0.25,0.75]" {
		t.Fatalf("embedding response = %+v", result)
	}
}

func TestAuthenticatedDiscoveryPathPrefixes(t *testing.T) {
	for _, prefix := range []string{"/v1", "", "/custom/v2"} {
		t.Run("prefix="+prefix, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodGet || r.URL.Path != prefix+"/models" {
					http.NotFound(w, r)
					return
				}
				if r.Header.Get("Authorization") != "Bearer synthetic-key" {
					http.Error(w, "authentication required", http.StatusUnauthorized)
					return
				}
				io.WriteString(w, `{"data":[{"id":"synthetic-model"}]}`)
			}))
			defer server.Close()
			for _, base := range []string{server.URL, server.URL + prefix + "/"} {
				cfg := config.ProviderConfig{Type: "greenpt", BaseURL: base, APIKey: "synthetic-key", APIPathPrefix: &prefix}
				applyProviderPreset(&cfg)
				got := autoDiscoverModels("greenpt", &cfg)
				if !reflect.DeepEqual(got, []string{"synthetic-model"}) {
					t.Errorf("models = %v, want [synthetic-model]", got)
				}
			}
		})
	}
}

func TestAuthenticatedDiscoveryRejectsRedirects(t *testing.T) {
	var redirected atomic.Int32
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		redirected.Add(1)
		io.WriteString(w, `{"data":[{"id":"redirected-model"}]}`)
	}))
	defer target.Close()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target.URL+"/models", http.StatusFound)
	}))
	defer server.Close()
	cfg := config.ProviderConfig{BaseURL: server.URL, APIKey: "synthetic-key", APIFormat: "openai"}
	if got := autoDiscoverModels("greenpt", &cfg); got != nil {
		t.Errorf("models from redirected discovery = %v, want none", got)
	}
	if redirected.Load() != 0 {
		t.Errorf("authenticated discovery followed %d redirects", redirected.Load())
	}
}
