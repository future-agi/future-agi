package server

import (
	"context"
	"encoding/json"
	"math"
	"net/http"
	"net/http/httptest"
	"slices"
	"strconv"
	"strings"
	"sync"
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

// finalStreamChunk is the last chunk before [DONE] of an SSE body, which
// carries the agentcc metadata (cost) when the request asked for it.
func finalStreamChunk(t *testing.T, body string) models.StreamChunk {
	t.Helper()
	var dataLines []string
	for _, line := range strings.Split(body, "\n") {
		if strings.HasPrefix(line, "data: ") {
			dataLines = append(dataLines, strings.TrimPrefix(line, "data: "))
		}
	}
	if len(dataLines) < 3 || dataLines[len(dataLines)-1] != "[DONE]" {
		t.Fatalf("unexpected stream: %s", body)
	}
	var final models.StreamChunk
	if err := json.Unmarshal([]byte(dataLines[len(dataLines)-2]), &final); err != nil {
		t.Fatalf("parsing the final metadata chunk: %v", err)
	}
	if final.AgentccMetadata == nil {
		t.Fatalf("no agentcc metadata in the final chunk: %s", dataLines[len(dataLines)-2])
	}
	return final
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

		final := finalStreamChunk(t, w.Body.String())
		if got := final.AgentccMetadata.Cost; math.Abs(got-0.003146) > 5e-7 || math.Abs(got-0.00286) <= 5e-7 {
			t.Fatalf("streamed cost = %v, want 0.003146 (not the un-uplifted 0.00286)", got)
		}
	})
}

// vertexInstance is one Vertex AI provider of vertexRoutingServer: its id, the
// location in its base URL and the models it lists.
type vertexInstance struct {
	id, location string
	models       []string
}

