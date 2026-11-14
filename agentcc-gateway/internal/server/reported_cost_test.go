package server

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/providers/testhttp"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type reportedFixture struct {
	vendor, host, model, priceKey, chatCost, responseCost, chatWant, responseWant, unit string
}

var reportedFixtures = []reportedFixture{
	{"openrouter", "openrouter.ai", "anthropic/claude-3-haiku", "openrouter/anthropic/claude-3-haiku", `"cost":0.00042`, `"cost":0.00042`, "0.000420", "0.000420", "openrouter_credits"},
	// Responses cost shape from docs.perplexity.ai, values from its example.
	{"perplexity", "api.perplexity.ai", "sonar", "perplexity/sonar", `"cost":{"input_tokens_cost":0.0001,"output_tokens_cost":0.0002,"request_cost":0.005,"total_cost":0.0053}`, `"cost":{"currency":"USD","input_cost":0.00919,"output_cost":0.02743,"tool_calls_cost":0.0055,"cache_read_cost":0.00059,"total_cost":0.04271}`, "0.005300", "0.042710", "usd"},
	{"xai", "api.x.ai", "grok-3", "xai/grok-3", `"cost_in_usd_ticks":37756000`, `"cost_in_usd_ticks":37756000`, "0.003776", "0.003776", "usd_ticks"},
}

type reportedObservation struct {
	mu     sync.Mutex
	values []map[string]string
}

func (o *reportedObservation) Name() string  { return "reported_cost_observer" }
func (o *reportedObservation) Priority() int { return 501 }
func (o *reportedObservation) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (o *reportedObservation) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	o.mu.Lock()
	defer o.mu.Unlock()
	copy := make(map[string]string)
	for _, key := range []string{"cost", "cost_source", "cost_reported_unit", "cost_catalogue"} {
		copy[key] = rc.Metadata[key]
	}
	o.values = append(o.values, copy)
	return pipeline.ResultContinue()
}
func (o *reportedObservation) last(t *testing.T) map[string]string {
	t.Helper()
	o.mu.Lock()
	defer o.mu.Unlock()
	if len(o.values) == 0 {
		t.Fatal("cost plugin did not run")
	}
	return o.values[len(o.values)-1]
}

