package server

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// Exercise the real HTTP handler, metadata parsing, router and provider adapters.
func TestChatCompletionConditionalNumericRouting(t *testing.T) {
	for _, op := range []string{"$lt", "$lte"} {
		t.Run(op, func(t *testing.T) {
			cfg := config.DefaultConfig()
			calls := map[string]*atomic.Int64{}
			for _, id := range []string{"default-provider", "numeric-provider"} {
				count := &atomic.Int64{}
				calls[id] = count
				upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if r.Method != http.MethodPost || r.URL.Path != "/v1/chat/completions" {
						http.NotFound(w, r)
						return
					}
					var req models.ChatCompletionRequest
					if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
						w.WriteHeader(http.StatusBadRequest)
						return
					}
					count.Add(1)
					if req.Stream {
						w.Header().Set("Content-Type", "text/event-stream")
						fmt.Fprintf(w, "data: {\"id\":\"numeric-test\",\"object\":\"chat.completion.chunk\",\"model\":%q,\"choices\":[{\"index\":0,\"delta\":{\"content\":%q},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n", req.Model, id)
						return
					}
					w.Header().Set("Content-Type", "application/json")
					fmt.Fprintf(w, `{"id":"numeric-test","object":"chat.completion","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":%q},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`, req.Model, id)
				}))
				t.Cleanup(upstream.Close)
				model := "test-model"
				if id == "numeric-provider" {
					model = "numeric-model"
				}
				cfg.Providers[id] = config.ProviderConfig{BaseURL: upstream.URL, APIFormat: "openai", Models: []string{model}}
			}
			cfg.Routing.ConditionalRoutes = []config.ConditionalRouteConfig{{
				Name:      "small-request",
				Condition: config.ConditionConfig{Field: "metadata.tokens", Op: op, Value: 1000},
				Action:    config.RouteActionConfig{Provider: "numeric-provider", ModelOverride: "numeric-model"},
			}}
			registry, err := providers.NewRegistry(cfg)
			if err != nil {
				t.Fatal(err)
			}
			srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
			srv.ready.Store(true)
			gateway := httptest.NewServer(srv.httpServer.Handler)
			t.Cleanup(gateway.Close)

			for _, tc := range []struct {
				name     string
				metadata string
				match    bool
			}{
				{"missing", `{}`, false},
				{"empty", `{"tokens":""}`, false},
				{"text", `{"tokens":"unknown"}`, false},
				{"null", `{"tokens":null}`, false},
				{"boolean", `{"tokens":false}`, false},
				{"array", `{"tokens":[1,2]}`, false},
				{"nan", `{"tokens":"NaN"}`, false},
				{"zero", `{"tokens":"0"}`, true},
				{"below", `{"tokens":"999"}`, true},
				{"equal", `{"tokens":"1000"}`, op == "$lte"},
				{"above", `{"tokens":"1001"}`, false},
			} {
				for _, stream := range []bool{false, true} {
					t.Run(fmt.Sprintf("%s/stream=%t", tc.name, stream), func(t *testing.T) {
						wantProvider, wantModel := "default-provider", "test-model"
						if tc.match {
							wantProvider, wantModel = "numeric-provider", "numeric-model"
						}
						before := map[string]int64{}
						for id, count := range calls {
							before[id] = count.Load()
						}
						body := fmt.Sprintf(`{"model":"test-model","stream":%t,"messages":[{"role":"user","content":"routing probe"}]}`, stream)
						req, err := http.NewRequest(http.MethodPost, gateway.URL+"/v1/chat/completions", strings.NewReader(body))
						if err != nil {
							t.Fatal(err)
						}
						req.Header.Set("Content-Type", "application/json")
						req.Header.Set("x-agentcc-metadata", tc.metadata)
						resp, err := gateway.Client().Do(req)
						if err != nil {
							t.Fatal(err)
						}
						defer resp.Body.Close()
						payload, err := io.ReadAll(resp.Body)
						if err != nil {
							t.Fatal(err)
						}
						if resp.StatusCode != http.StatusOK {
							t.Fatalf("status = %d, want 200", resp.StatusCode)
						}
						if got := resp.Header.Get("x-agentcc-provider"); got != wantProvider {
							t.Errorf("provider = %q, want %q", got, wantProvider)
						}
						if !strings.Contains(string(payload), fmt.Sprintf(`"model":%q`, wantModel)) || !strings.Contains(string(payload), fmt.Sprintf(`"content":%q`, wantProvider)) {
							t.Errorf("response does not contain expected model %q and provider content %q", wantModel, wantProvider)
						}
						if stream && !strings.Contains(string(payload), "data: [DONE]") {
							t.Error("stream did not complete")
						}
						for id, count := range calls {
							wantCalls := int64(0)
							if id == wantProvider {
								wantCalls = 1
							}
							if got := count.Load() - before[id]; got != wantCalls {
								t.Errorf("%s calls = %d, want %d", id, got, wantCalls)
							}
						}
					})
				}
			}
		})
	}
}
