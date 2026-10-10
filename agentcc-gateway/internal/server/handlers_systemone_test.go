package server

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/providers/testhttp"
)

const systemOneBody = `{"model":"jev-latest","state":{"text":"private-state"},"questions":{"q":{"type":"score","instructions":"private-instructions","criteria":["bad","okay","good"],"future":{"keep":true}}}}`
const systemOneFixture = `{"model":"jev-1.13.0","answers":{"q":{"type":"score","score":1.05,"legend":{"0":"bad","1":"okay","2":"good"},"probabilities":{"0":0.2,"1":0.55,"2":0.25},"confidence":0.8,"future":{"keep":true}}},"usage":{"input_tokens":17,"output_tokens":3}}`

func newSystemOneServer(t *testing.T, cfg *config.Config, plugins ...pipeline.Plugin) *Server {
	t.Helper()
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { registry.Close() })
	srv := New(cfg, "", registry, pipeline.NewEngine(plugins...), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	t.Cleanup(func() { srv.Shutdown(context.Background()) })
	return srv
}

func systemOneConfig(format string) *config.Config {
	cfg := config.DefaultConfig()
	local := false
	cfg.Providers = map[string]config.ProviderConfig{"typesafe": {APIFormat: format, Models: []string{"jev-latest"}, Local: &local, DialContext: func(context.Context, string, string) (net.Conn, error) {
		return nil, errors.New("unexpected upstream call")
	}}}
	return cfg
}

func postSystemOne(srv *Server, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, "/v1/systemone", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer synthetic-caller-key")
	req.Header.Set("x-agentcc-metadata", `{"workflow":"eval-test"}`)
	req.Header.Set("x-agentcc-timeout", "1500ms")
	rr := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(rr, req)
	return rr
}

func TestSystemOneHandlerValidation(t *testing.T) {
	for _, tt := range []struct {
		name, body, format string
		limit              int64
		status             int
		code               string
	}{
		{"missing_model", `{}`, "typesafe", 0, 400, "missing_model"},
		{"missing_state", `{"model":"jev-latest"}`, "typesafe", 0, 400, "missing_state"},
		{"missing_questions", `{"model":"jev-latest","state":"private-state"}`, "typesafe", 0, 400, "missing_questions"},
		{"invalid_question", `{"model":"jev-latest","state":"private-state","questions":{"q":{}}}`, "typesafe", 0, 400, "invalid_question"},
		{"invalid_json", `{"state":"private-state","model":`, "typesafe", 0, 400, "invalid_json"},
		{"oversize", systemOneBody, "typesafe", 10, 413, "request_too_large"},
		{"unsupported", systemOneBody, "openai", 0, 501, "not_supported"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			cfg := systemOneConfig(tt.format)
			if tt.limit > 0 {
				cfg.Server.MaxRequestBodySize = tt.limit
			}
			rr := postSystemOne(newSystemOneServer(t, cfg), tt.body)
			var result models.ErrorResponse
			if err := json.Unmarshal(rr.Body.Bytes(), &result); err != nil {
				t.Fatalf("status=%d body=%s", rr.Code, rr.Body.String())
			}
			if rr.Code != tt.status || result.Error.Code != tt.code {
				t.Fatalf("got %d %s, want %d %s", rr.Code, rr.Body.String(), tt.status, tt.code)
			}
			for _, secret := range []string{"private-state", "private-instructions"} {
				if strings.Contains(rr.Body.String(), secret) {
					t.Fatal("response leaked content")
				}
			}
		})
	}
}

type systemOneObserver struct {
	t      *testing.T
	called bool
}

func (p *systemOneObserver) Name() string  { return "systemone_observer" }
func (p *systemOneObserver) Priority() int { return 100 }
func (p *systemOneObserver) ProcessRequest(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if rc.EndpointType != "systemone" || rc.SystemOneRequest == nil || rc.Model != "jev-latest" || rc.RequestID == "" || rc.TraceID == "" {
		p.t.Error("missing endpoint, request, model, or tracing context")
	}
	if rc.Metadata["workflow"] != "eval-test" || rc.Metadata["timeout_ms"] != "1500" {
		p.t.Errorf("caller metadata/timeout not carried: %v", rc.Metadata)
	}
	if rc.Metadata["authorization"] != "Bearer synthetic-caller-key" {
		p.t.Error("caller auth metadata not carried")
	}
	return pipeline.ResultContinue()
}
func (p *systemOneObserver) ProcessResponse(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	p.called = true
	if rc.SystemOneResponse == nil || rc.Response == nil || rc.Response.Usage == nil {
		p.t.Error("post-plugins cannot see response usage")
		return pipeline.ResultContinue()
	}
	usage := rc.Response.Usage
	if usage.PromptTokens != 17 || usage.CompletionTokens != 3 || usage.TotalTokens != 20 {
		p.t.Errorf("usage=%+v", usage)
	}
	if rc.ResolvedModel != "jev-1.13.0" || rc.Provider != "typesafe" || rc.Model != "jev-latest" {
		p.t.Error("requested/returned model or provider missing")
	}
	if rc.Request != nil || len(rc.Response.Choices) != 0 {
		p.t.Error("structured content should not enter chat logging payloads")
	}
	return pipeline.ResultContinue()
}

