package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/cache"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/blocklist"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/pii"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	budgetplugin "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	cacheplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cache"
	quotaplugin "github.com/futureagi/agentcc-gateway/internal/plugins/quota"
	rbacplugin "github.com/futureagi/agentcc-gateway/internal/plugins/rbac"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A response from the cache is served only to a request the checks on the
// request pass under the policy in force for it: the checks on the request
// run before the cache is read, so an entry stored under another policy, a
// semantic hit and a hit on a caller-chosen cache key are each served only
// to a request the checks pass. A check on the answer runs on a hit too.
//
// The probe is an org with the response cache on, a loopback provider that
// numbers its answers, the real auth, guardrail and cache plugins at their
// own priorities, and keyword-blocklist ("project bluebird") as the
// org's check or as a rule in the config file.

const (
	cacheOrderOrg         = "org-cache-order"
	cacheOrderKey         = "sk-agentcc-cache-order"
	cacheOrderOverrideKey = "sk-agentcc-cache-order-override"
	cacheOrderWord        = "project bluebird"
)

// cacheOrderCheck is the org's keyword-blocklist in block mode.
var cacheOrderCheck = map[string]*tenant.GuardrailCheck{
	"keyword-blocklist": {Enabled: true, Action: "block", Config: map[string]any{"words": []any{cacheOrderWord}}},
}

type cacheOrderGateway struct {
	srv     *Server
	tenants *tenant.Store
	calls   *atomic.Int64
	sent    *atomic.Value // the last body the provider got
	checked *atomic.Int64 // how many times keyword-blocklist read a request
}

// cacheOrderSetup: with configRule the config file has keyword-blocklist as a
// pre-stage rule in block mode; checks are the org's checks to start with;
// exactOnly leaves out the semantic layer; with quota the request quota
// plugin, reading that Redis, is in the pipeline too.
type cacheOrderSetup struct {
	configRule bool
	checks     map[string]*tenant.GuardrailCheck
	exactOnly  bool
	quota      quotaplugin.RedisClient
	// answer is the text the provider answers with (numbered, as its
	// answers are); answerRule is a check on the answer, a rule in the
	// config file at the post stage, run by answerCheck.
	answer      string
	answerRule  *config.GuardrailRuleConfig
	answerCheck guardrails.Guardrail
	// disk stores the cache's entries on disk, which writes an answer when
	// it is stored (as the Redis and object-store backends do), where the
	// in-memory store keeps the answer itself.
	disk bool
}

