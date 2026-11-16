package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	budgetpkg "github.com/futureagi/agentcc-gateway/internal/budget"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	budgetplugin "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	"github.com/futureagi/agentcc-gateway/internal/plugins/logging"
	"github.com/futureagi/agentcc-gateway/internal/plugins/ratelimit"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// Inspect the actual request-log webhook, not just a synthetic post-plugin.
func responseDropsLogger(t *testing.T) (*logging.Plugin, func(string)) {
	t.Helper()
	rows := make(chan map[string]string, 16)
	hook := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var batch struct {
			Logs []struct {
				Metadata map[string]string `json:"metadata"`
			} `json:"logs"`
		}
		if err := json.NewDecoder(r.Body).Decode(&batch); err != nil {
			t.Error(err)
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		for _, row := range batch.Logs {
			rows <- row.Metadata
		}
	}))
	t.Cleanup(hook.Close)
	logs := logging.New(config.RequestLoggingConfig{Enabled: true}, nil)
	flusher := logging.NewLogFlusher(hook.URL, "", time.Hour, 1)
	logs.SetFlusher(flusher)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		flusher.Run(ctx)
	}()
	t.Cleanup(func() {
		cancel()
		<-done
		logs.Close()
	})
	return logs, func(want string) {
		t.Helper()
		select {
		case row := <-rows:
			if got := row["translation_drops"]; got != want {
				t.Errorf("request log translation_drops = %q, want %q", got, want)
			}
		case <-time.After(5 * time.Second):
			t.Fatal("request log not delivered")
		}
	}
}

// Mirror calls have no independent request log or client response. Observe
// their provider warning without logging values from the shadow request.
type responsesWarningCapture struct {
	slog.Handler
	warnings chan slog.Record
}

func (h responsesWarningCapture) Handle(ctx context.Context, r slog.Record) error {
	if r.Message == "responses route drops chat-only parameters" {
		h.warnings <- r.Clone()
	}
	return h.Handler.Handle(ctx, r)
}

func captureResponsesWarning(t *testing.T) func(string) {
	t.Helper()
	old := slog.Default()
	records := make(chan slog.Record, 16)
	slog.SetDefault(slog.New(responsesWarningCapture{Handler: old.Handler(), warnings: records}))
	t.Cleanup(func() { slog.SetDefault(old) })
	return func(want string) {
		t.Helper()
		select {
		case record := <-records:
			if record.Level != slog.LevelWarn || record.NumAttrs() != 1 {
				t.Errorf("warning level/attrs = %v/%d", record.Level, record.NumAttrs())
			}
			record.Attrs(func(a slog.Attr) bool {
				if a.Key != "parameters" || fmt.Sprint(a.Value.Any()) != want {
					t.Errorf("warning contains unexpected data: %s=%v", a.Key, a.Value)
				}
				return true
			})
		case <-time.After(5 * time.Second):
			t.Fatal("provider drop warning absent")
		}
	}
}

const allResponsesOnlySettings = `"stop":["secret-stop\r\nvalue"],"seed":0,"logprobs":true,"top_logprobs":3,"frequency_penalty":0.5,"presence_penalty":-0.5,"logit_bias":{"50256":-100},"n":2,`
const allResponsesDropNotes = "stop_unsupported,seed_unsupported,logprobs_unsupported,top_logprobs_unsupported,frequency_penalty_unsupported,presence_penalty_unsupported,logit_bias_unsupported,n_unsupported"