func reportedServer(t *testing.T, f reportedFixture, typ, id, host string, ignore bool, responseCost string) (*Server, *reportedObservation) {
	t.Helper()
	fake := testhttp.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"data":[]}`)
			return
		}
		b, _ := io.ReadAll(r.Body)
		stream := strings.Contains(string(b), `"stream":true`)
		w.Header().Set("Content-Type", "application/json")
		usage := `"prompt_tokens":1000,"completion_tokens":100,"total_tokens":1100,` + f.chatCost
		if strings.HasSuffix(r.URL.Path, "/responses") {
			usage = `"input_tokens":12088,"output_tokens":2743,"total_tokens":14831`
			if responseCost != "" {
				usage += "," + responseCost
			}
			completed := fmt.Sprintf(`{"type":"response.completed","response":{"id":"resp_1","object":"response","status":"completed","model":%q,"output":[],"usage":{%s}}}`, f.model, usage)
			if stream {
				w.Header().Set("Content-Type", "text/event-stream")
				_, _ = fmt.Fprintf(w, "event: response.created\ndata: {\"type\":\"response.created\",\"response\":{\"id\":\"resp_1\",\"model\":%q}}\n\nevent: response.completed\ndata: %s\n\n", f.model, completed)
				return
			}
			_, _ = fmt.Fprintf(w, `{"id":"resp_1","object":"response","status":"completed","model":%q,"output":[],"usage":{%s}}`, f.model, usage)
			return
		}
		if strings.HasSuffix(r.URL.Path, "/completions") && !strings.HasSuffix(r.URL.Path, "/chat/completions") {
			_, _ = fmt.Fprintf(w, `{"id":"cmpl_1","object":"text_completion","created":1,"model":%q,"choices":[{"text":"hi","index":0,"finish_reason":"stop"}],"usage":{%s}}`, f.model, usage)
			return
		}
		if !strings.HasSuffix(r.URL.Path, "/chat/completions") {
			http.Error(w, "unexpected upstream route "+r.URL.Path, 404)
			return
		}
		if stream {
			w.Header().Set("Content-Type", "text/event-stream")
			_, _ = fmt.Fprintf(w, "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":%q,\"choices\":[{\"index\":0,\"delta\":{\"content\":\"hi\"},\"finish_reason\":\"stop\"}]}\n\ndata: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":%q,\"choices\":[],\"usage\":{%s}}\n\ndata: [DONE]\n\n", f.model, f.model, usage)
			return
		}
		_, _ = fmt.Fprintf(w, `{"id":"c1","object":"chat.completion","created":1,"model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"hi"},"finish_reason":"stop"}],"usage":{%s}}`, f.model, usage)
	}))
	t.Cleanup(fake.Close)
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "reported", Key: "sk-agentcc-reported", Owner: "test-org", KeyType: "internal"}}
	cfg.Providers[id] = config.ProviderConfig{Type: typ, APIFormat: "openai", BaseURL: "http://" + host + "/api", APIKey: "fake", Models: []string{f.model}, DialContext: fake.DialContext, IgnoreProviderCost: ignore}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	db := testModelDBPtr()
	observer := &reportedObservation{}
	cost := costplugin.New(true, db.Load, nil, nil)
	srv := New(cfg, "", registry, pipeline.NewEngine(cost, observer), nil, nil, nil, nil, db, nil, nil)
	cost.SetPriceTables(srv.PriceTableName)
	cost.SetReportedCostVendors(srv.ReportedCostVendor)
	srv.ready.Store(true)
	return srv, observer
}

