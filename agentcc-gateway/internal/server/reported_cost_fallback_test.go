package server

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/providers/testhttp"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A model fallback can move a request from an org's instance of a provider
// ID to the operator's instance of the same ID, while the routing marker
// (org_provider_model_match) still names the first. The trust decision
// follows the instance that answered, which the chat handlers record.
func TestReportedCostVendorFollowsTheInstanceThatAnswered(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.Providers["openrouter"] = config.ProviderConfig{Type: "openrouter", APIFormat: "openai", BaseURL: "https://openrouter.ai/api", APIKey: "fake"}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), tenant.NewStore(), nil)
	// The org's own "openrouter" instance, at a host that is not OpenRouter's.
	// The IP literal passes the base URL check without a lookup; nothing is sent to it.
	orgCfg := &tenant.ProviderConfig{APIKey: "org-key", Enabled: true, BaseURL: "https://8.8.8.8", APIFormat: "openai"}
	orgInstance, err := srv.OrgProviderCache.GetOrCreateWithTenantConfig("org-1", "openrouter", orgCfg.APIKey, orgCfg)
	if err != nil {
		t.Fatal(err)
	}
	operator, _ := registry.GetProvider("openrouter")

	stale := func() map[string]string {
		return map[string]string{"org_id": "org-1", "org_provider_model_match": "model-a"}
	}
	for _, tc := range []struct {
		name     string
		servedBy any
		want     string
	}{
		{"operator's instance answered the fallback", operator, "openrouter"},
		{"org's instance answered", orgInstance, ""},
		{"nothing recorded: judged by the routing marker", nil, ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rc := &models.RequestContext{Provider: "openrouter", Metadata: stale(), ServedBy: tc.servedBy}
			if got := srv.ReportedCostVendor(rc); got != tc.want {
				t.Fatalf("ReportedCostVendor = %q, want %q", got, tc.want)
			}
		})
	}
}

type servedByObserver struct {
	mu       sync.Mutex
	servedBy []any
	meta     []map[string]string
}

func (o *servedByObserver) Name() string  { return "served_by_observer" }
func (o *servedByObserver) Priority() int { return 501 }
func (o *servedByObserver) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (o *servedByObserver) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	o.mu.Lock()
	defer o.mu.Unlock()
	o.servedBy = append(o.servedBy, rc.ServedBy)
	o.meta = append(o.meta, map[string]string{"cost": rc.Metadata["cost"], "cost_source": rc.Metadata["cost_source"], "provider": rc.Provider})
	return pipeline.ResultContinue()
}

// Through the chat handlers: the primary model's provider fails, the model
// fallback is answered by OpenRouter, and the request records OpenRouter's
// instance as the one that served it, streamed or not, so its stated cost
// is recorded.
func TestChatModelFallbackRecordsTheInstanceThatAnswered(t *testing.T) {
	failing := testhttp.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadGateway)
		_, _ = io.WriteString(w, `{"error":{"message":"down","type":"server_error"}}`)
	}))
	t.Cleanup(failing.Close)
	openrouter := testhttp.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		usage := `{"prompt_tokens":1000,"completion_tokens":100,"total_tokens":1100,"cost":0.00042}`
		if strings.Contains(string(body), `"stream":true`) {
			w.Header().Set("Content-Type", "text/event-stream")
			_, _ = fmt.Fprintf(w, "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":\"openai/gpt-4o-mini\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"hi\"},\"finish_reason\":\"stop\"}]}\n\ndata: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":\"openai/gpt-4o-mini\",\"choices\":[],\"usage\":%s}\n\ndata: [DONE]\n\n", usage)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = fmt.Fprintf(w, `{"id":"c1","object":"chat.completion","created":1,"model":"openai/gpt-4o-mini","choices":[{"index":0,"message":{"role":"assistant","content":"hi"},"finish_reason":"stop"}],"usage":%s}`, usage)
	}))
	t.Cleanup(openrouter.Close)

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "fallback", Key: "sk-agentcc-fallback", Owner: "test-org", KeyType: "internal"}}
	cfg.Providers["primary"] = config.ProviderConfig{APIFormat: "openai", BaseURL: "http://primary.internal", APIKey: "fake", Models: []string{"primary-model"}, DialContext: failing.DialContext}
	cfg.Providers["openrouter"] = config.ProviderConfig{Type: "openrouter", APIFormat: "openai", BaseURL: "http://openrouter.ai/api", APIKey: "fake", Models: []string{"openai/gpt-4o-mini"}, DialContext: openrouter.DialContext}
	cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 1, OnStatusCodes: []int{http.StatusBadGateway}}
	cfg.Routing.ModelFallbacks = map[string][]string{"primary-model": {"openai/gpt-4o-mini"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	db := testModelDBPtr()
	observer := &servedByObserver{}
	cost := costplugin.New(true, db.Load, nil, nil)
	srv := New(cfg, "", registry, pipeline.NewEngine(cost, observer), nil, nil, nil, nil, db, nil, nil)
	cost.SetPriceTables(srv.PriceTableName)
	cost.SetReportedCostVendors(srv.ReportedCostVendor)
	// As handlers_stream_retry_test.go does: with no routing targets the
	// server builds no failover, which the model fallback chain needs.
	srv.handlers.failover.Store(routing.NewFailover(cfg.Routing.Failover, registry.Router(), nil, nil))
	srv.handlers.modelFallbacks.Store(routing.NewModelFallbacks(cfg.Routing.ModelFallbacks))
	srv.ready.Store(true)
	want, _ := registry.GetProvider("openrouter")

	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%v", stream), func(t *testing.T) {
			body := fmt.Sprintf(`{"model":"primary-model","messages":[{"role":"user","content":"hi"}],"stream":%v}`, stream)
			req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
			req.Header.Set("Authorization", "Bearer sk-agentcc-fallback")
			req.Header.Set("Content-Type", "application/json")
			w := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(w, req)
			if w.Code != http.StatusOK {
				t.Fatalf("status %d: %s", w.Code, w.Body.String())
			}
			observer.mu.Lock()
			defer observer.mu.Unlock()
			if len(observer.servedBy) == 0 {
				t.Fatal("post plugins did not run")
			}
			last := len(observer.servedBy) - 1
			if got, ok := observer.servedBy[last].(providers.Provider); !ok || got != want {
				t.Fatalf("ServedBy = %v, want the openrouter provider that answered the fallback", observer.servedBy[last])
			}
			if m := observer.meta[last]; m["provider"] != "openrouter" || m["cost"] != "0.000420" || m["cost_source"] != costplugin.CostSourceProviderReported {
				t.Fatalf("metadata = %v, want openrouter's reported 0.000420", m)
			}
		})
	}
}