// Decision B: causal replacement of direct refusal, through the real handler,
// provider dispatch and logging plugin. Expected red at 3db524c; not run here.
func TestResponsesDecisionBDirectDispatchAndRequestLog(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%t", stream), func(t *testing.T) {
			wantWarning := captureResponsesWarning(t)
			logs, wantLog := responseDropsLogger(t)
			srv, upstream := newResponsesBridgeServerWith(t, bridgeServerOptions{plugins: []pipeline.Plugin{logs}})
			w := postBridgeChat(t, srv, chatToolBody("gpt-5.6-terra", stream, allResponsesOnlySettings), map[string]string{
				"x-agentcc-translation-drops": "forged-secret",
				"x-agentcc-metadata": `{"translation_drops":"forged-secret"}`,
			})
			if w.Code != http.StatusOK {
				t.Fatalf("status=%d: %s", w.Code, w.Body.String())
			}
			if stream {
				checkToolStream(t, w)
			} else {
				checkToolCompletion(t, w.Body.Bytes())
			}
			checkHits(t, upstream, 0, 1)
			if _, leaked := sentToResponses(t, upstream); len(leaked) != 0 {
				t.Errorf("unsupported fields sent: %v", leaked)
			}
			if got := w.Result().Header.Get("x-agentcc-translation-drops"); got != allResponsesDropNotes {
				t.Errorf("wire header=%q, want fixed names only", got)
			}
			wantLog(allResponsesDropNotes)
			wantWarning("[stop seed logprobs top_logprobs frequency_penalty presence_penalty logit_bias n]")
		})
	}
}

func TestResponsesDecisionBNoFalseWarnings(t *testing.T) {
	for _, stream := range []bool{false, true} {
		for _, tc := range []struct {
			name   string
			extra  string
			plugin pipeline.Plugin
			status int
			chat   int
			resp   int
		}{
			{"reasoning none", allResponsesOnlySettings + `"reasoning_effort":"none",`, nil, 200, 1, 0},
			{"no-op settings", `"stop":[""],"frequency_penalty":0,"presence_penalty":0,"logprobs":false,"top_logprobs":0,"logit_bias":{"50256":0},"n":1,`, nil, 200, 0, 1},
			{"no settings", "", nil, 200, 0, 1},
			{"tools stripped", allResponsesOnlySettings, testRequestPlugin{stripTools: true}, 200, 1, 0},
			{"policy refuses", allResponsesOnlySettings, testRequestPlugin{refuse: true}, 403, 0, 0},
			{"invalid supported tool choice", allResponsesOnlySettings + `"tool_choice":"sometimes",`, nil, 400, 0, 0},
		} {
			t.Run(fmt.Sprintf("%s/stream=%t", tc.name, stream), func(t *testing.T) {
				logs, wantLog := responseDropsLogger(t)
				plugins := []pipeline.Plugin{logs}
				if tc.plugin != nil {
					plugins = append(plugins, tc.plugin)
				}
				srv, upstream := newResponsesBridgeServerWith(t, bridgeServerOptions{plugins: plugins})
				w := postBridgeChat(t, srv, chatToolBody("gpt-5.6-terra", stream, tc.extra), map[string]string{
					"x-agentcc-translation-drops": "forged-secret",
					"x-agentcc-metadata": `{"translation_drops":"forged-secret"}`,
				})
				if w.Code != tc.status {
					t.Fatalf("status=%d want %d: %s", w.Code, tc.status, w.Body.String())
				}
				checkHits(t, upstream, tc.chat, tc.resp)
				if got := w.Result().Header.Get("x-agentcc-translation-drops"); got != "" {
					t.Errorf("false warning %q", got)
				}
				wantLog("")
				if tc.name == "reasoning none" {
					var sent map[string]json.RawMessage
					_ = json.Unmarshal(upstream.body("/v1/chat/completions"), &sent)
					for _, key := range []string{"stop", "seed", "logprobs", "top_logprobs", "frequency_penalty", "presence_penalty", "logit_bias", "n"} {
						if _, ok := sent[key]; !ok {
							t.Errorf("normal chat lost %s", key)
						}
					}
				}
			})
		}
	}
}