func reportedPost(t *testing.T, srv *Server, path, body string, stream bool) (string, string) {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, path, strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer sk-agentcc-reported")
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("anthropic-version", "2023-06-01")
	if stream {
		req.Header.Set("x-agentcc-include-metadata", "true")
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != 200 {
		t.Fatalf("%s status %d: %s", path, w.Code, w.Body.String())
	}
	return w.Header().Get("x-agentcc-cost"), w.Body.String()
}

func reportedStreamCost(t *testing.T, body, terminal string) string {
	t.Helper()
	for _, frame := range sseFrames(body) {
		if frame.event != terminal || frame.data == "[DONE]" {
			continue
		}
		var event struct {
			Metadata struct {
				Cost float64 `json:"cost"`
			} `json:"agentcc_metadata"`
		}
		if err := json.Unmarshal([]byte(frame.data), &event); err != nil {
			t.Fatal(err)
		}
		if strings.Contains(frame.data, `"agentcc_metadata"`) {
			return fmt.Sprintf("%.6f", event.Metadata.Cost)
		}
	}
	t.Fatalf("missing terminal agentcc_metadata in %s", body)
	return ""
}
func reportedCatalogue(t *testing.T, f reportedFixture, prompt, completion int) string {
	t.Helper()
	got, ok := testModelDBPtr().Load().CalculateCost(f.priceKey, prompt, completion, modeldb.CostOptions{})
	if !ok || got == 0 {
		t.Fatalf("missing nonzero catalogue cost for %q", f.priceKey)
	}
	return fmt.Sprintf("%.6f", got)
}

func TestReportedCostVendorEndpoints(t *testing.T) {
	for _, f := range reportedFixtures {
		t.Run(f.vendor, func(t *testing.T) {
			srv, observed := reportedServer(t, f, f.vendor, f.vendor, f.host, false, f.responseCost)
			catalogue := reportedCatalogue(t, f, 1000, 100)
			if catalogue == f.chatWant {
				t.Fatalf("catalogue equals reported cost: %s", catalogue)
			}
			for _, route := range []struct {
				name, path, request, usage, want, terminal string
				stream                                     bool
			}{
				{"chat", "/v1/chat/completions", fmt.Sprintf(`{"model":%q,"messages":[{"role":"user","content":"hi"}]}`, f.model), f.chatCost, f.chatWant, "", false},
				{"chat stream", "/v1/chat/completions", fmt.Sprintf(`{"model":%q,"messages":[{"role":"user","content":"hi"}],"stream":true}`, f.model), f.chatCost, f.chatWant, "", true},
				{"responses", "/v1/responses", fmt.Sprintf(`{"model":%q,"input":"hi"}`, f.model), f.responseCost, f.responseWant, "", false},
				{"responses stream", "/v1/responses", fmt.Sprintf(`{"model":%q,"input":"hi","stream":true}`, f.model), f.responseCost, f.responseWant, "response.completed", true},
				{"messages", "/v1/messages", fmt.Sprintf(`{"model":%q,"max_tokens":32,"messages":[{"role":"user","content":"hi"}]}`, f.model), f.chatCost, f.chatWant, "", false},
				{"messages stream", "/v1/messages", fmt.Sprintf(`{"model":%q,"max_tokens":32,"messages":[{"role":"user","content":"hi"}],"stream":true}`, f.model), f.chatCost, f.chatWant, "message_stop", true},
				{"completions", "/v1/completions", fmt.Sprintf(`{"model":%q,"prompt":"hi"}`, f.model), f.chatCost, f.chatWant, "", false},
			} {
				t.Run(route.name, func(t *testing.T) {
					header, body := reportedPost(t, srv, route.path, route.request, route.stream)
					if route.stream {
						if got := reportedStreamCost(t, body, route.terminal); got != route.want {
							t.Errorf("stream cost = %q, want %q; body %s", got, route.want, body)
						}
					} else if header != route.want {
						t.Errorf("x-agentcc-cost = %q, want %q; body %s", header, route.want, body)
					}
					if route.path != "/v1/messages" && !strings.Contains(body, route.usage) {
						t.Errorf("original usage field missing from client body: %s", body)
					}
					meta := observed.last(t)
					if meta["cost_source"] != "provider_reported" || meta["cost_reported_unit"] != f.unit || meta["cost"] != route.want {
						t.Errorf("recorded metadata = %v; want reported %s in %s", meta, route.want, f.unit)
					}
					wantCatalogue := catalogue
					if route.path == "/v1/responses" {
						wantCatalogue = reportedCatalogue(t, f, 12088, 2743)
					}
					if meta["cost_catalogue"] != wantCatalogue {
						t.Errorf("catalogue audit price = %q, want %q", meta["cost_catalogue"], wantCatalogue)
					}
				})
			}
		})
	}
}

func TestReportedCostTrustBoundaries(t *testing.T) {
	f := reportedFixtures[0]
	for _, tc := range []struct {
		name, typ, id, host string
		ignore              bool
	}{
		{"other type on vendor host", "openai", "openrouter", "openrouter.ai", false},
		{"vendor type on proxy", "openrouter", "openrouter", "proxy.internal", false},
		{"custom ID without vendor type", "", "my-or", "openrouter.ai", false},
		{"operator ignores vendor cost", "openrouter", "openrouter", "openrouter.ai", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := f
			if tc.id == "my-or" {
				// An unrelated provider ID has no openrouter price-table prefix.
				f.model, f.priceKey = "gpt-4o", "gpt-4o"
			}
			srv, observed := reportedServer(t, f, tc.typ, tc.id, tc.host, tc.ignore, f.responseCost)
			want := reportedCatalogue(t, f, 1000, 100)
			header, _ := reportedPost(t, srv, "/v1/chat/completions", fmt.Sprintf(`{"model":%q,"messages":[{"role":"user","content":"hi"}]}`, f.model), false)
			if header != want || observed.last(t)["cost_source"] != "catalogue" {
				t.Errorf("cost = %q, metadata = %v; want catalogue %s", header, observed.last(t), want)
			}
		})
	}
}

