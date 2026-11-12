package providers

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func loadRegistryPreset(t *testing.T, body string) *config.Config {
	t.Helper()
	path := filepath.Join(t.TempDir(), "config.yaml")
	if err := os.WriteFile(path, []byte(body), 0600); err != nil {
		t.Fatal(err)
	}
	cfg, err := config.Load(path)
	if err != nil {
		t.Fatalf("config.Load before NewRegistry: %v", err)
	}
	return cfg
}

// registryDefaults is a loaded provider entry as NewRegistry keeps it: the
// entry, presets already resolved by config.Load, plus the defaults the
// registry fills in for what it leaves unset (local-provider defaults, then
// the upstream concurrency defaults and queue wait of PROVIDER-CONCURRENCY-1).
// Every other field must be exactly the loaded value.
func registryDefaults(cfg *config.Config, entry config.ProviderConfig) config.ProviderConfig {
	applyLocalDefaults(&entry)
	applyConcurrencyDefaults(&entry, cfg.ProviderDefaults.QueueTimeout)
	return entry
}

func TestProviderPresetLoadThenRegistry(t *testing.T) {
	for kind, preset := range KnownProviders {
		t.Run(kind, func(t *testing.T) {
			body := fmt.Sprintf("providers:\n  alias:\n    type: %s\n    api_key: synthetic-operator-credential\n    local: false\n    auto_discover: false\n    models: [synthetic-model]\n", kind)
			wantURL := preset.BaseURL
			if kind == "azure" {
				wantURL = "https://resource.invalid"
				body += "    base_url: " + wantURL + "\n"
			}
			cfg := loadRegistryPreset(t, body)
			r, err := NewRegistry(cfg)
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = r.Close() })
			p, ok := r.GetProvider("alias")
			if !ok || p == nil {
				t.Fatal("loaded preset provider is not registered")
			}
			resolved := r.configs["alias"]
			if resolved.BaseURL != wantURL || resolved.APIFormat != preset.APIFormat {
				t.Errorf("registry uses (%q, %q), want (%q, %q)", resolved.BaseURL, resolved.APIFormat, wantURL, preset.APIFormat)
			}
			if !reflect.DeepEqual(registryDefaults(cfg, cfg.Providers["alias"]), resolved) {
				t.Error("registry and loaded config disagree on preset resolution")
			}
			// A tenant that inherits this endpoint still supplies its own key
			// and may override even an explicitly empty path prefix.
			for _, prefix := range []string{"", "/org/version"} {
				org := resolveOrgConfig(resolved, "synthetic-org-credential", &tenant.ProviderConfig{APIPathPrefix: &prefix})
				if org.APIKey != "synthetic-org-credential" || org.BaseURL != wantURL || org.APIFormat != preset.APIFormat || org.EffectiveAPIPathPrefix() != prefix {
					t.Error("preset resolution changed tenant inheritance or overrides")
				}
			}
		})
	}
}

// Old, fully explicit configurations must build the same runtime instances.
// No requests or discovery are made to any preset's real upstream.
func TestProviderPresetExplicitRegistryUnchanged(t *testing.T) {
	for kind := range KnownProviders {
		for _, prefix := range []string{"", "/custom/version"} {
			t.Run(kind+"/prefix="+prefix, func(t *testing.T) {
				body := fmt.Sprintf("providers:\n  alias:\n    type: %s\n    base_url: https://custom.invalid/proxy\n    api_format: cohere\n    api_path_prefix: %q\n    api_key: synthetic-operator-credential\n    local: false\n    auto_discover: false\n", kind, prefix)
				cfg := loadRegistryPreset(t, body)
				before := cfg.Providers["alias"]
				r, err := NewRegistry(cfg)
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { _ = r.Close() })
				if before.BaseURL != "https://custom.invalid/proxy" || before.APIFormat != "cohere" || before.APIPathPrefix == nil || *before.APIPathPrefix != prefix {
					t.Fatal("loading changed explicit values")
				}
				if !reflect.DeepEqual(registryDefaults(cfg, before), r.configs["alias"]) {
					t.Error("registry changed explicit configuration")
				}
			})
		}
	}
}

func TestProviderPresetTenantOwnUpstreamUnchanged(t *testing.T) {
	stubLookupIP(t, map[string][]string{"localhost": {"203.0.113.7"}})
	allowLoopbackDial(t)
	for kind := range KnownProviders {
		t.Run(kind, func(t *testing.T) {
			// The operator starts with type + key (Azure additionally needs a
			// resource URL). Nothing in this test dials the operator's upstream.
			body := fmt.Sprintf("providers:\n  alias:\n    type: %s\n    api_key: synthetic-operator-credential\n", kind)
			if kind == "azure" {
				body += "    base_url: https://resource.invalid\n"
			}
			cfg := loadRegistryPreset(t, body)
			before := cfg.Providers["alias"]
			cache := NewOrgProviderCache(cfg.Providers, false)
			t.Cleanup(cache.EvictAll)
			for index, prefix := range []string{"", "/org/version"} {
				t.Run(fmt.Sprintf("org-%d", index), func(t *testing.T) {
					upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						if r.URL.Path != prefix+"/chat/completions" {
							t.Errorf("org request path %q, want %q", r.URL.Path, prefix+"/chat/completions")
						}
						if r.Header.Get("Authorization") != "Bearer synthetic-org-credential" {
							t.Error("org request did not use the org's own credential")
						}
						w.Header().Set("Content-Type", "application/json")
						fmt.Fprint(w, `{"id":"synthetic","object":"chat.completion","model":"synthetic-model","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}`)
					}))
					defer upstream.Close()
					_, port, err := net.SplitHostPort(strings.TrimPrefix(upstream.URL, "http://"))
					if err != nil {
						t.Fatal(err)
					}
					p, err := cache.GetOrCreateWithTenantConfig(fmt.Sprintf("org-%d", index), "alias", "synthetic-org-credential", &tenant.ProviderConfig{
						BaseURL: "http://localhost:" + port, APIFormat: "openai", APIPathPrefix: &prefix,
						Models: []string{"synthetic-model"}, Enabled: true,
					})
					if err != nil {
						t.Fatal(err)
					}
					ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
					defer cancel()
					if _, err := p.ChatCompletion(ctx, &models.ChatCompletionRequest{
						Model: "synthetic-model", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}},
					}); err != nil {
						t.Fatalf("org request: %v", err)
					}
				})
			}
			if !reflect.DeepEqual(before, cfg.Providers["alias"]) {
				t.Error("org override mutated the shared provider defaults")
			}
		})
	}
}
