package providers

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const r4NodeHeader = "r4-synthetic-node-header"
const r4TenantKey = "r4-synthetic-tenant-key"

func r4Settings(mixed bool) map[string]string {
	h := map[string]string{
		"auth-type": "bearer", "api-version": "2024-10-21",
		"x-gcp-project": "r4-project", "x-gcp-location": "europe-west4",
		"x-aws-region": "eu-west-3", "x-cohere-input-type": "search_query",
		"anthropic-version": "2023-06-01", "anthropic-beta": "r4-beta", "user-agent": "r4-client",
		"authorization": r4NodeHeader, "x-api-key": r4NodeHeader, "api-key": r4NodeHeader,
		"x-goog-api-key": r4NodeHeader, "proxy-authorization": r4NodeHeader, "cookie": r4NodeHeader,
		"auth-type-extra": r4NodeHeader, "x-gcp-project-extra": r4NodeHeader,
		"x-aws-region-extra": r4NodeHeader, "x-cohere-input-type-extra": r4NodeHeader,
		"anthropic-beta-extra": r4NodeHeader, "user-agent-extra": r4NodeHeader,
	}
	if !mixed {
		return h
	}
	out := make(map[string]string, len(h))
	for k, v := range h {
		out[http.CanonicalHeaderKey(k)] = v
	}
	return out
}

type r4Wire struct {
	header http.Header
	path   string
	query  url.Values
	body   []byte
}