// vertexRoutingUpstream answers like vertexUpstream, except that it fails with
// a 503 whenever fail(instance id, call number) says so. The instance id is the
// first path segment, which the base URLs of vertexRoutingServer carry. The
// returned function lists the ids of the instances that answered with success.
func vertexRoutingUpstream(t *testing.T, fail func(id string, call int) bool) (*httptest.Server, func() []string) {
	t.Helper()
	var mu sync.Mutex
	calls := 0
	var served []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		streaming := strings.HasSuffix(r.URL.Path, ":streamGenerateContent")
		if !streaming && !strings.HasSuffix(r.URL.Path, ":generateContent") {
			// The gateway's start-up connectivity check is not a request.
			w.WriteHeader(http.StatusOK)
			return
		}
		id := strings.SplitN(strings.TrimPrefix(r.URL.Path, "/"), "/", 2)[0]
		mu.Lock()
		calls++
		failed := fail(id, calls)
		if !failed {
			served = append(served, id)
		}
		mu.Unlock()
		if failed {
			http.Error(w, `{"error":{"code":503,"message":"unavailable","status":"UNAVAILABLE"}}`, http.StatusServiceUnavailable)
			return
		}
		if streaming {
			w.Header().Set("Content-Type", "text/event-stream")
			_, _ = w.Write([]byte("data: {\"candidates\":[{\"content\":{\"role\":\"model\",\"parts\":[{\"text\":\"hi\"}]}}]}\n\n"))
			_, _ = w.Write([]byte("data: {\"candidates\":[{\"content\":{\"role\":\"model\",\"parts\":[{\"text\":\"!\"}]},\"finishReason\":\"STOP\"}],\"usageMetadata\":" + vertexServedUsage + "}\n\n"))
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"candidates":[{"content":{"role":"model","parts":[{"text":"hi"}]},"finishReason":"STOP"}],"usageMetadata":` + vertexServedUsage + `}`))
	}))
	return srv, func() []string {
		mu.Lock()
		defer mu.Unlock()
		return append([]string(nil), served...)
	}
}

// vertexRoutingServer serves the given Vertex AI instances from upstream, each
// at its own location, with the cost plugin wired as main wires it. Failover is
// on for 503; routing sets the targets and the model fallbacks.
func vertexRoutingServer(t *testing.T, upstreamURL string, instances []vertexInstance, routing func(r *config.RoutingConfig)) *Server {
	t.Helper()
	db := testModelDBPtr()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "vertex-location", Key: "sk-agentcc-vertex-location", Owner: "test-org", KeyType: "internal"}}
	off := false
	for _, in := range instances {
		cfg.Providers[in.id] = config.ProviderConfig{
			BaseURL:      upstreamURL + "/" + in.id + "/aiplatform.googleapis.com/v1/projects/p/locations/" + in.location,
			APIKey:       "config-vertex-key",
			APIFormat:    "gemini",
			Models:       in.models,
			AutoDiscover: &off,
		}
	}
	cfg.Routing.DefaultStrategy = "round-robin"
	cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 2, OnStatusCodes: []int{http.StatusBadGateway, http.StatusServiceUnavailable}}
	routing(&cfg.Routing)
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	cost := costplugin.New(true, db.Load, nil, nil)
	srv := New(cfg, "", registry, pipeline.NewEngine(cost), nil, nil, nil, nil, db, tenant.NewStore(), nil)
	cost.SetPriceTables(srv.PriceTableName)
	cost.SetCachedTokensInPrompt(srv.CachedTokensInPrompt)
	srv.ready.Store(true)
	return srv
}

// The uplift follows the instance that served after a model fallback, after a
// provider failover and after both, whichever locations the instances that
// failed were at.
func TestVertexUpliftFollowsTheFallbackAndFailoverThatServed(t *testing.T) {
	const (
		primaryModel  = "gemini-4-argon"
		fallbackModel = "gemini-4-argon-cyber-permissive"
		body          = `{"model":"gemini-4-argon","messages":[{"role":"user","content":"hi"}]}`
		// What vertexServedUsage costs at each model's price, without and with
		// the regional uplift. The fallback model's price is twice the primary's.
		primaryFlat, primaryUplifted   = 0.002860, 0.003146
		fallbackFlat, fallbackUplifted = 0.005720, 0.006292
	)
	primary := func(id, location string) vertexInstance {
		return vertexInstance{id: id, location: location, models: []string{primaryModel}}
	}
	fallback := func(id, location string) vertexInstance {
		return vertexInstance{id: id, location: location, models: []string{fallbackModel}}
	}
	fallbackChain := func(r *config.RoutingConfig) {
		r.ModelFallbacks = map[string][]string{primaryModel: {fallbackModel}}
	}
	targetsAB := func(r *config.RoutingConfig) {
		fallbackChain(r)
		r.Targets = map[string][]config.RoutingTargetConfig{primaryModel: {{Provider: "a", Weight: 1}, {Provider: "b", Weight: 1}}}
	}
	failIDs := func(ids ...string) func(string, int) bool {
		return func(id string, _ int) bool { return slices.Contains(ids, id) }
	}
	// The first call fails, whichever instance takes it, so the instance that
	// serves is the one failover moves to.
	firstCallFails := func(_ string, call int) bool { return call == 1 }

	for _, tc := range []struct {
		name      string
		instances []vertexInstance
		routing   func(r *config.RoutingConfig)
		fail      func(id string, call int) bool
		want      float64
	}{
		{"model fallback: regional primary fails, global fallback serves",
			[]vertexInstance{primary("primary", "us-central1"), fallback("fb", "global")}, fallbackChain, failIDs("primary"), fallbackFlat},
		{"model fallback: global primary fails, regional fallback serves",
			[]vertexInstance{primary("primary", "global"), fallback("fb", "us-central1")}, fallbackChain, failIDs("primary"), fallbackUplifted},
		{"failover: regional target a, global target b",
			[]vertexInstance{primary("a", "us-central1"), primary("b", "global")}, targetsAB, firstCallFails, 0},
		{"failover: global target a, regional target b",
			[]vertexInstance{primary("a", "global"), primary("b", "us-central1")}, targetsAB, firstCallFails, 0},
		{"failover then model fallback: regional targets fail, global fallback serves",
			[]vertexInstance{primary("a", "us-central1"), primary("b", "us-central1"), fallback("fb", "global")}, targetsAB, failIDs("a", "b"), fallbackFlat},
		{"failover then model fallback: global targets fail, regional fallback serves",
			[]vertexInstance{primary("a", "global"), primary("b", "global"), fallback("fb", "us-central1")}, targetsAB, failIDs("a", "b"), fallbackUplifted},
	} {
		t.Run(tc.name, func(t *testing.T) {
			upstream, served := vertexRoutingUpstream(t, tc.fail)
			defer upstream.Close()
			srv := vertexRoutingServer(t, upstream.URL, tc.instances, tc.routing)
			w := postVertexChat(srv, body, nil)
			if w.Code != http.StatusOK {
				t.Fatalf("status %d: %s", w.Code, w.Body.String())
			}
			ids := served()
			if len(ids) != 1 {
				t.Fatalf("instances that served = %v, want exactly one", ids)
			}
			want := tc.want
			if want == 0 {
				// A failover between the two primary instances: the one that
				// serves decides, so look its location up.
				for _, in := range tc.instances {
					if in.id == ids[0] {
						want = primaryFlat
						if in.location != "global" {
							want = primaryUplifted
						}
					}
				}
			}
			got, err := strconv.ParseFloat(strings.TrimSpace(w.Header().Get("x-agentcc-cost")), 64)
			if err != nil || math.Abs(got-want) > 5e-7 {
				t.Fatalf("x-agentcc-cost = %q, want %.6f (served by %s)", w.Header().Get("x-agentcc-cost"), want, ids[0])
			}
		})
	}
}

// A streamed request records the instance that served it, the same as a plain
// one: after the targets' failover is exhausted, the fallback model's instance
// decides the uplift, not the instance the request first went to.
func TestVertexUpliftFollowsTheFallbackThatServedAStream(t *testing.T) {
	const (
		primaryModel  = "gemini-4-argon"
		fallbackModel = "gemini-4-argon-cyber-permissive"
		body          = `{"model":"gemini-4-argon","messages":[{"role":"user","content":"hi"}],"stream":true}`
		// vertexServedUsage at the fallback model's price, without and with
		// the regional uplift.
		fallbackFlat, fallbackUplifted = 0.005720, 0.006292
	)
	targetsAB := func(r *config.RoutingConfig) {
		r.ModelFallbacks = map[string][]string{primaryModel: {fallbackModel}}
		r.Targets = map[string][]config.RoutingTargetConfig{primaryModel: {{Provider: "a", Weight: 1}, {Provider: "b", Weight: 1}}}
	}
	targetsFail := func(id string, _ int) bool { return id == "a" || id == "b" }

	for _, tc := range []struct {
		name      string
		instances []vertexInstance
		want      float64
	}{
		{"failover exhausted, regional targets, global fallback serves",
			[]vertexInstance{
				{id: "a", location: "us-central1", models: []string{primaryModel}},
				{id: "b", location: "us-central1", models: []string{primaryModel}},
				{id: "fb", location: "global", models: []string{fallbackModel}},
			}, fallbackFlat},
		{"failover exhausted, global targets, regional fallback serves",
			[]vertexInstance{
				{id: "a", location: "global", models: []string{primaryModel}},
				{id: "b", location: "global", models: []string{primaryModel}},
				{id: "fb", location: "us-central1", models: []string{fallbackModel}},
			}, fallbackUplifted},
	} {
		t.Run(tc.name, func(t *testing.T) {
			upstream, served := vertexRoutingUpstream(t, targetsFail)
			defer upstream.Close()
			srv := vertexRoutingServer(t, upstream.URL, tc.instances, targetsAB)
			w := postVertexChat(srv, body, map[string]string{"x-agentcc-include-metadata": "1"})
			if w.Code != http.StatusOK {
				t.Fatalf("status %d: %s", w.Code, w.Body.String())
			}
			if ids := served(); len(ids) != 1 || ids[0] != "fb" {
				t.Fatalf("instances that served = %v, want only fb", ids)
			}
			final := finalStreamChunk(t, w.Body.String())
			if got := final.AgentccMetadata.Cost; math.Abs(got-tc.want) > 5e-7 {
				t.Fatalf("streamed cost = %v, want %.6f", got, tc.want)
			}
		})
	}
}
