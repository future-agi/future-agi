package server

import (
	"context"
	"encoding/json"
	"math"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The org's own instance sits at an IP literal, which passes the base URL
// check without a lookup; its path names the Vertex AI host and location, which
// is all the gateway reads. Nothing is sent to it.
const orgVertexBaseURL = "https://8.8.8.8/europe-west4-aiplatform.googleapis.com/v1/projects/p/locations/europe-west4"

// vertexInstances builds config instances and one org instance, none of which
// is called, and returns them by id with the server that holds them.
func vertexInstances(t *testing.T) (*Server, map[string]providers.Provider) {
	t.Helper()
	cfg := config.DefaultConfig()
	// Model discovery is off, so no base URL is called for a model list.
	off := false
	cfg.Providers["vertex"] = config.ProviderConfig{BaseURL: "https://us-central1-aiplatform.googleapis.com/v1/projects/p/locations/us-central1", APIFormat: "gemini", AutoDiscover: &off}
	cfg.Providers["vertex_global"] = config.ProviderConfig{
		BaseURL:      "https://aiplatform.googleapis.com/v1beta1/projects/p/locations/global",
		APIFormat:    "gemini",
		Headers:      map[string]string{"x-gcp-location": "us-central1"},
		AutoDiscover: &off,
	}
	cfg.Providers["google"] = config.ProviderConfig{BaseURL: "https://generativelanguage.googleapis.com", APIFormat: "gemini", AutoDiscover: &off}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: "https://api.openai.com/v1", APIFormat: "openai", AutoDiscover: &off}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), tenant.NewStore(), nil)

	instances := map[string]providers.Provider{}
	for _, id := range []string{"vertex", "vertex_global", "google", "openai"} {
		p, ok := registry.GetProvider(id)
		if !ok {
			t.Fatalf("provider %s is not in the registry", id)
		}
		instances[id] = p
	}
	orgCfg := &tenant.ProviderConfig{APIKey: "org-key", Enabled: true, BaseURL: orgVertexBaseURL, APIFormat: "gemini"}
	orgProvider, err := srv.OrgProviderCache.GetOrCreateWithTenantConfig("org-1", "team-vertex", orgCfg.APIKey, orgCfg)
	if err != nil {
		t.Fatalf("creating org provider: %v", err)
	}
	instances["team-vertex"] = orgProvider
	return srv, instances
}

func TestServedVertexLocationIsReadFromTheInstance(t *testing.T) {
	_, instances := vertexInstances(t)

	for _, tc := range []struct{ name, want string }{
		{"vertex", "us-central1"},
		{"vertex_global", "global"},
		{"team-vertex", "europe-west4"},
		{"google", ""},
		{"openai", ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rc := &models.RequestContext{Metadata: map[string]string{servedVertexLocationKey: "stale"}}
			noteServedVertexLocation(rc, instances[tc.name])
			got, set := rc.Metadata[servedVertexLocationKey]
			if tc.want == "" {
				if set {
					t.Errorf("%s = %q, want it deleted", servedVertexLocationKey, got)
				}
				return
			}
			if got != tc.want {
				t.Errorf("%s = %q, want %q", servedVertexLocationKey, got, tc.want)
			}
		})
	}

	t.Run("nil provider", func(t *testing.T) {
		rc := &models.RequestContext{Metadata: map[string]string{servedVertexLocationKey: "stale"}}
		noteServedVertexLocation(rc, nil)
		if got, set := rc.Metadata[servedVertexLocationKey]; set {
			t.Errorf("%s = %q, want it deleted", servedVertexLocationKey, got)
		}
	})
}