// Admission and JSON validation still fail before any provider attempt.
func TestResponsesDecisionBAdmissionErrorsRemainErrors(t *testing.T) {
	for _, tc := range []struct {
		name      string
		body      string
		auth      string
		providers []string
		status    int
	}{
		{"missing auth", chatToolBody("gpt-5.6-terra", false, allResponsesOnlySettings), "", nil, 401},
		{"provider ACL", chatToolBody("gpt-5.6-terra", false, allResponsesOnlySettings), "Bearer synthetic-key", []string{"another-provider"}, 403},
		{"malformed JSON", `{"model":"gpt-5.6-terra",`, "Bearer synthetic-key", nil, 400},
		{"wrong supported field type", `{"model":"gpt-5.6-terra","temperature":"not-a-number","messages":[]}`, "Bearer synthetic-key", nil, 400},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv, upstream := newResponsesBridgeServerWith(t, bridgeServerOptions{configure: func(cfg *config.Config) {
				cfg.Auth.Enabled = true
				cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "synthetic", Key: "synthetic-key", KeyType: "byok", Providers: tc.providers}}
			}})
			w := postBridgeChat(t, srv, tc.body, map[string]string{"Authorization": tc.auth})
			if w.Code != tc.status {
				t.Fatalf("status=%d want %d: %s", w.Code, tc.status, w.Body.String())
			}
			checkHits(t, upstream, 0, 0)
			if got := w.Result().Header.Get("x-agentcc-translation-drops"); got != "" {
				t.Errorf("admission failure reported drops: %q", got)
			}
		})
	}
}

func TestResponsesDecisionBRateAndBudgetErrorsRemainErrors(t *testing.T) {
	for _, kind := range []string{"rate_limit_exceeded", "budget_exceeded"} {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%t", kind, stream), func(t *testing.T) {
				var gate pipeline.Plugin
				if kind == "rate_limit_exceeded" {
					limiter := ratelimit.NewRateLimiter()
					t.Cleanup(limiter.Stop)
					limiter.Allow("global", 1)
					gate = ratelimit.New(config.RateLimitConfig{Enabled: true, GlobalRPM: 1}, nil, limiter)
				} else {
					tracker := budgetpkg.NewTracker(config.BudgetsConfig{Enabled: true, DefaultPeriod: "monthly", Org: &config.BudgetLevelConfig{Limit: 1}})
					tracker.RecordSpend("", "", "", "gpt-5.6-terra", nil, 1)
					gate = budgetplugin.New(tracker, budgetplugin.NewPricingLookup(nil), true, nil)
				}
				logs, wantLog := responseDropsLogger(t)
				srv, upstream := newResponsesBridgeServerWith(t, bridgeServerOptions{plugins: []pipeline.Plugin{gate, logs}})
				w := postBridgeChat(t, srv, chatToolBody("gpt-5.6-terra", stream, allResponsesOnlySettings), nil)
				var got models.ErrorResponse
				if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil || w.Code != http.StatusTooManyRequests || got.Error.Code != kind {
					t.Fatalf("status=%d body=%s, want 429 %s", w.Code, w.Body.String(), kind)
				}
				checkHits(t, upstream, 0, 0)
				if drops := w.Result().Header.Get("x-agentcc-translation-drops"); drops != "" {
					t.Errorf("pre-provider rejection reported drops: %q", drops)
				}
				wantLog("")
			})
		}
	}
}

// A Responses failure followed by normal chat must clear that attempt's notes
// before either headers or the final request log are written.
func TestResponsesDecisionBFallbackClearsAttemptWarnings(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%t", stream), func(t *testing.T) {
			logs, wantLog := responseDropsLogger(t)
			srv, upstream := newResponsesBridgeServerWith(t, bridgeServerOptions{
				plugins: []pipeline.Plugin{logs},
				configure: func(cfg *config.Config) {
					cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 3, OnStatusCodes: []int{http.StatusBadGateway}}
					cfg.Routing.ModelFallbacks = map[string][]string{"gpt-5.6-terra": {"gpt-5.5"}}
				},
			})
			upstream.failResponses = true
			w := postBridgeChat(t, srv, chatToolBody("gpt-5.6-terra", stream, allResponsesOnlySettings), nil)
			if w.Code != http.StatusOK {
				t.Fatalf("status=%d: %s", w.Code, w.Body.String())
			}
			if got := w.Result().Header.Get("x-agentcc-translation-drops"); got != "" {
				t.Errorf("stale warning=%q", got)
			}
			checkHits(t, upstream, 1, 1)
			wantLog("")
		})
	}
}

