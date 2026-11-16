package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/leakage"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/pii"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/topic"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/validation"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// This file deliberately uses dev's public types only. The /legacy subtests
// can be copied unchanged to 575aaf3 for cloud characterization. Selected policy
// configuration is decoded from the wire, not required to compile on dev.
// Authored, not run: red/green outcomes below are source-derived expectations.
type runtimeR5Capture struct {
	stage guardrails.Stage
	mu    sync.Mutex
	views [][]byte
}

func (c *runtimeR5Capture) Name() string            { return "zz-capture" }
func (c *runtimeR5Capture) Stage() guardrails.Stage { return c.stage }
func (c *runtimeR5Capture) Check(_ context.Context, in *guardrails.CheckInput) *guardrails.CheckResult {
	var value interface{} = in.Request
	if c.stage == guardrails.StagePost {
		value = in.Response
	}
	raw, _ := json.Marshal(value)
	c.mu.Lock()
	c.views = append(c.views, raw)
	c.mu.Unlock()
	return &guardrails.CheckResult{Pass: true}
}

func (c *runtimeR5Capture) last(t *testing.T) []byte {
	t.Helper()
	c.mu.Lock()
	defer c.mu.Unlock()
	if len(c.views) == 0 {
		t.Fatal("legacy CheckInput was not observed")
	}
	return append([]byte(nil), c.views[len(c.views)-1]...)
}

type runtimeR5Fixture struct {
	srv     *Server
	gateway *httptest.Server
	mu      sync.Mutex
	bodies  [][]byte
}

func runtimeR5Factory(name string, cfg map[string]interface{}) guardrails.Guardrail {
	switch name {
	case "input-validation":
		return validation.New(cfg)
	case "pii-detection":
		return pii.New(cfg)
	case "topic-restriction":
		return topic.New(cfg)
	case "data-leakage-prevention":
		return leakage.New(cfg)
	}
	return nil
}

func newRuntimeR5Fixture(t *testing.T, gr *tenant.GuardrailConfig, reply string, capture *runtimeR5Capture, rules ...config.GuardrailRuleConfig) *runtimeR5Fixture {
	t.Helper()
	f := &runtimeR5Fixture{}
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			fmt.Fprint(w, `{"data":[],"models":[]}`)
			return
		}
		body, _ := io.ReadAll(r.Body)
		f.mu.Lock()
		f.bodies = append(f.bodies, body)
		f.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, reply)
	}))
	t.Cleanup(upstream.Close)
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "selected", Key: "sk-agentcc-r5-a", Owner: "synthetic", KeyType: "internal", Metadata: map[string]string{"org_id": "r5-org"}},
		{Name: "other", Key: "sk-agentcc-r5-b", Owner: "synthetic", KeyType: "internal", Metadata: map[string]string{"org_id": "r5-org"}},
	}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "synthetic", APIFormat: "openai", Models: []string{"gpt-5.5", "gpt-3.5-turbo-instruct"}}
	cfg.Providers["anthropic"] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "synthetic", APIFormat: "anthropic", Models: []string{"claude-sonnet-5-5"}}
	cfg.Providers["gemini"] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "synthetic", APIFormat: "gemini", Models: []string{"gemini-3.8-flash"}}
	cfg.MCP.Enabled, cfg.A2A.Enabled = true, true
	cfg.Guardrails = config.GuardrailsConfig{Enabled: true, DefaultTimeout: time.Second, Rules: rules}
	// main registers the built-in leakage check even without a static rule.
	// A legacy org check with nil Config cannot use the dynamic fallback, so
	// omitting this implementation silently turns its blocking control off.
	registry := map[string]guardrails.Guardrail{"data-leakage-prevention": leakage.New(nil)}
	for _, rule := range rules {
		registry[rule.Name] = runtimeR5Factory(rule.Name, rule.Config)
	}
	if capture != nil {
		registry[capture.Name()] = capture
		if gr.Checks == nil {
			gr.Checks = map[string]*tenant.GuardrailCheck{}
		}
		gr.Checks[capture.Name()] = &tenant.GuardrailCheck{Enabled: true, Action: "log"}
	}
	store := tenant.NewStore()
	store.Set("r5-org", &tenant.OrgConfig{Guardrails: gr})
	engine := guardrails.NewEngine(cfg.Guardrails, registry)
	plugin := guardrails.NewPlugin(engine, registry, runtimeR5Factory, nil, store)
	providers, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	keys := auth.NewKeyStore(cfg.Auth)
	f.srv = New(cfg, "", providers, pipeline.NewEngine(authplugin.New(keys, true), plugin), keys, engine, nil, nil, testModelDBPtr(), store, nil)
	f.srv.ready.Store(true)
	t.Cleanup(f.srv.asyncWorker.Stop)
	f.gateway = httptest.NewServer(f.srv.httpServer.Handler)
	t.Cleanup(f.gateway.Close)
	return f
}