// A location header the URL does not carry is no location. The provider named
// vertex_global sends its requests to the global endpoint, whatever its header
// says, so it is not billed the regional uplift.
func TestLeftoverLocationHeaderAddsNoUplift(t *testing.T) {
	srv, instances := vertexInstances(t)
	db := testModelDBPtr()
	cost := costplugin.New(true, db.Load, nil, nil)
	cost.SetPriceTables(srv.PriceTableName)
	cost.SetCachedTokensInPrompt(srv.CachedTokensInPrompt)

	for _, tc := range []struct {
		name, org, provider, want, location string
	}{
		{"vertex_global", "", "vertex_global", "2.240000", ""},
		{"vertex", "", "vertex", "2.464000", "us-central1"},
		{"team-vertex", "org-1", "team-vertex", "2.464000", "europe-west4"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			details := json.RawMessage(`{"cached_tokens":400000}`)
			rc := &models.RequestContext{
				EndpointType: "chat",
				Model:        "gemini-4-argon",
				Provider:     tc.provider,
				Response: &models.ChatCompletionResponse{Usage: &models.Usage{
					PromptTokens:        1_000_000,
					CompletionTokens:    100_000,
					TotalTokens:         1_100_000,
					PromptTokensDetails: &details,
				}},
				Metadata: map[string]string{},
			}
			if tc.org != "" {
				rc.Metadata[tenant.MetadataKeyOrgID] = tc.org
			}
			noteServedVertexLocation(rc, instances[tc.name])
			if res := cost.ProcessResponse(context.Background(), rc); res.Error != nil {
				t.Fatalf("cost step failed: %v", res.Error)
			}
			if got := rc.Metadata["cost"]; got != tc.want {
				t.Errorf("cost = %q, want %q", got, tc.want)
			}
			if got := rc.Metadata["cost_vertex_location"]; got != tc.location {
				t.Errorf("cost_vertex_location = %q, want %q", got, tc.location)
			}
			wantFactor := ""
			if tc.location != "" {
				wantFactor = "1.1"
			}
			if got := rc.Metadata["cost_region_uplift"]; got != wantFactor {
				t.Errorf("cost_region_uplift = %q, want %q", got, wantFactor)
			}
		})
	}
}

const vertexServedUsage = `{"promptTokenCount":1000,"cachedContentTokenCount":600,"candidatesTokenCount":200,"totalTokenCount":1200}`

