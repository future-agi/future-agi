package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// declareOpenAI names the bridge's providers ("openai" unless ids are given)
// in the config, so routing targets may refer to them; the test server then
// registers the real ones, which replace these entries.
func declareOpenAI(cfg *config.Config, ids ...string) {
	if len(ids) == 0 {
		ids = []string{"openai"}
	}
	for _, id := range ids {
		cfg.Providers[id] = config.ProviderConfig{BaseURL: "http://127.0.0.1:9", APIKey: "test-key", APIFormat: "openai", Models: bridgeModels}
	}
}

// chatOnlyExtra is a caller's chat-only parameters the Responses API cannot
// carry.
const chatOnlyExtra = `"stop":["END"],"seed":7,`

// sentToResponses decodes the last /v1/responses body and reports the
// chat-only parameters that reached it.
func sentToResponses(t *testing.T, upstream *openAIUpstream) (model string, leaked []string) {
	t.Helper()
	var sent map[string]json.RawMessage
	if err := json.Unmarshal(upstream.body("/v1/responses"), &sent); err != nil {
		t.Fatalf("decoding /v1/responses body: %v", err)
	}
	json.Unmarshal(sent["model"], &model)
	for _, key := range []string{"stop", "seed"} {
		if _, ok := sent[key]; ok {
			leaked = append(leaked, key)
		}
	}
	return model, leaked
}

// A request the gateway itself moves onto gpt-5.6 (a model fallback or a
// failover target's model_override) was not the caller's choice of model, so
// its chat-only parameters are dropped and logged rather than refused with a
// remedy only the caller could apply.
func TestGatewayRetargetedOntoGPT56DropsChatOnlyParams(t *testing.T) {
	for _, path := range []string{"fallback", "failover"} {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%t", path, stream), func(t *testing.T) {
				srv, upstream := newResponsesBridgeServerWith(t, bridgeServerOptions{alsoAs: []string{"openai-b"}, configure: func(cfg *config.Config) {
					cfg.Routing.DefaultStrategy = "least-latency"
					cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 3, OnStatusCodes: []int{http.StatusBadGateway}}
					switch path {
					case "fallback":
						cfg.Routing.ModelFallbacks = map[string][]string{"gpt-5.5": {"gpt-5.6-terra"}}
					case "failover":
						declareOpenAI(cfg, "openai", "openai-b")
						cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"gpt-5.5": {
							{Provider: "openai", Priority: 1},
							{Provider: "openai-b", Priority: 2, ModelOverride: "gpt-5.6-terra"},
						}}
					}
				}})
				upstream.failChatModel = "gpt-5.5"
				w := postBridgeChat(t, srv, chatToolBody("gpt-5.5", stream, chatOnlyExtra), nil)
				if stream {
					checkToolStream(t, w)
				} else {
					if w.Code != http.StatusOK {
						t.Fatalf("status = %d, want 200 from gpt-5.6-terra: %s", w.Code, w.Body.String())
					}
					checkToolCompletion(t, w.Body.Bytes())
				}
				if upstream.count("/v1/responses") != 1 {
					t.Fatalf("/v1/responses hits = %d, want 1: %v", upstream.count("/v1/responses"), upstream.hits)
				}
				model, leaked := sentToResponses(t, upstream)
				if model != "gpt-5.6-terra" || len(leaked) > 0 {
					t.Errorf("sent model %q with %v, want gpt-5.6-terra with stop and seed dropped", model, leaked)
				}
			})
		}
	}
}

// The traffic mirror's target model is the gateway's choice too: a mirrored
// copy onto gpt-5.6 reaches the Responses API without the chat-only
// parameters instead of being refused before it is sent.
func TestMirrorOntoGPT56DropsChatOnlyParams(t *testing.T) {
	srv, upstream := newResponsesBridgeServerWith(t, bridgeServerOptions{configure: func(cfg *config.Config) {
		cfg.Routing.Mirror = config.MirrorConfig{Enabled: true, Rules: []config.MirrorRule{
			{SourceModel: "gpt-5.5", TargetProvider: "openai", TargetModel: "gpt-5.6-terra", SampleRate: 1},
		}}
	}})
	w := postBridgeChat(t, srv, chatToolBody("gpt-5.5", false, chatOnlyExtra), nil)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", w.Code, w.Body.String())
	}
	deadline := time.Now().Add(5 * time.Second)
	for upstream.count("/v1/responses") == 0 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if upstream.count("/v1/responses") != 1 {
		t.Fatalf("mirror never reached /v1/responses: %v", upstream.hits)
	}
	if model, leaked := sentToResponses(t, upstream); model != "gpt-5.6-terra" || len(leaked) > 0 {
		t.Errorf("mirror sent model %q with %v, want gpt-5.6-terra with stop and seed dropped", model, leaked)
	}
}