func (f *runtimeR5Fixture) send(t *testing.T, method, path, body string, headers map[string]string) (int, []byte, [][]byte) {
	t.Helper()
	r, err := http.NewRequest(method, f.gateway.URL+path, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	r.Header.Set("Content-Type", "application/json")
	for key, value := range headers {
		r.Header.Set(key, value)
	}
	resp, err := f.gateway.Client().Do(r)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	return resp.StatusCode, raw, append([][]byte(nil), f.bodies...)
}

func runtimeR5Selected(t *testing.T, gr *tenant.GuardrailConfig, checks map[string]*tenant.GuardrailCheck) {
	t.Helper()
	raw, err := json.Marshal(map[string]interface{}{"key_policies": []interface{}{map[string]interface{}{
		"id": "r5-selected", "name": "r5-selected", "mode": "enforce", "key_ids": []string{"key_1"}, "checks": checks,
	}}})
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, gr); err != nil {
		t.Fatal(err)
	}
}

func runtimeR5EqualJSON(t *testing.T, got, want []byte) {
	t.Helper()
	var a, b interface{}
	if json.Unmarshal(got, &a) != nil || json.Unmarshal(want, &b) != nil || !reflect.DeepEqual(a, b) {
		t.Fatalf("JSON changed\ngot  %s\nwant %s", got, want)
	}
}

func TestRuntimeR5ValidationHTTP(t *testing.T) {
	for _, scope := range []string{"legacy-org", "legacy-static", "selected"} {
		for _, content := range []string{`""`, `[{"type":"text","text":""}]`, `[]`, `[{"type":"text","text":"ok"}]`} {
			t.Run(scope+"/"+content, func(t *testing.T) {
				check := &tenant.GuardrailCheck{Enabled: true, Action: "block", ConfidenceThreshold: .5, Config: map[string]interface{}{"min_message_length": 1}}
				gr := &tenant.GuardrailConfig{}
				var rules []config.GuardrailRuleConfig
				switch scope {
				case "legacy-org":
					gr.Checks = map[string]*tenant.GuardrailCheck{"input-validation": check}
				case "legacy-static":
					rules = []config.GuardrailRuleConfig{{Name: "input-validation", Stage: "pre", Action: "block", Threshold: .5, Config: check.Config}}
				default:
					runtimeR5Selected(t, gr, map[string]*tenant.GuardrailCheck{"input-validation": check})
				}
				f := newRuntimeR5Fixture(t, gr, `{"model":"gpt-5.5","choices":[{"index":0,"message":{"role":"assistant","content":"safe"}}]}`, nil, rules...)
				status, body, calls := f.send(t, "POST", "/v1/chat/completions", `{"model":"gpt-5.5","messages":[{"role":"user","content":`+content+`}]}`, map[string]string{"Authorization": "Bearer sk-agentcc-r5-a"})
				want, count := 200, 1
				if content == `""` || (scope == "selected" && !strings.Contains(content, "ok")) {
					want, count = 403, 0
				}
				if status != want || len(calls) != count || (want == 403 && !bytes.Contains(body, []byte("content_blocked"))) {
					t.Fatalf("status=%d want=%d calls=%d body=%s", status, want, len(calls), body)
				}
			})
		}
	}
}