func newCacheOrderGateway(t *testing.T, s cacheOrderSetup) *cacheOrderGateway {
	t.Helper()
	g := &cacheOrderGateway{calls: &atomic.Int64{}, sent: &atomic.Value{}, checked: &atomic.Int64{}}
	g.sent.Store("")
	// A provider that numbers its answers and keeps the last body it got.
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/chat/completions" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		var body bytes.Buffer
		_, _ = body.ReadFrom(r.Body)
		g.sent.Store(body.String())
		n := g.calls.Add(1)
		content := fmt.Sprintf("upstream answer %d", n)
		if s.answer != "" {
			content = fmt.Sprintf("%s (answer %d)", s.answer, n)
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"id": fmt.Sprintf("chatcmpl-%d", n), "object": "chat.completion", "created": 1700000000, "model": "gpt-4o",
			"choices": []any{map[string]any{"index": 0, "finish_reason": "stop",
				"message": map[string]string{"role": "assistant", "content": content}}},
			"usage": map[string]int{"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
		})
	}))
	t.Cleanup(upstream.Close)

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "cache-order", Key: cacheOrderKey, Owner: "o", KeyType: "internal", Metadata: map[string]string{"org_id": cacheOrderOrg}},
		{Name: "cache-order-override", Key: cacheOrderOverrideKey, Owner: "o", KeyType: "internal",
			Metadata: map[string]string{"org_id": cacheOrderOrg, "allow_policy_override": "true"}},
	}
	cfg.Guardrails = config.GuardrailsConfig{Enabled: true, DefaultTimeout: 2 * time.Second}
	if s.configRule {
		cfg.Guardrails.Rules = []config.GuardrailRuleConfig{
			{Name: "keyword-blocklist", Stage: "pre", Mode: "sync", Action: "block"},
		}
	}
	if s.answerRule != nil {
		cfg.Guardrails.Rules = append(cfg.Guardrails.Rules, *s.answerRule)
	}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "dummy", APIFormat: "openai", Models: []string{"gpt-4o"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}

	grRegistry := map[string]guardrails.Guardrail{
		"keyword-blocklist": countingCheck{blocklist.New(map[string]interface{}{"words": []interface{}{cacheOrderWord}}), g.checked},
		"pii-detection":     pii.New(nil),
	}
	if s.answerRule != nil {
		grRegistry[s.answerRule.Name] = s.answerCheck
	}
	factory := func(name string, cfg map[string]interface{}) guardrails.Guardrail {
		switch name {
		case "keyword-blocklist":
			return countingCheck{blocklist.New(cfg), g.checked}
		case "pii-detection":
			return pii.New(cfg)
		}
		return nil
	}
	grEngine := guardrails.NewEngine(cfg.Guardrails, grRegistry)
	g.tenants = tenant.NewStore()
	g.setChecks(s.checks)
	policies := policy.NewStore()

	cachePlugin := cacheplugin.NewFromStore(cache.NewStore(1000), 5*time.Minute)
	if s.disk {
		backend, err := cache.NewDiskBackend(t.TempDir(), 0, false, 0)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(backend.Close)
		cachePlugin = cacheplugin.New(backend, 5*time.Minute)
	}
	if !s.exactOnly {
		cachePlugin.SetSemanticStoreFromConcrete(cache.NewSemanticStore(0.8, 256, 1000))
	}

	ks := auth.NewKeyStore(cfg.Auth)
	plugins := []pipeline.Plugin{
		authplugin.New(ks, true),
		guardrails.NewPlugin(grEngine, grRegistry, factory, policies, g.tenants),
		cachePlugin,
	}
	if s.quota != nil {
		plugins = append(plugins, quotaplugin.New(s.quota, true))
	}
	engine := pipeline.NewEngine(plugins...)
	g.srv = New(cfg, "", registry, engine, ks, grEngine, policies, nil, testModelDBPtr(), g.tenants, nil)
	g.srv.ready.Store(true)
	return g
}

// countingCheck counts the requests a check reads.
type countingCheck struct {
	guardrails.Guardrail
	n *atomic.Int64
}

func (c countingCheck) Check(ctx context.Context, in *guardrails.CheckInput) *guardrails.CheckResult {
	c.n.Add(1)
	return c.Guardrail.Check(ctx, in)
}

// setChecks sets the org's checks (none when checks is nil), with the
// response cache on.
func (g *cacheOrderGateway) setChecks(checks map[string]*tenant.GuardrailCheck) {
	org := &tenant.OrgConfig{Cache: &tenant.CacheConfig{Enabled: true}}
	if checks != nil {
		org.Guardrails = &tenant.GuardrailConfig{Checks: checks}
	}
	g.tenants.Set(cacheOrderOrg, org)
}

type cacheOrderCall struct {
	status  int
	cache   string // X-Agentcc-Cache-Status
	content string
	body    string
}

func (g *cacheOrderGateway) chat(t *testing.T, key, prompt string, headers map[string]string) cacheOrderCall {
	t.Helper()
	body, _ := json.Marshal(map[string]any{"model": "gpt-4o", "messages": []map[string]string{{"role": "user", "content": prompt}}})
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+key)
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	out := cacheOrderCall{status: w.Code, cache: w.Header().Get(cacheplugin.HeaderCacheStatus), body: w.Body.String()}
	var resp models.ChatCompletionResponse
	if json.Unmarshal(w.Body.Bytes(), &resp) == nil && len(resp.Choices) > 0 {
		_ = json.Unmarshal(resp.Choices[0].Message.Content, &out.content)
	}
	return out
}