func TestResponsesDecisionBTranslatedNotesPreserved(t *testing.T) {
	for _, dialect := range []string{"google", "anthropic"} {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%t", dialect, stream), func(t *testing.T) {
				logs, wantLog := responseDropsLogger(t)
				srv, upstream := newResponsesBridgeServerWith(t, bridgeServerOptions{plugins: []pipeline.Plugin{logs}})
				path := "/v1beta/models/gpt-5.6-terra:generateContent"
				body := `{"contents":[{"role":"user","parts":[{"text":"Hi"}]}],"tools":[{"functionDeclarations":[{"name":"get_weather","parameters":{"type":"object"}}]}],"generationConfig":{"topK":7,"stopSequences":["secret"]}}`
				if dialect == "google" && stream {
					path = "/v1beta/models/gpt-5.6-terra:streamGenerateContent?alt=sse"
				}
				if dialect == "anthropic" {
					path = "/v1/messages"
					body = fmt.Sprintf(`{"model":"gpt-5.6-terra","max_tokens":100,"stream":%t,"top_k":7,"stop_sequences":["secret"],"tools":[{"name":"get_weather","input_schema":{"type":"object"}}],"messages":[{"role":"user","content":"Hi"}]}`, stream)
				}
				req := httptest.NewRequest(http.MethodPost, path, bytes.NewBufferString(body))
				req.Header.Set("Content-Type", "application/json")
				w := httptest.NewRecorder()
				srv.httpServer.Handler.ServeHTTP(w, req)
				if w.Code != http.StatusOK {
					t.Fatalf("status=%d: %s", w.Code, w.Body.String())
				}
				want := "top_k_unsupported,stop"
				if got := w.Result().Header.Get("x-agentcc-translation-drops"); got != want {
					t.Errorf("header=%q, want %q", got, want)
				}
				checkHits(t, upstream, 0, 1)
				if _, leaked := sentToResponses(t, upstream); len(leaked) != 0 {
					t.Errorf("unsupported fields sent: %v", leaked)
				}
				wantLog(want)
			})
		}
	}
}

// Exercise repeated note bookkeeping on a real translated context. These
// simulated prior attempts do not dispatch; the final tools-stripped attempt
// must clear their notes but preserve the translator's original top_k note.
type responsesAttemptNotesPlugin struct {
	t        *testing.T
	provider providers.Provider
}

func (p *responsesAttemptNotesPlugin) Name() string  { return "attempt-notes-test" }
func (p *responsesAttemptNotesPlugin) Priority() int { return 10 }
func (p *responsesAttemptNotesPlugin) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	rc.Metadata["translation_drops"] = "forged-secret\r\nx-fake: yes"
	w := httptest.NewRecorder()
	for i := 0; i < 2; i++ {
		noteResponsesRouteDrops(w, rc, p.provider, rc.Request)
		if got := rc.Metadata["translation_drops"]; got != "top_k_unsupported,stop" {
			p.t.Errorf("attempt %d notes=%q", i, got)
		}
	}
	rc.Request.Tools = nil
	return pipeline.ResultContinue()
}
func (p *responsesAttemptNotesPlugin) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

func TestResponsesDecisionBAttemptNotesAreTrustedAndDeduplicated(t *testing.T) {
	logs, wantLog := responseDropsLogger(t)
	plugin := &responsesAttemptNotesPlugin{t: t}
	srv, upstream := newResponsesBridgeServerWith(t, bridgeServerOptions{plugins: []pipeline.Plugin{plugin, logs}})
	var ok bool
	plugin.provider, ok = srv.handlers.registry.GetProvider("openai")
	if !ok {
		t.Fatal("missing provider")
	}
	body := `{"contents":[{"role":"user","parts":[{"text":"Hi"}]}],"tools":[{"functionDeclarations":[{"name":"get_weather","parameters":{"type":"object"}}]}],"generationConfig":{"topK":7,"stopSequences":["secret"]}}`
	req := httptest.NewRequest(http.MethodPost, "/v1beta/models/gpt-5.6-terra:generateContent", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("status=%d: %s", w.Code, w.Body.String())
	}
	checkHits(t, upstream, 1, 0)
	if got := w.Result().Header.Get("x-agentcc-translation-drops"); got != "top_k_unsupported" {
		t.Errorf("translator note lost or stale provider notes kept: %q", got)
	}
	wantLog("top_k_unsupported")
}
