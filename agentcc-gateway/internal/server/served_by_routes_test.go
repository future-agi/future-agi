package server

import (
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/providers/testhttp"
)

// The non-chat routes record the instance that answered, as the chat handlers
// do, so the reported-cost trust rule is judged on it rather than on an
// instance resolved again after the call.
func TestNonChatRoutesRecordTheInstanceThatAnswered(t *testing.T) {
	const model = "openai/gpt-4o-mini"
	usage := `"prompt_tokens":1000,"completion_tokens":100,"total_tokens":1100,"cost":0.00042`
	upstream := testhttp.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		stream := strings.Contains(string(body), `"stream":true`)
		switch {
		case strings.HasSuffix(r.URL.Path, "/responses"):
			resp := fmt.Sprintf(`{"id":"resp_1","object":"response","status":"completed","model":%q,"output":[],"usage":{"input_tokens":1000,"output_tokens":100,"total_tokens":1100,"cost":0.00042}}`, model)
			if stream {
				w.Header().Set("Content-Type", "text/event-stream")
				_, _ = fmt.Fprintf(w, "event: response.completed\ndata: {\"type\":\"response.completed\",\"response\":%s}\n\n", resp)
				return
			}
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, resp)
		case strings.HasSuffix(r.URL.Path, "/completions") && !strings.HasSuffix(r.URL.Path, "/chat/completions"):
			w.Header().Set("Content-Type", "application/json")
			_, _ = fmt.Fprintf(w, `{"id":"cmpl_1","object":"text_completion","created":1,"model":%q,"choices":[{"text":"hi","index":0,"finish_reason":"stop"}],"usage":{%s}}`, model, usage)
		case strings.HasSuffix(r.URL.Path, "/chat/completions") && stream:
			w.Header().Set("Content-Type", "text/event-stream")
			_, _ = fmt.Fprintf(w, "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":%q,\"choices\":[{\"index\":0,\"delta\":{\"content\":\"hi\"},\"finish_reason\":\"stop\"}]}\n\ndata: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":%q,\"choices\":[],\"usage\":{%s}}\n\ndata: [DONE]\n\n", model, model, usage)
		case strings.HasSuffix(r.URL.Path, "/chat/completions"):
			w.Header().Set("Content-Type", "application/json")
			_, _ = fmt.Fprintf(w, `{"id":"c1","object":"chat.completion","created":1,"model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"hi"},"finish_reason":"stop"}],"usage":{%s}}`, model, usage)
		default:
			http.Error(w, "unexpected upstream route "+r.URL.Path, http.StatusNotFound)
		}
	}))
	t.Cleanup(upstream.Close)

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "served", Key: "sk-agentcc-served", Owner: "test-org", KeyType: "internal"}}
	cfg.Providers["openrouter"] = config.ProviderConfig{Type: "openrouter", APIFormat: "openai", BaseURL: "http://openrouter.ai/api", APIKey: "fake", Models: []string{model}, DialContext: upstream.DialContext}
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
	want, _ := registry.GetProvider("openrouter")

	for _, route := range []struct{ name, path, body string }{
		{"responses", "/v1/responses", fmt.Sprintf(`{"model":%q,"input":"hi"}`, model)},
		{"responses stream", "/v1/responses", fmt.Sprintf(`{"model":%q,"input":"hi","stream":true}`, model)},
		{"messages", "/v1/messages", fmt.Sprintf(`{"model":%q,"max_tokens":32,"messages":[{"role":"user","content":"hi"}]}`, model)},
		{"messages stream", "/v1/messages", fmt.Sprintf(`{"model":%q,"max_tokens":32,"messages":[{"role":"user","content":"hi"}],"stream":true}`, model)},
		{"completions", "/v1/completions", fmt.Sprintf(`{"model":%q,"prompt":"hi"}`, model)},
		{"completions stream", "/v1/completions", fmt.Sprintf(`{"model":%q,"prompt":"hi","stream":true}`, model)},
	} {
		t.Run(route.name, func(t *testing.T) {
			observer.mu.Lock()
			before := len(observer.servedBy)
			observer.mu.Unlock()
			req := httptest.NewRequest(http.MethodPost, route.path, strings.NewReader(route.body))
			req.Header.Set("Authorization", "Bearer sk-agentcc-served")
			req.Header.Set("Content-Type", "application/json")
			req.Header.Set("anthropic-version", "2023-06-01")
			w := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(w, req)
			if w.Code != http.StatusOK {
				t.Fatalf("status %d: %s", w.Code, w.Body.String())
			}
			observer.mu.Lock()
			defer observer.mu.Unlock()
			if len(observer.servedBy) == before {
				t.Fatal("post plugins did not run")
			}
			if got, ok := observer.servedBy[len(observer.servedBy)-1].(providers.Provider); !ok || got != want {
				t.Fatalf("ServedBy = %v, want the openrouter instance that answered", observer.servedBy[len(observer.servedBy)-1])
			}
		})
	}
}