// assertBlockedNotServed: the request got the guardrail's 403, no answer
// from the cache, and the provider was not called again.
func (g *cacheOrderGateway) assertBlockedNotServed(t *testing.T, c cacheOrderCall, wantCalls int64) {
	t.Helper()
	if c.status != http.StatusForbidden || !strings.Contains(c.body, "content_blocked") {
		t.Errorf("got %d %q (cache %q), want the guardrail's 403 content_blocked", c.status, c.body, c.cache)
	}
	if c.content != "" {
		t.Errorf("the request was served the cached answer %q", c.content)
	}
	if c.cache != "" {
		t.Errorf("X-Agentcc-Cache-Status %q on a request the checks refused before the cache", c.cache)
	}
	if n := g.calls.Load(); n != wantCalls {
		t.Errorf("provider called %d times, want %d", n, wantCalls)
	}
}

const cacheOrderPrompt = "Please write a short status update about " + cacheOrderWord + " for the weekly team meeting notes."

// An entry stored before the org turned a check on is not served to a
// request that check blocks; a request it passes is still served from the
// cache.
func TestCache_HitIsCheckedByTheCurrentPolicy(t *testing.T) {
	g := newCacheOrderGateway(t, cacheOrderSetup{})
	first := g.chat(t, cacheOrderKey, cacheOrderPrompt, nil)
	if first.status != http.StatusOK || first.content != "upstream answer 1" {
		t.Fatalf("first call: %+v", first)
	}
	if again := g.chat(t, cacheOrderKey, cacheOrderPrompt, nil); again.cache != "HIT" || again.content != first.content || g.calls.Load() != 1 {
		t.Fatalf("the repeat is not a hit: %+v (provider calls %d)", again, g.calls.Load())
	}

	g.setChecks(cacheOrderCheck)
	g.assertBlockedNotServed(t, g.chat(t, cacheOrderKey, cacheOrderPrompt, nil), 1)

	// Control: a request the check passes is served from the cache.
	const benign = "What time does the cafeteria open on Mondays?"
	b1 := g.chat(t, cacheOrderKey, benign, nil)
	b2 := g.chat(t, cacheOrderKey, benign, nil)
	if b1.status != http.StatusOK || b2.status != http.StatusOK || b2.cache != "HIT" || b2.content != b1.content || g.calls.Load() != 2 {
		t.Errorf("a passing request is not served from the cache: %+v then %+v (provider calls %d)", b1, b2, g.calls.Load())
	}
}

// An entry stored under a key whose X-Guardrail-Policy turned the checks
// off is not served to a request the config file's rule blocks.
func TestCache_HitStoredWithChecksOffIsNotServedWithChecksOn(t *testing.T) {
	g := newCacheOrderGateway(t, cacheOrderSetup{configRule: true})
	off := g.chat(t, cacheOrderOverrideKey, cacheOrderPrompt, map[string]string{"X-Guardrail-Policy": "disabled"})
	if off.status != http.StatusOK || g.calls.Load() != 1 {
		t.Fatalf("with the checks off: %+v (provider calls %d)", off, g.calls.Load())
	}
	g.assertBlockedNotServed(t, g.chat(t, cacheOrderKey, cacheOrderPrompt, nil), 1)
}

// Under one policy all along: a semantic hit (a request like a stored one)
// and a hit on a caller-chosen cache key are not served to a request that
// holds a value the check blocks.
func TestCache_HitIsNotServedToARequestTheChecksBlock(t *testing.T) {
	const stored = "Please write a short status update about the quarterly roadmap for the weekly team meeting notes today."
	for _, tc := range []struct {
		name    string
		prompt  string
		headers map[string]string
	}{
		{"a semantic hit", stored + " " + cacheOrderWord, nil},
		{"a caller-chosen cache key", "Tell me everything about " + cacheOrderWord + ".", map[string]string{cacheplugin.HeaderCacheKey: "weekly-notes"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g := newCacheOrderGateway(t, cacheOrderSetup{checks: cacheOrderCheck})
			first := g.chat(t, cacheOrderKey, stored, tc.headers)
			if first.status != http.StatusOK || g.calls.Load() != 1 {
				t.Fatalf("first call: %+v (provider calls %d)", first, g.calls.Load())
			}
			g.assertBlockedNotServed(t, g.chat(t, cacheOrderKey, tc.prompt, tc.headers), 1)
		})
	}
}

