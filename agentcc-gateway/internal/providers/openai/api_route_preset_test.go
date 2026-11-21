package openai

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// API Route is an opt-in name on the existing OpenAI-compatible provider.
// Its public base URL is https://global.api-route.com/v1
// (https://www.api-route.com/integrations). The chat request must carry that
// version prefix once: https://global.api-route.com/v1/chat/completions, and
// never /v1/v1.
func TestAPIRoutePresetConstructsChatRequestWithVersionPrefixOnce(t *testing.T) {
	const (
		wantHost = "global.api-route.com"
		wantPath = "/v1/chat/completions"
		wantURL  = "https://" + wantHost + wantPath
		model    = "example-model"
		apiKey   = "synthetic-provider-credential"
	)
	v1 := "/v1"

	for _, tc := range []struct {
		name     string
		fromFile bool
		cfg      config.ProviderConfig
	}{
		{name: "type-only preset", fromFile: true},
		{
			// The dialog stores the published SDK base, which already ends in
			// /v1, and the default path prefix. Joining them must keep one /v1.
			name: "base already carries /v1",
			cfg: config.ProviderConfig{
				Type:          "api-route",
				BaseURL:       "https://global.api-route.com/v1",
				APIFormat:     "openai",
				APIPathPrefix: &v1,
				APIKey:        apiKey,
				Models:        []string{model},
			},
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			cfg := tc.cfg
			if tc.fromFile {
				path := filepath.Join(t.TempDir(), "config.yaml")
				file := "providers:\n  api-route:\n    type: api-route\n    api_key: " + apiKey + "\n    models: [" + model + "]\n"
				if err := os.WriteFile(path, []byte(file), 0o600); err != nil {
					t.Fatal(err)
				}
				loaded, err := config.Load(path)
				if err != nil {
					t.Fatalf("load type-only config: %v", err)
				}
				cfg = loaded.Providers["api-route"]
				if cfg.APIFormat != "openai" {
					t.Fatalf("APIFormat = %q, want openai", cfg.APIFormat)
				}
				if cfg.BaseURL != "https://global.api-route.com" {
					t.Fatalf("BaseURL = %q, want the preset host without a version segment", cfg.BaseURL)
				}
			} else {
				config.ApplyProviderPreset(&cfg)
				if cfg.BaseURL != "https://global.api-route.com/v1" {
					t.Fatalf("explicit BaseURL = %q, want it left unchanged", cfg.BaseURL)
				}
			}

			gotURL := cfg.EndpointURL("/v1/chat/completions")
			if gotURL != wantURL || strings.Contains(gotURL, "/v1/v1") {
				t.Fatalf("EndpointURL = %q, want %q with /v1 applied once", gotURL, wantURL)
			}

			var gotHost, gotPath string
			upstream := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				gotHost = r.Host
				gotPath = r.URL.RequestURI()
				if r.Method != http.MethodPost {
					t.Errorf("method = %s, want POST", r.Method)
				}
				if strings.Contains(r.URL.Path, "/v1/v1") {
					t.Errorf("version prefix applied twice: %s", r.URL.Path)
				}
				if r.Header.Get("Authorization") != "Bearer "+apiKey {
					t.Error("synthetic credential was not forwarded as a bearer token")
				}
				w.Header().Set("Content-Type", "application/json")
				_ = json.NewEncoder(w).Encode(models.ChatCompletionResponse{
					ID: "chatcmpl-synthetic",
					Choices: []models.Choice{{
						Message: models.Message{Role: "assistant", Content: json.RawMessage(`"ok"`)},
					}},
				})
			}))
			t.Cleanup(upstream.Close)

			cfg.SkipTLS = true // The in-process server's certificate only.
			cfg.DialContext = func(ctx context.Context, network, addr string) (net.Conn, error) {
				if !strings.HasPrefix(addr, wantHost+":") {
					t.Errorf("dial address = %q, want host %s", addr, wantHost)
				}
				return (&net.Dialer{}).DialContext(ctx, network, upstream.Listener.Addr().String())
			}

			provider, err := New("api-route", cfg)
			if err != nil {
				t.Fatalf("New: %v", err)
			}
			t.Cleanup(func() { _ = provider.Close() })
			if provider.ConfiguredType() != "api-route" {
				t.Fatalf("configured type = %q, want api-route", provider.ConfiguredType())
			}
			if provider.openAIAPI {
				t.Fatal("api-route was treated as the official OpenAI API")
			}

			if _, err := provider.ChatCompletion(context.Background(), &models.ChatCompletionRequest{
				Model:    model,
				Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hello"`)}},
			}); err != nil {
				t.Fatalf("ChatCompletion: %v", err)
			}
			if gotHost != wantHost {
				t.Errorf("request host = %q, want %q", gotHost, wantHost)
			}
			if gotPath != wantPath {
				t.Errorf("request path = %q, want %q", gotPath, wantPath)
			}
		})
	}
}