// A caller who asks for gpt-5.6 with function tools and stop or seed is still
// told, on the chat and the streaming route alike, and nothing is sent. So is
// a caller whose own model a routing target only renames.
func TestCallerOwnGPT56RequestWithChatOnlyParamsIsRefused(t *testing.T) {
	for _, renamed := range []bool{false, true} {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("renamed=%t/stream=%t", renamed, stream), func(t *testing.T) {
				srv, upstream := newResponsesBridgeServerWith(t, bridgeServerOptions{configure: func(cfg *config.Config) {
					if renamed {
						declareOpenAI(cfg)
						cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"gpt-5.6-terra": {
							{Provider: "openai", ModelOverride: "gpt-5.6-terra-2026-10-01"},
						}}
					}
				}})
				w := postBridgeChat(t, srv, chatToolBody("gpt-5.6-terra", stream, chatOnlyExtra), nil)
				if w.Code != http.StatusBadRequest {
					t.Fatalf("status = %d, want 400: %s", w.Code, w.Body.String())
				}
				var resp struct {
					Error struct {
						Code    string `json:"code"`
						Message string `json:"message"`
					} `json:"error"`
				}
				if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil || resp.Error.Code != "unsupported_over_responses" ||
					!strings.Contains(resp.Error.Message, "stop, seed") || !strings.Contains(resp.Error.Message, "reasoning_effort") {
					t.Errorf("error = %s, want unsupported_over_responses naming stop, seed with the reasoning_effort remedy", w.Body.String())
				}
				checkHits(t, upstream, 0, 0)
			})
		}
	}
}

// A GenAI caller's stopSequences are a chat-only parameter once translated. The route drops them for gpt-5.6 with tools and says so in
// x-agentcc-translation-drops, as it does for its other drops.
func TestGenAIChatOnlyParamsDroppedOverResponsesAreReported(t *testing.T) {
	for _, action := range []string{"generateContent", "streamGenerateContent"} {
		t.Run(action, func(t *testing.T) {
			srv, upstream := newResponsesBridgeServer(t)
			path := "/v1beta/models/gpt-5.6-terra:" + action
			if action == "streamGenerateContent" {
				path += "?alt=sse"
			}
			body := `{"contents":[{"role":"user","parts":[{"text":"Weather in Paris?"}]}],
				"tools":[{"functionDeclarations":[{"name":"get_weather","description":"Weather for a city","parameters":{"type":"object","properties":{"city":{"type":"string"}}}}]}],
				"generationConfig":{"stopSequences":["END"]}}`
			req := httptest.NewRequest(http.MethodPost, path, bytes.NewBufferString(body))
			req.Header.Set("Content-Type", "application/json")
			w := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(w, req)
			if w.Code != http.StatusOK || strings.Contains(w.Body.String(), "unsupported_over_responses") {
				t.Fatalf("status = %d: %s", w.Code, w.Body.String())
			}
			if !strings.Contains(w.Body.String(), `"get_weather"`) {
				t.Errorf("answer has no get_weather call: %s", w.Body.String())
			}
			checkHits(t, upstream, 0, 1)
			if model, leaked := sentToResponses(t, upstream); model != "gpt-5.6-terra" || len(leaked) > 0 {
				t.Errorf("sent model %q with %v, want gpt-5.6-terra with stop dropped", model, leaked)
			}
			drops := strings.Split(w.Header().Get("x-agentcc-translation-drops"), ",")
			for _, want := range []string{"stop"} {
				found := false
				for _, d := range drops {
					found = found || d == want
				}
				if !found {
					t.Errorf("x-agentcc-translation-drops = %q, want it to name %s", w.Header().Get("x-agentcc-translation-drops"), want)
				}
			}
		})
	}
}

// testRequestPlugin changes or refuses a request before the provider call, as
// a tool policy or guardrail would.
type testRequestPlugin struct {
	stripTools bool
	refuse     bool
}

func (p testRequestPlugin) Name() string  { return "test-request-plugin" }
func (p testRequestPlugin) Priority() int { return 10 }
func (p testRequestPlugin) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if p.refuse {
		return pipeline.ResultError(models.ErrForbidden("refused by policy"))
	}
	if p.stripTools && rc.Request != nil {
		rc.Request.Tools = nil
	}
	return pipeline.ResultContinue()
}
func (p testRequestPlugin) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

// The drops a translated route reports are the ones the request it finally
// sends goes through: none from the Responses route when a policy stripped
// its tools (it goes to chat completions with stop) or refused it.
func TestGenAIReportsResponsesDropsOnlyWhenItTakesThatRoute(t *testing.T) {
	body := `{"contents":[{"role":"user","parts":[{"text":"Weather in Paris?"}]}],
		"tools":[{"functionDeclarations":[{"name":"get_weather","description":"Weather for a city","parameters":{"type":"object","properties":{"city":{"type":"string"}}}}]}],
		"generationConfig":{"stopSequences":["END"]}}`
	for _, tt := range []struct {
		name   string
		plugin testRequestPlugin
		status int
		chat   int
	}{
		{"tools stripped", testRequestPlugin{stripTools: true}, http.StatusOK, 1},
		{"refused", testRequestPlugin{refuse: true}, http.StatusForbidden, 0},
	} {
		t.Run(tt.name, func(t *testing.T) {
			srv, upstream := newResponsesBridgeServerWith(t, bridgeServerOptions{plugins: []pipeline.Plugin{tt.plugin}})
			req := httptest.NewRequest(http.MethodPost, "/v1beta/models/gpt-5.6-terra:generateContent", bytes.NewBufferString(body))
			req.Header.Set("Content-Type", "application/json")
			w := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(w, req)
			if w.Code != tt.status {
				t.Fatalf("status = %d, want %d: %s", w.Code, tt.status, w.Body.String())
			}
			checkHits(t, upstream, tt.chat, 0)
			if got := w.Header().Get("x-agentcc-translation-drops"); strings.Contains(got, "stop") {
				t.Errorf("x-agentcc-translation-drops = %q, but no Responses call dropped stop", got)
			}
		})
	}
}