// vertexUpstream answers a generateContent call and a streamGenerateContent
// call with the same usage, whatever the path in front of the action.
func vertexUpstream(t *testing.T) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, ":generateContent"):
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"candidates":[{"content":{"role":"model","parts":[{"text":"hi"}]},"finishReason":"STOP"}],"usageMetadata":` + vertexServedUsage + `}`))
		case strings.HasSuffix(r.URL.Path, ":streamGenerateContent"):
			w.Header().Set("Content-Type", "text/event-stream")
			_, _ = w.Write([]byte("data: {\"candidates\":[{\"content\":{\"role\":\"model\",\"parts\":[{\"text\":\"hi\"}]}}]}\n\n"))
			_, _ = w.Write([]byte("data: {\"candidates\":[{\"content\":{\"role\":\"model\",\"parts\":[{\"text\":\"!\"}]},\"finishReason\":\"STOP\"}],\"usageMetadata\":" + vertexServedUsage + "}\n\n"))
		default:
			http.Error(w, "unexpected path "+r.URL.Path, http.StatusNotFound)
		}
	}))
}

// vertexChainServer serves the Vertex AI provider "vertex" from upstream, at
// the location in its base URL, with the cost plugin wired as main wires it.
// When orgInstance is set, org-1 also has a cached regional instance with the
// same id, for a different model.
func vertexChainServer(t *testing.T, upstreamURL, location string, orgInstance bool) *Server {
	t.Helper()
	db := testModelDBPtr()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	key := config.AuthKeyConfig{Name: "vertex-location", Key: "sk-agentcc-vertex-location", Owner: "test-org", KeyType: "internal"}
	if orgInstance {
		key.Metadata = map[string]string{"org_id": "org-1"}
	}
	cfg.Auth.Keys = []config.AuthKeyConfig{key}
	cfg.Providers["vertex"] = config.ProviderConfig{
		BaseURL:   upstreamURL + "/aiplatform.googleapis.com/v1/projects/p/locations/" + location,
		APIKey:    "config-vertex-key",
		APIFormat: "gemini",
		Models:    []string{"gemini-4-argon"},
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}

	var store *tenant.Store
	var orgProvider *tenant.ProviderConfig
	if orgInstance {
		orgProvider = &tenant.ProviderConfig{
			Enabled:   true,
			APIKey:    "org-vertex-key",
			APIFormat: "gemini",
			BaseURL:   orgVertexBaseURL,
			Models:    []string{"gemini-other"},
		}
		store = tenant.NewStore()
		store.Set("org-1", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{"vertex": orgProvider}})
	}

	cost := costplugin.New(true, db.Load, nil, nil)
	srv := New(cfg, "", registry, pipeline.NewEngine(cost), nil, nil, nil, nil, db, store, nil)
	cost.SetPriceTables(srv.PriceTableName)
	cost.SetCachedTokensInPrompt(srv.CachedTokensInPrompt)
	srv.ready.Store(true)

	if orgInstance {
		// The org's instance is cached, as an earlier request of its own
		// would have left it.
		if _, err := srv.OrgProviderCache.GetOrCreateWithTenantConfig("org-1", "vertex", orgProvider.APIKey, orgProvider); err != nil {
			t.Fatalf("warming the org provider cache: %v", err)
		}
	}
	return srv
}

func postVertexChat(srv *Server, body string, headers map[string]string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer sk-agentcc-vertex-location")
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// The uplift follows the instance that served the request, not whichever
// instance carries the same id in the org's cache.
func TestVertexUpliftFollowsTheInstanceThatServed(t *testing.T) {
	upstream := vertexUpstream(t)
	defer upstream.Close()

	const body = `{"model":"gemini-4-argon","messages":[{"role":"user","content":"hi"}]}`

	t.Run("internal-key-config-global-org-regional-cached", func(t *testing.T) {
		srv := vertexChainServer(t, upstream.URL, "global", true)
		w := postVertexChat(srv, body, nil)
		if w.Code != http.StatusOK {
			t.Fatalf("status %d: %s", w.Code, w.Body.String())
		}
		got, err := strconv.ParseFloat(strings.TrimSpace(w.Header().Get("x-agentcc-cost")), 64)
		if err != nil || math.Abs(got-0.00286) > 5e-7 {
			t.Fatalf("x-agentcc-cost = %q, want 0.002860 (no uplift: the global instance served)", w.Header().Get("x-agentcc-cost"))
		}
	})

	t.Run("config-regional", func(t *testing.T) {
		srv := vertexChainServer(t, upstream.URL, "us-central1", false)
		w := postVertexChat(srv, body, nil)
		if w.Code != http.StatusOK {
			t.Fatalf("status %d: %s", w.Code, w.Body.String())
		}
		got, err := strconv.ParseFloat(strings.TrimSpace(w.Header().Get("x-agentcc-cost")), 64)
		if err != nil || math.Abs(got-0.003146) > 5e-7 {
			t.Fatalf("x-agentcc-cost = %q, want 0.003146", w.Header().Get("x-agentcc-cost"))
		}
	})

	t.Run("config-regional-stream", func(t *testing.T) {
		srv := vertexChainServer(t, upstream.URL, "us-central1", false)
		w := postVertexChat(srv, `{"model":"gemini-4-argon","messages":[{"role":"user","content":"hi"}],"stream":true}`,
			map[string]string{"x-agentcc-include-metadata": "1"})
		if w.Code != http.StatusOK {
			t.Fatalf("status %d: %s", w.Code, w.Body.String())
		}

		var dataLines []string
		for _, line := range strings.Split(w.Body.String(), "\n") {
			if strings.HasPrefix(line, "data: ") {
				dataLines = append(dataLines, strings.TrimPrefix(line, "data: "))
			}
		}
		if len(dataLines) < 3 || dataLines[len(dataLines)-1] != "[DONE]" {
			t.Fatalf("unexpected stream: %s", w.Body.String())
		}
		var final models.StreamChunk
		if err := json.Unmarshal([]byte(dataLines[len(dataLines)-2]), &final); err != nil {
			t.Fatalf("parsing the final metadata chunk: %v", err)
		}
		if final.AgentccMetadata == nil {
			t.Fatalf("no agentcc metadata in the final chunk: %s", dataLines[len(dataLines)-2])
		}
		if got := final.AgentccMetadata.Cost; math.Abs(got-0.003146) > 5e-7 || math.Abs(got-0.00286) <= 5e-7 {
			t.Fatalf("streamed cost = %v, want 0.003146 (not the un-uplifted 0.00286)", got)
		}
	})
}