// A Responses stream can end in response.incomplete (the output limit was
// reached) as well as response.completed. Its usage, the vendor's stated cost
// included, is priced the same way. Shape from docs.perplexity.ai, values
// from its example.
func TestReportedCostOnAnIncompleteResponsesStream(t *testing.T) {
	const usage = `{"input_tokens":12088,"output_tokens":2743,"total_tokens":14831,"cost":{"currency":"USD","input_cost":0.00919,"output_cost":0.02743,"tool_calls_cost":0.0055,"cache_read_cost":0.00059,"total_cost":0.04271}}`
	fake := testhttp.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = fmt.Fprintf(w, "event: response.created\ndata: {\"type\":\"response.created\",\"response\":{\"id\":\"resp_1\",\"model\":\"sonar\"}}\n\n"+
			"event: response.incomplete\ndata: {\"type\":\"response.incomplete\",\"response\":{\"id\":\"resp_1\",\"object\":\"response\",\"status\":\"incomplete\",\"incomplete_details\":{\"reason\":\"max_output_tokens\"},\"model\":\"sonar\",\"output\":[],\"usage\":%s}}\n\n", usage)
	}))
	t.Cleanup(fake.Close)
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "incomplete", Key: "sk-agentcc-incomplete", Owner: "test-org", KeyType: "internal"}}
	cfg.Providers["perplexity"] = config.ProviderConfig{Type: "perplexity", APIFormat: "openai", BaseURL: "http://api.perplexity.ai", APIKey: "fake", Models: []string{"sonar"}, DialContext: fake.DialContext}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	db := testModelDBPtr()
	observer := &servedByObserver{}
	cost := costplugin.New(true, db.Load, nil, nil)
	srv := New(cfg, "", registry, pipeline.NewEngine(cost, observer), nil, nil, nil, nil, db, nil, nil)
	cost.SetPriceTables(srv.PriceTableName)
	cost.SetReportedCostVendors(srv.ReportedCostVendor)
	srv.ready.Store(true)

	req := httptest.NewRequest(http.MethodPost, "/v1/responses", strings.NewReader(`{"model":"sonar","input":"hi","stream":true}`))
	req.Header.Set("Authorization", "Bearer sk-agentcc-incomplete")
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("x-agentcc-include-metadata", "true")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("status %d: %s", w.Code, w.Body.String())
	}
	if got := reportedStreamCost(t, w.Body.String(), "response.incomplete"); got != "0.042710" {
		t.Fatalf("terminal agentcc_metadata.cost = %q, want total_cost 0.042710", got)
	}
	observer.mu.Lock()
	defer observer.mu.Unlock()
	if m := observer.meta[len(observer.meta)-1]; m["cost_source"] != costplugin.CostSourceProviderReported {
		t.Fatalf("metadata = %v", m)
	}
}