func TestRuntimeR5CompletionOutputHTTP(t *testing.T) {
	for _, scope := range []string{"legacy-org", "legacy-static", "selected"} {
		for _, texts := range [][]string{{"safe reply", "stack trace"}, {"safe reply", "safe second"}, {"stack trace", "safe second"}, {}} {
			t.Run(scope+"/"+strings.Join(texts, ","), func(t *testing.T) {
				check := &tenant.GuardrailCheck{Enabled: true, Action: "block", ConfidenceThreshold: .5}
				gr := &tenant.GuardrailConfig{PipelineMode: "parallel"}
				var rules []config.GuardrailRuleConfig
				if scope == "legacy-org" {
					gr.Checks = map[string]*tenant.GuardrailCheck{"data-leakage-prevention": check}
				} else if scope == "legacy-static" {
					rules = []config.GuardrailRuleConfig{{Name: "data-leakage-prevention", Stage: "post", Action: "block", Threshold: .5}}
				} else {
					runtimeR5Selected(t, gr, map[string]*tenant.GuardrailCheck{"data-leakage-prevention": check})
				}
				resp := models.CompletionResponse{ID: "r5-completion", Object: "text_completion", Model: "gpt-3.5-turbo-instruct", Usage: &models.Usage{PromptTokens: 5, CompletionTokens: 3, TotalTokens: 8}}
				for i, text := range texts {
					resp.Choices = append(resp.Choices, models.CompletionChoice{Index: i + 4, Text: text, FinishReason: "stop"})
				}
				reply, _ := json.Marshal(resp)
				capture := &runtimeR5Capture{stage: guardrails.StagePost}
				f := newRuntimeR5Fixture(t, gr, string(reply), capture, rules...)
				status, body, calls := f.send(t, "POST", "/v1/completions", `{"model":"gpt-3.5-turbo-instruct","prompt":"safe","n":2}`, map[string]string{"Authorization": "Bearer sk-agentcc-r5-a"})
				want, code := 200, "content_blocked"
				if len(texts) > 0 && texts[0] == "stack trace" || scope == "selected" && strings.Contains(strings.Join(texts, " "), "stack trace") {
					want = 403
				}
				if scope == "selected" && len(texts) == 0 {
					want, code = 403, "output_guardrail_unsupported"
				}
				if status != want || len(calls) != 1 || (want == 403 && !bytes.Contains(body, []byte(code))) {
					t.Fatalf("status=%d want=%d calls=%d body=%s", status, want, len(calls), body)
				}
				if want == 200 {
					if !bytes.Equal(body, append(reply, '\n')) {
						t.Fatalf("native wire response changed: %s", body)
					}
					text, finish := "", ""
					if len(texts) > 0 {
						text, finish = texts[0], "stop"
					}
					content, _ := json.Marshal(text)
					wantView, _ := json.Marshal(&models.ChatCompletionResponse{ID: resp.ID, Object: "chat.completion", Model: resp.Model, Usage: resp.Usage, Choices: []models.Choice{{Index: 0, Message: models.Message{Role: "assistant", Content: content}, FinishReason: finish}}})
					runtimeR5EqualJSON(t, capture.last(t), wantView)
				}
			})
		}
	}
}

func TestRuntimeR5NativeRemediationHTTP(t *testing.T) {
	for _, route := range []struct{ name, path, body, reply string }{
		{"completion", "/v1/completions", `{"model":"gpt-3.5-turbo-instruct","prompt":"Contact sample@example.com"}`, `{"model":"gpt-3.5-turbo-instruct","choices":[{"index":0,"text":"safe reply"}]}`},
		{"responses", "/v1/responses", `{"model":"gpt-5.5","input":"Contact sample@example.com"}`, `{"model":"gpt-5.5","output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"safe reply"}]}]}`},
		{"anthropic", "/v1/messages", `{"model":"claude-sonnet-5-5","max_tokens":10,"messages":[{"role":"user","content":"Contact sample@example.com"}]}`, `{"model":"claude-sonnet-5-5","content":[{"type":"text","text":"safe reply"}]}`},
		{"genai", "/v1beta/models/gemini-3.8-flash:generateContent", `{"contents":[{"role":"user","parts":[{"text":"Contact sample@example.com"}]}]}`, `{"candidates":[{"content":{"role":"model","parts":[{"text":"safe reply"}]}}]}`},
	} {
		for _, scope := range []string{"legacy-org", "legacy-static", "selected-org", "selected-static"} {
			t.Run(scope+"/"+route.name, func(t *testing.T) {
				redact := map[string]interface{}{"remediation": "redact", "entities": []interface{}{"email"}}
				restrict := map[string]interface{}{"forbidden_topics": []interface{}{map[string]interface{}{"name": "address", "keywords": []interface{}{"sample@example.com"}}}}
				checks := map[string]*tenant.GuardrailCheck{
					"pii-detection":     {Enabled: true, Action: "log", ConfidenceThreshold: .5, Config: redact},
					"topic-restriction": {Enabled: true, Action: "block", ConfidenceThreshold: .5, Config: restrict},
				}
				gr := &tenant.GuardrailConfig{PipelineMode: "parallel"}
				var rules []config.GuardrailRuleConfig
				if strings.HasSuffix(scope, "org") {
					gr.Checks = checks
				} else {
					rules = []config.GuardrailRuleConfig{{Name: "pii-detection", Stage: "pre", Action: "log", Threshold: .5, Config: redact}, {Name: "topic-restriction", Stage: "pre", Action: "block", Threshold: .5, Config: restrict}}
				}
				if strings.HasPrefix(scope, "selected") {
					runtimeR5Selected(t, gr, map[string]*tenant.GuardrailCheck{"topic-restriction": checks["topic-restriction"]})
				}
				capture := &runtimeR5Capture{stage: guardrails.StagePre}
				f := newRuntimeR5Fixture(t, gr, route.reply, capture, rules...)
				status, body, calls := f.send(t, "POST", route.path, route.body, map[string]string{"Authorization": "Bearer sk-agentcc-r5-a"})
				want, count := 200, 1
				if strings.HasPrefix(scope, "selected") {
					want, count = 403, 0
				}
				if status != want || len(calls) != count || (want == 403 && !bytes.Contains(body, []byte("content_blocked"))) {
					t.Fatalf("status=%d want=%d calls=%d body=%s", status, want, len(calls), body)
				}
				var view models.ChatCompletionRequest
				if err := json.Unmarshal(capture.last(t), &view); err != nil {
					t.Fatal(err)
				}
				wantMessages, _ := json.Marshal([]models.Message{{Role: "user", Content: json.RawMessage(`"Contact [REDACTED:email]"`)}})
				gotMessages, _ := json.Marshal(view.Messages)
				runtimeR5EqualJSON(t, gotMessages, wantMessages)
				if count == 1 && !bytes.Contains(calls[0], []byte("sample@example.com")) {
					t.Fatal("legacy projection remediation changed the native wire body")
				}
			})
		}
	}
}