func TestSystemOneRouteHappyPathAndUsage(t *testing.T) {
	calls := 0
	upstream := testhttp.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.URL.Path != "/v1/systemone" || r.Header.Get("Authorization") != "Bearer synthetic-provider-key" {
			t.Error("upstream request path/key incorrect")
		}
		body, _ := io.ReadAll(r.Body)
		var got, want any
		json.Unmarshal(body, &got)
		json.Unmarshal([]byte(systemOneBody), &want)
		if !reflect.DeepEqual(got, want) {
			t.Error("upstream body changed")
		}
		io.WriteString(w, systemOneFixture)
	}))
	defer upstream.Close()
	cfg := systemOneConfig("typesafe")
	pc := cfg.Providers["typesafe"]
	pc.BaseURL = upstream.URL
	pc.DialContext = upstream.DialContext
	pc.APIKey = "synthetic-provider-key"
	cfg.Providers["typesafe"] = pc
	observer := &systemOneObserver{t: t}
	rr := postSystemOne(newSystemOneServer(t, cfg, observer), systemOneBody)
	if rr.Code != 200 {
		t.Fatalf("route status=%d body=%s, want 200", rr.Code, rr.Body.String())
	}
	if !observer.called || calls != 1 {
		t.Fatalf("pipeline called=%v upstream calls=%d", observer.called, calls)
	}
	var got, want any
	json.Unmarshal(rr.Body.Bytes(), &got)
	json.Unmarshal([]byte(systemOneFixture), &want)
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("response changed: %s", rr.Body.String())
	}
	for _, header := range []string{"x-agentcc-request-id", "x-agentcc-trace-id", "x-agentcc-provider", "x-agentcc-model-used", "x-agentcc-latency-ms", "x-agentcc-timeout-ms"} {
		if rr.Header().Get(header) == "" {
			t.Errorf("missing %s", header)
		}
	}
	if rr.Header().Get("x-agentcc-model-used") != "jev-1.13.0" {
		t.Error("actual model header incorrect")
	}
	if strings.Contains(rr.Header().Get("Authorization"), "synthetic-provider-key") || strings.Contains(rr.Body.String(), "synthetic-provider-key") {
		t.Fatal("provider key leaked")
	}
}

func TestSystemOneDoesNotUseChatModelFallbacks(t *testing.T) {
	cfg := systemOneConfig("typesafe")
	cfg.Providers["chat"] = config.ProviderConfig{APIFormat: "openai", Models: []string{"turing_small"}}
	cfg.Routing.ModelFallbacks = map[string][]string{"jev-unknown": {"turing_small"}}
	rr := postSystemOne(newSystemOneServer(t, cfg), strings.Replace(systemOneBody, "jev-latest", "jev-unknown", 1))
	if rr.Code != 404 {
		t.Fatalf("unknown Jev must fail without chat fallback: status=%d body=%s", rr.Code, rr.Body.String())
	}
}

func TestSystemOneRejectsModelOverrides(t *testing.T) {
	for _, mode := range []string{"conditional", "weighted"} {
		t.Run(mode, func(t *testing.T) {
			cfg := systemOneConfig("typesafe")
			if mode == "conditional" {
				cfg.Routing.ConditionalRoutes = []config.ConditionalRouteConfig{{Name: "rewrite-model", Condition: config.ConditionConfig{Field: "model", Op: "$eq", Value: "jev-latest"}, Action: config.RouteActionConfig{Provider: "typesafe", ModelOverride: "turing_small"}}}
			} else {
				pc := cfg.Providers["typesafe"]
				pc.Models = []string{"jev-1.13.0"}
				cfg.Providers["typesafe"] = pc
				cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"jev-latest": {{Provider: "typesafe", Weight: 1, ModelOverride: "jev-1.13.0"}, {Provider: "typesafe", Weight: 1, ModelOverride: "jev-1.13.0"}}}
			}
			rr := postSystemOne(newSystemOneServer(t, cfg), systemOneBody)
			var result models.ErrorResponse
			json.Unmarshal(rr.Body.Bytes(), &result)
			if rr.Code != 400 || result.Error.Code != "model_override_not_supported" {
				t.Fatalf("model rewrite must fail safely: status=%d body=%s", rr.Code, rr.Body.String())
			}
		})
	}
}