// These REDs dispatch through the org cache, not merely through resolveOrgConfig.
// Every outbound connection is pinned to the loopback fixture, including Vertex.
func TestOptBR4AdapterSettingsDispatch(t *testing.T) {
	for _, format := range []string{"azure", "gemini", "google", "bedrock", "cohere"} {
		for _, mixed := range []bool{false, true} {
			name := format + "/lower"
			if mixed {
				name = format + "/mixed"
			}
			t.Run(name, func(t *testing.T) {
				wire := make(chan r4Wire, 1)
				upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					body, _ := io.ReadAll(r.Body)
					wire <- r4Wire{r.Header.Clone(), r.URL.Path, r.URL.Query(), body}
					w.WriteHeader(http.StatusBadRequest)
					_, _ = io.WriteString(w, `{"error":{"message":"synthetic refusal"}}`)
				}))
				defer upstream.Close()
				base := config.ProviderConfig{APIFormat: format, BaseURL: upstream.URL, Headers: r4Settings(mixed)}
				if format == "gemini" || format == "google" {
					base.BaseURL = "http://r4.aiplatform.googleapis.com"
					base.DialContext = func(ctx context.Context, network, _ string) (net.Conn, error) {
						return (&net.Dialer{}).DialContext(ctx, network, upstream.Listener.Addr().String())
					}
				}
				tenantCfg := &tenant.ProviderConfig{APIKey: r4TenantKey}
				if format == "bedrock" {
					tenantCfg.AWSAccessKeyID = "r4-tenant-access"
					tenantCfg.AWSSecretAccessKey = "r4-tenant-secret"
				}
				cache := NewOrgProviderCache(map[string]config.ProviderConfig{format: base}, false)
				defer cache.EvictAll()
				p, err := cache.GetOrCreateWithTenantConfig("r4-org", format, r4TenantKey, tenantCfg)
				if err != nil {
					t.Fatal(err)
				}
				cached, err := cache.GetOrCreateWithTenantConfig("r4-org", format, r4TenantKey, tenantCfg)
				if err != nil || cached != p {
					t.Fatal("org cache did not reuse the resolved adapter")
				}
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				if format == "cohere" {
					_, err = p.(EmbeddingProvider).CreateEmbedding(ctx, &models.EmbeddingRequest{Model: "fixture", Input: json.RawMessage(`"fixture"`)})
				} else {
					_, err = p.ChatCompletion(ctx, &models.ChatCompletionRequest{Model: "fixture", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"fixture"`)}}})
				}
				if err == nil {
					t.Fatal("fixture refusal was not returned")
				}
				var sent r4Wire
				select {
				case sent = <-wire:
				default:
					t.Fatal("adapter never dispatched to the loopback fixture")
				}
				switch format {
				case "azure":
					if sent.header.Get("Authorization") != "Bearer "+r4TenantKey || sent.header.Get("api-key") != "" {
						t.Error("Azure auth-type did not reach adapter: tenant bearer auth changed")
					}
					if sent.query.Get("api-version") != "2024-10-21" {
						t.Error("Azure api-version did not reach adapter")
					}
				case "gemini", "google":
					if sent.path != "/v1beta1/projects/r4-project/locations/europe-west4/publishers/google/models/fixture:generateContent" {
						t.Error("Gemini project/location did not reach adapter endpoint construction")
					}
					if sent.header.Get("Authorization") != "Bearer "+r4TenantKey {
						t.Error("Vertex did not use tenant credential")
					}
				case "bedrock":
					if !strings.Contains(sent.header.Get("Authorization"), "/eu-west-3/bedrock/aws4_request") || !strings.Contains(sent.header.Get("Authorization"), "Credential=r4-tenant-access/") {
						t.Error("Bedrock region did not reach tenant SigV4 signing")
					}
				case "cohere":
					var body map[string]any
					if json.Unmarshal(sent.body, &body) != nil || body["input_type"] != "search_query" {
						t.Error("Cohere input-type did not reach embedding translation")
					}
				}
				if format != "bedrock" { // Bedrock consumes region but does not apply wire defaults.
					for name, want := range map[string]string{"anthropic-version": "2023-06-01", "anthropic-beta": "r4-beta", "user-agent": "r4-client"} {
						if sent.header.Get(name) != want {
							t.Errorf("reviewed wire header %s did not reach adapter dispatch", name)
						}
					}
				}
				for _, values := range sent.header {
					for _, value := range values {
						if strings.Contains(value, r4NodeHeader) {
							t.Error("inherited credential or near-match header reached adapter dispatch")
						}
					}
				}
				resolved := resolveOrgConfig(base, r4TenantKey, tenantCfg)
				for name := range resolved.Headers {
					switch strings.ToLower(name) {
					case "authorization", "x-api-key", "api-key", "x-goog-api-key", "proxy-authorization", "cookie":
						t.Error("credential header reached adapter configuration")
					}
				}
				if !reflect.DeepEqual(base.Headers, r4Settings(mixed)) {
					t.Error("org resolution mutated the platform configuration")
				}
			})
		}
	}
}

// Positive controls cover the common reviewed wire headers and exclusion of
// settings belonging to another adapter. No pre-existing assertion is changed.
func TestOptBR4AllowlistIsolation(t *testing.T) {
	settings := map[string][]string{
		"openai": nil, "anthropic": nil, "azure": {"auth-type", "api-version"},
		"gemini": {"x-gcp-project", "x-gcp-location"}, "google": {"x-gcp-project", "x-gcp-location"},
		"bedrock": {"x-aws-region"}, "cohere": {"x-cohere-input-type"},
	}
	for format, specific := range settings {
		t.Run(format, func(t *testing.T) {
			base := config.ProviderConfig{APIFormat: format, Headers: r4Settings(false)}
			got := resolveOrgConfig(base, r4TenantKey, nil)
			want := map[string]string{}
			for _, name := range append([]string{"anthropic-version", "anthropic-beta", "user-agent"}, specific...) {
				want[name] = base.Headers[name]
			}
			if !reflect.DeepEqual(got.Headers, want) {
				t.Error("resolved headers differ from the exact per-adapter allowlist")
			}
			got.Headers["user-agent"] = "changed"
			if base.Headers["user-agent"] != "r4-client" {
				t.Error("resolved headers alias platform headers")
			}
		})
	}
}

func TestOptBR4AmbiguousOrMalformedInheritance(t *testing.T) {
	for name, base := range map[string]config.ProviderConfig{
		"duplicate_setting":  {APIFormat: "azure", BaseURL: "https://fixture.invalid", Headers: map[string]string{"auth-type": "bearer", "Auth-Type": "api-key"}},
		"malformed_userinfo": {APIFormat: "anthropic", BaseURL: "http://r4-node-user:r4-node-secret%zz@fixture.invalid"},
	} {
		t.Run(name, func(t *testing.T) {
			cache := NewOrgProviderCache(map[string]config.ProviderConfig{"fixture": base}, false)
			defer cache.EvictAll()
			_, err := cache.GetOrCreate("r4-org", "fixture", r4TenantKey)
			var configErr *ConfigurationError
			if !errors.As(err, &configErr) {
				t.Fatal("unseparable or ambiguous inherited configuration must fail with a typed error")
			}
			for _, value := range []string{"r4-node-user", "r4-node-secret", "fixture.invalid", "bearer", r4TenantKey} {
				if strings.Contains(err.Error(), value) {
					t.Error("configuration rejection published an inherited value")
				}
			}
			if cache.Count() != 0 {
				t.Error("rejected configuration was cached")
			}
		})
	}
}

func TestOptBR4InheritedProxyUserinfo(t *testing.T) {
	for _, format := range []string{"anthropic", "gemini"} {
		t.Run(format, func(t *testing.T) {
			wire := make(chan http.Header, 1)
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				_, _ = io.Copy(io.Discard, r.Body)
				wire <- r.Header.Clone()
				w.WriteHeader(http.StatusBadRequest)
				_, _ = io.WriteString(w, `{"error":{"message":"synthetic proxy refusal"}}`)
			}))
			defer upstream.Close()
			u, err := url.Parse(upstream.URL + "/proxy")
			if err != nil {
				t.Fatal(err)
			}
			u.User = url.UserPassword("r4-node-proxy-user", "r4-node-proxy-password")
			base := config.ProviderConfig{APIFormat: format, BaseURL: u.String()}
			cache := NewOrgProviderCache(map[string]config.ProviderConfig{format: base}, false)
			defer cache.EvictAll()
			p, err := cache.GetOrCreate("r4-org", format, r4TenantKey)
			if err != nil {
				t.Fatal(err)
			}
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			_, _ = p.ChatCompletion(ctx, &models.ChatCompletionRequest{Model: "fixture", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"fixture"`)}}})
			select {
			case sent := <-wire:
				if sent.Get("Authorization") != "" || sent.Get("Proxy-Authorization") != "" {
					t.Error("inherited URL userinfo authenticated the tenant request as the node proxy user")
				}
				header := "X-Api-Key"
				if format == "gemini" {
					header = "X-Goog-Api-Key"
				}
				if sent.Get(header) != r4TenantKey {
					t.Error("tenant key did not reach proxy")
				}
			default:
				t.Fatal("proxy fixture never received the request")
			}
			got := resolveOrgConfig(base, r4TenantKey, nil)
			if got.BaseURL != upstream.URL+"/proxy" || base.BaseURL != u.String() {
				t.Error("userinfo stripping must preserve the endpoint and not mutate the operator config")
			}
		})
	}
}