func TestRuntimeR5ResponsesEmptyHTTP(t *testing.T) {
	for _, scope := range []string{"legacy-org", "legacy-static", "selected"} {
		for _, input := range []string{`""`, `[{"role":"user","content":""}]`, `[{"type":"message","role":"user","content":[]}]`, `"hello"`} {
			t.Run(scope+"/"+input, func(t *testing.T) {
				check := &tenant.GuardrailCheck{Enabled: true, Action: "block", ConfidenceThreshold: .5, Config: map[string]interface{}{"min_message_length": 1}}
				gr := &tenant.GuardrailConfig{PipelineMode: "parallel"}
				var rules []config.GuardrailRuleConfig
				if scope == "legacy-org" {
					gr.Checks = map[string]*tenant.GuardrailCheck{"input-validation": check}
				} else if scope == "legacy-static" {
					rules = []config.GuardrailRuleConfig{{Name: "input-validation", Stage: "pre", Action: "block", Threshold: .5, Config: check.Config}}
				} else {
					runtimeR5Selected(t, gr, map[string]*tenant.GuardrailCheck{"input-validation": check})
				}
				capture := &runtimeR5Capture{stage: guardrails.StagePre}
				reply := `{"model":"gpt-5.5","output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"safe reply"}]}]}`
				f := newRuntimeR5Fixture(t, gr, reply, capture, rules...)
				status, body, calls := f.send(t, "POST", "/v1/responses", `{"model":"gpt-5.5","instructions":"Say hello","user":"synthetic","temperature":0.3,"input":`+input+`}`, map[string]string{"Authorization": "Bearer sk-agentcc-r5-a"})
				want, count := 200, 1
				if scope == "selected" && input != `"hello"` {
					want, count = 403, 0
				}
				if status != want || len(calls) != count || (want == 403 && !bytes.Contains(body, []byte("content_blocked"))) {
					t.Fatalf("status=%d want=%d calls=%d body=%s", status, want, len(calls), body)
				}
				messages := []models.Message{{Role: "system", Content: json.RawMessage(`"Say hello"`)}}
				if input == `"hello"` {
					messages = append(messages, models.Message{Role: "user", Content: json.RawMessage(input)})
				}
				wantView, _ := json.Marshal(&models.ChatCompletionRequest{Model: "gpt-5.5", Messages: messages})
				runtimeR5EqualJSON(t, capture.last(t), wantView)
				if count == 1 && string(body) != reply {
					t.Fatal("native response bytes changed")
				}
			})
		}
	}
}