func TestReportedCostOrgInstanceTrust(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.Providers["openrouter"] = config.ProviderConfig{Type: "openrouter", APIFormat: "openai", BaseURL: "https://openrouter.ai/api", APIKey: "fake"}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), tenant.NewStore(), nil)
	orgCfg := &tenant.ProviderConfig{APIKey: "org-key", Enabled: true, BaseURL: "https://8.8.8.8", APIFormat: "openai"}
	if _, err := srv.OrgProviderCache.GetOrCreateWithTenantConfig("org-1", "openrouter", orgCfg.APIKey, orgCfg); err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		name     string
		metadata map[string]string
		want     string
	}{
		{"org instance served", map[string]string{"org_id": "org-1", "org_provider": "true"}, ""},
		{"internal operator provider served", map[string]string{"org_id": "org-1", "key_type": "internal"}, "openrouter"},
		{"operator without org", map[string]string{}, "openrouter"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rc := &models.RequestContext{Provider: "openrouter", Metadata: tc.metadata}
			if got := srv.ReportedCostVendor(rc); got != tc.want {
				t.Errorf("vendor = %q, want %q", got, tc.want)
			}
		})
	}
}

// TH-10225: a Responses request's tool and reasoning cost is already part of
// total_cost. The gateway must not recompute, add line items, or drop it.
func TestReportedCostPerplexityResponsesToolsAndEffort(t *testing.T) {
	f := reportedFixtures[1]
	catalogue := reportedCatalogue(t, f, 12088, 2743)
	// shape from docs.perplexity.ai, values from its example.
	docCost := f.responseCost
	// shape from docs.perplexity.ai, values synthetic.
	for _, tc := range []struct{ name, cost, extras, want, source string }{
		{"no tool docs example", docCost, "", "0.042710", "provider_reported"},
		{"web_search", `"cost":{"currency":"USD","input_cost":0.001,"output_cost":0.002,"tool_calls_cost":0.0025,"tool_calls_details":{"web_search":0.0025},"total_cost":0.0055}`, `,"tools":[{"type":"web_search"}]`, "0.005500", "provider_reported"},
		{"effort low", `"cost":{"currency":"USD","input_cost":0.001,"output_cost":0.002,"tool_calls_cost":0.0005,"cache_read_cost":0.0001,"total_cost":0.0036}`, `,"reasoning":{"effort":"low"}`, "0.003600", "provider_reported"},
		{"effort medium", `"cost":{"currency":"USD","input_cost":0.001,"output_cost":0.002,"tool_calls_cost":0.0015,"cache_read_cost":0.0003,"total_cost":0.0048}`, `,"reasoning":{"effort":"medium"}`, "0.004800", "provider_reported"},
		{"effort high", `"cost":{"currency":"USD","input_cost":0.001,"output_cost":0.002,"tool_calls_cost":0.0035,"cache_read_cost":0.0007,"total_cost":0.0072}`, `,"reasoning":{"effort":"high"}`, "0.007200", "provider_reported"},
		{"no cost object", "", "", catalogue, "catalogue"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv, observed := reportedServer(t, f, f.vendor, f.vendor, f.host, false, tc.cost)
			request := fmt.Sprintf(`{"model":%q,"input":"hi"%s}`, f.model, tc.extras)
			got, body := reportedPost(t, srv, "/v1/responses", request, false)
			if got != tc.want || observed.last(t)["cost_source"] != tc.source {
				t.Errorf("cost = %q, metadata = %v; want %s (%s)", got, observed.last(t), tc.want, tc.source)
			}
			if tc.cost != "" && !strings.Contains(body, tc.cost) {
				t.Errorf("client response lost original usage cost: %s", body)
			}
		})
	}
}
