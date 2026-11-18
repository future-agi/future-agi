package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/blocklist"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/secrets"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Observe the production plugin's actual pre-stage outcomes, including when
// it short-circuits; no checker, policy resolution or failure result is mocked.
type keywordR2Observer struct {
	*guardrails.GuardrailPlugin
	mu      sync.Mutex
	results []models.GuardrailResult
}

func (p *keywordR2Observer) ProcessRequest(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	result := p.GuardrailPlugin.ProcessRequest(ctx, rc)
	p.mu.Lock()
	p.results = append([]models.GuardrailResult(nil), rc.GuardrailResults...)
	p.mu.Unlock()
	return result
}
func (p *keywordR2Observer) snapshot() []models.GuardrailResult {
	p.mu.Lock()
	defer p.mu.Unlock()
	return append([]models.GuardrailResult(nil), p.results...)
}

type keywordR2Fixture struct {
	gateway  *httptest.Server
	observer *keywordR2Observer
	mu       sync.Mutex
	bodies   [][]byte
}

func newKeywordR2Fixture(t *testing.T) *keywordR2Fixture {
	t.Helper()
	f := &keywordR2Fixture{}
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			fmt.Fprint(w, `{"data":[]}`)
			return
		}
		body, _ := io.ReadAll(r.Body)
		f.mu.Lock()
		f.bodies = append(f.bodies, body)
		f.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"id":"synthetic","object":"chat.completion","model":"gpt-5.5","choices":[{"index":0,"message":{"role":"assistant","content":"safe reply"},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}`)
	}))
	t.Cleanup(up.Close)
	cfg := config.DefaultConfig()
	cfg.Admin.Token = "synthetic-keyword-admin"
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "selected", Key: "sk-agentcc-keyword-selected", Owner: "synthetic", KeyType: "internal", Metadata: map[string]string{"org_id": "keyword-org"}},
		{Name: "unselected", Key: "sk-agentcc-keyword-unselected", Owner: "synthetic", KeyType: "internal", Metadata: map[string]string{"org_id": "keyword-org"}},
		{Name: "other-org", Key: "sk-agentcc-keyword-other", Owner: "synthetic", KeyType: "internal", Metadata: map[string]string{"org_id": "other-org"}},
	}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "synthetic", APIFormat: "openai", Models: []string{"gpt-5.5"}}
	cfg.Guardrails = config.GuardrailsConfig{Enabled: true, DefaultTimeout: time.Second}
	store := tenant.NewStore()
	registry := map[string]guardrails.Guardrail{"deployed-custom": secrets.New(nil)}
	engine := guardrails.NewEngine(cfg.Guardrails, registry)
	factory := func(name string, config map[string]interface{}) guardrails.Guardrail {
		// Same concrete constructor used by cmd/agentcc/main.go.
		if name == "keyword-blocklist" {
			return blocklist.New(config)
		}
		return nil
	}
	gr := guardrails.NewPlugin(engine, registry, factory, nil, store)
	f.observer = &keywordR2Observer{GuardrailPlugin: gr}
	pr, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	keys := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", pr, pipeline.NewEngine(authplugin.New(keys, true), f.observer), keys, engine, nil, nil, testModelDBPtr(), store, gr.InvalidateDynamicCache)
	srv.ready.Store(true)
	f.gateway = httptest.NewServer(srv.httpServer.Handler)
	t.Cleanup(f.gateway.Close)
	return f
}
func (f *keywordR2Fixture) call(t *testing.T, method, path, key string, payload interface{}) (int, []byte) {
	t.Helper()
	body, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	req, err := http.NewRequest(method, f.gateway.URL+path, bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+key)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	return resp.StatusCode, data
}
func (f *keywordR2Fixture) push(t *testing.T, config map[string]interface{}, mode, action string, failOpen, enabled, selected bool, pipelineMode string) {
	t.Helper()
	checks := map[string]interface{}{"keyword-blocklist": map[string]interface{}{"enabled": enabled, "action": action, "confidence_threshold": 0.5, "config": config}}
	gr := map[string]interface{}{"checks": map[string]interface{}{}, "fail_open": failOpen, "timeout_ms": 1000, "pipeline_mode": pipelineMode}
	if selected {
		gr["key_policies"] = []interface{}{map[string]interface{}{"id": "keyword-policy", "name": "synthetic-keyword", "mode": mode, "priority": 1, "key_ids": []string{"key_1"}, "checks": checks}}
	} else {
		gr["checks"] = checks
	}
	status, body := f.call(t, http.MethodPut, "/-/orgs/keyword-org/config", "synthetic-keyword-admin", map[string]interface{}{"guardrails": gr})
	if status != 200 {
		t.Fatalf("config push status=%d body=%s", status, body)
	}
}
func (f *keywordR2Fixture) send(t *testing.T, key, prompt string) (int, []byte) {
	return f.call(t, http.MethodPost, "/v1/chat/completions", key, map[string]interface{}{"model": "gpt-5.5", "messages": []interface{}{map[string]interface{}{"role": "user", "content": prompt}}})
}
func (f *keywordR2Fixture) count() int { f.mu.Lock(); defer f.mu.Unlock(); return len(f.bodies) }

func TestKeywordConfigR2PushedInvalidNeverReportsSuccess(t *testing.T) {
	bad := []struct {
		name string
		cfg  map[string]interface{}
	}{
		{"string", map[string]interface{}{"words": "restricted"}},
		{"missing", map[string]interface{}{}},
		{"null", map[string]interface{}{"words": nil}},
		{"empty", map[string]interface{}{"words": []interface{}{}}},
		{"whitespace", map[string]interface{}{"words": []interface{}{" \t"}}},
		{"mixed", map[string]interface{}{"words": []interface{}{"restricted", 42}}},
	}
	for _, tc := range bad {
		for _, mode := range []string{"enforce", "monitor"} {
			for _, action := range []string{"block", "log"} {
				for _, failOpen := range []bool{false, true} {
					t.Run(fmt.Sprintf("%s/%s/%s/open=%v", tc.name, mode, action, failOpen), func(t *testing.T) {
						f := newKeywordR2Fixture(t)
						for _, pipelineMode := range []string{"sequential", "parallel"} {
							f.push(t, tc.cfg, mode, action, failOpen, true, true, pipelineMode)
							for _, prompt := range []string{"restricted", "clean text"} {
								before := f.count()
								status, body := f.send(t, "sk-agentcc-keyword-selected", prompt)
								want, calls := 200, 1
								if mode == "enforce" && action == "block" && !failOpen {
									want, calls = 503, 0
								}
								if status != want || f.count()-before != calls {
									t.Fatalf("pipeline=%s prompt=%q status=%d want=%d upstream=%d want=%d response=%s", pipelineMode, prompt, status, want, f.count()-before, calls, body)
								}
								if want == 503 && !bytes.Contains(body, []byte("guardrail_unavailable")) {
									t.Fatalf("wrong error envelope: %s", body)
								}
								if calls == 1 {
									f.mu.Lock()
									wire := append([]byte(nil), f.bodies[len(f.bodies)-1]...)
									f.mu.Unlock()
									var forwarded models.ChatCompletionRequest
									if err := json.Unmarshal(wire, &forwarded); err != nil || len(forwarded.Messages) != 1 {
										t.Fatalf("unexpected forwarded wire: %s", wire)
									}
									var text string
									if err := json.Unmarshal(forwarded.Messages[0].Content, &text); err != nil || text != prompt {
										t.Fatalf("forwarded text changed: %s", wire)
									}
									t.Logf("upstream_bytes=%d text_unchanged=true", len(wire))
								}
								results := f.observer.snapshot()
								if len(results) != 1 || results[0].PolicyID != "keyword-policy" || !strings.Contains(results[0].Message, "guardrail_unavailable") {
									t.Fatalf("missing unavailable attribution: %+v", results)
								}
								if strings.Contains(results[0].Message, "restricted") {
									t.Fatal("invalid config content leaked in attribution")
								}
								t.Logf("pipeline=%s mode=%s action=%s fail_open=%v input=%q status=%d upstream_calls=%d outcome=%s", pipelineMode, mode, action, failOpen, prompt, status, calls, results[0].Outcome)
							}
						}
						for _, key := range []string{"sk-agentcc-keyword-unselected", "sk-agentcc-keyword-other"} {
							before := f.count()
							status, body := f.send(t, key, "restricted")
							if status != 200 || f.count() != before+1 || len(f.observer.snapshot()) != 0 {
								t.Fatalf("isolation control failed: status=%d body=%s", status, body)
							}
						}
					})
				}
			}
		}
	}
}

func TestKeywordConfigR2ValidCorrectionDisabledAndLegacyOrgControls(t *testing.T) {
	f := newKeywordR2Fixture(t)
	f.push(t, map[string]interface{}{"words": []interface{}{"restricted"}}, "enforce", "block", false, true, true, "sequential")
	for _, prompt := range []string{"RESTRICTED", "clean text"} {
		before := f.count()
		status, body := f.send(t, "sk-agentcc-keyword-selected", prompt)
		want, calls := 403, 0
		if prompt == "clean text" {
			want, calls = 200, 1
		}
		if status != want || f.count()-before != calls {
			t.Fatalf("valid words status=%d calls=%d body=%s", status, f.count()-before, body)
		}
	}
	// Updating to bad config must invalidate the prior usable checker; correcting
	// it again must recover, rather than retain a cached unavailable wrapper.
	f.push(t, map[string]interface{}{"words": "restricted"}, "enforce", "block", false, true, true, "parallel")
	status, body := f.send(t, "sk-agentcc-keyword-selected", "restricted")
	if status != 503 {
		t.Fatalf("bad update status=%d body=%s", status, body)
	}
	f.push(t, map[string]interface{}{"words": []interface{}{"restricted"}}, "enforce", "block", false, true, true, "parallel")
	status, body = f.send(t, "sk-agentcc-keyword-selected", "restricted")
	if status != 403 {
		t.Fatalf("corrected update status=%d body=%s", status, body)
	}
	for _, selected := range []bool{true, false} {
		// Disabled selected bad config and the existing empty org-check behavior
		// are controls, not scope expansion to org-wide validation.
		f.push(t, map[string]interface{}{"words": "restricted"}, "enforce", "block", false, !selected, selected, "sequential")
		before := f.count()
		status, body = f.send(t, "sk-agentcc-keyword-selected", "restricted")
		if status != 200 || f.count() != before+1 || len(f.observer.snapshot()) != 0 {
			t.Fatalf("disabled/org control selected=%v status=%d body=%s", selected, status, body)
		}
	}
}

func TestKeywordConfigR2CustomStaticCheckStillResolves(t *testing.T) {
	f := newKeywordR2Fixture(t)
	payload := map[string]interface{}{"guardrails": map[string]interface{}{"checks": map[string]interface{}{}, "fail_open": false, "key_policies": []interface{}{map[string]interface{}{"id": "custom-policy", "name": "custom", "mode": "enforce", "key_ids": []string{"key_1"}, "checks": map[string]interface{}{"deployed-custom": map[string]interface{}{"enabled": true, "action": "block", "confidence_threshold": 0.5, "config": map[string]interface{}{"words": "custom-contract"}}}}}}}
	status, body := f.call(t, http.MethodPut, "/-/orgs/keyword-org/config", "synthetic-keyword-admin", payload)
	if status != 200 {
		t.Fatalf("custom push status=%d body=%s", status, body)
	}
	status, body = f.send(t, "sk-agentcc-keyword-selected", "deploy with "+awsKey)
	if status != 403 || f.count() != 0 {
		t.Fatalf("custom resolution status=%d calls=%d body=%s", status, f.count(), body)
	}
}