// The checks read the request before the cache does, so a request a check
// rewrites (pii-detection's mask) is looked up and stored as the provider
// gets it: two requests that differ only in the value the check masks are
// one exact entry, and the cache never keys an entry by the value. (Exact
// layer only: a semantic hit would join them whatever the order.)
func TestCache_EntryIsKeyedByTheRequestAsTheChecksLeftIt(t *testing.T) {
	g := newCacheOrderGateway(t, cacheOrderSetup{exactOnly: true, checks: map[string]*tenant.GuardrailCheck{
		"pii-detection": {Enabled: true, Action: "log", Config: map[string]any{"remediation": "mask"}},
	}})
	first := g.chat(t, cacheOrderKey, "My SSN is 123-45-6789. Which form do I file it on?", nil)
	if first.status != http.StatusOK || first.cache != "MISS" || strings.Contains(g.sent.Load().(string), "123-45-6789") {
		t.Fatalf("first call: %+v, provider got %s", first, g.sent.Load())
	}
	second := g.chat(t, cacheOrderKey, "My SSN is 234-56-7890. Which form do I file it on?", nil)
	if second.status != http.StatusOK || second.cache != "HIT" || second.content != first.content || g.calls.Load() != 1 {
		t.Errorf("the second request, the same once masked, is not a hit on the first's entry: %+v (provider calls %d)", second, g.calls.Load())
	}
}

// The guardrails run after auth and RBAC, whose key identity and permissions
// they read, and after the request quota, which refuses a paused or
// over-limit org without reading its requests; and before the response cache
// and the budget. The pipeline runs its plugins in this order and stops at
// the first refusal.
func TestPluginOrder_GuardrailsRunBeforeTheCache(t *testing.T) {
	gr := guardrails.NewPlugin(nil, nil, nil, nil, nil).Priority()
	for _, before := range []struct {
		name     string
		priority int
	}{
		{"auth", authplugin.New(auth.NewKeyStore(config.AuthConfig{}), true).Priority()},
		{"rbac", rbacplugin.New(nil, false).Priority()},
		{"quota", quotaplugin.New(nil, false).Priority()},
	} {
		if before.priority >= gr {
			t.Errorf("%s (%d) does not run before the guardrails (%d)", before.name, before.priority, gr)
		}
	}
	for _, after := range []struct {
		name     string
		priority int
	}{
		{"cache", cacheplugin.NewFromStore(cache.NewStore(1), time.Minute).Priority()},
		{"budget", budgetplugin.New(nil, nil, false, nil).Priority()},
	} {
		if after.priority <= gr {
			t.Errorf("%s (%d) does not run after the guardrails (%d)", after.name, after.priority, gr)
		}
	}

	// On the answer too: the pipeline runs its sequential post-plugins in
	// ascending PostPriority (Priority unless the plugin sets it), so the
	// checks on the answer run before the cache stores it and before the
	// budget records its cost. An answer a check masks is stored masked, and
	// one a check withholds is not stored.
	order := []struct {
		name   string
		plugin pipeline.Plugin
	}{
		{"guardrails", guardrails.NewPlugin(nil, nil, nil, nil, nil)},
		{"cache", cacheplugin.NewFromStore(cache.NewStore(1), time.Minute)},
		{"budget", budgetplugin.New(nil, nil, false, nil)},
	}
	for i, p := range order {
		if pp, ok := p.plugin.(pipeline.PostParallel); ok && pp.IsPostParallel() {
			t.Errorf("%s runs its post-processing in parallel, out of order", p.name)
		}
		if i == 0 {
			continue
		}
		if prev, cur := postPriorityOf(order[i-1].plugin), postPriorityOf(p.plugin); prev >= cur {
			t.Errorf("on the answer %s (%d) does not run before %s (%d)", order[i-1].name, prev, p.name, cur)
		}
	}
}

// postPriorityOf is where the pipeline runs p's post-processing: its
// PostPriority, else its Priority.
func postPriorityOf(p pipeline.Plugin) int {
	if po, ok := p.(pipeline.PostOrdered); ok {
		return po.PostPriority()
	}
	return p.Priority()
}
