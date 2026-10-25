package server

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"go/ast"
	"go/parser"
	"go/token"
	"math"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/routing/eligibility"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// eligGateway serves an org's BYOK key (sk-elig, org org-e) through a
// managed OpenRouter provider ("openrouter"), an optional managed direct
// price-card source ("zz-direct", sorting after openrouter), and the
// operator's non-managed provider ("operator", gpt-4o), each on a
// body-recording upstream.
type eligGateway struct {
	srv      *Server
	tenants  *tenant.Store
	gens     chan *models.TraceSnapshot
	or       *hookUpstream
	direct   *hookUpstream
	operator *hookUpstream
}

const (
	eligOrg = "org-e"
	eligKey = "sk-elig"
)

type eligOpts struct {
	org          *tenant.OrgConfig
	keyModels    []string
	keyGroups    string                    // the key's access groups, comma separated
	groups       config.AccessGroupsConfig // the node's access groups
	orModels     map[string]*catalog.Model
	directModels map[string]*catalog.Model // nil: no direct source
	orFails      int32
	// thoughtSigModels is catalog.thought_signature_models (nil: unset).
	thoughtSigModels []string
}

func newEligGateway(t *testing.T, o eligOpts) *eligGateway {
	t.Helper()
	g := &eligGateway{or: startHookUpstream(t, o.orFails), direct: startHookUpstream(t, 0), operator: startHookUpstream(t, 0),
		gens: make(chan *models.TraceSnapshot, 16)}
	off := false
	cfg := config.DefaultConfig()
	cfg.Catalog.ThoughtSignatureModels = o.thoughtSigModels
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "elig", Key: eligKey, Owner: "o", KeyType: "byok", Models: o.keyModels,
		Metadata: map[string]string{"org_id": eligOrg}}}
	if o.keyGroups != "" {
		cfg.Auth.Keys[0].Metadata["access_groups"] = o.keyGroups
		cfg.Routing.AccessGroups = o.groups
	}
	cfg.Providers["openrouter"] = config.ProviderConfig{BaseURL: g.or.URL, APIKey: "fagi-or", APIFormat: "openai",
		Type: "openrouter", Managed: true, AutoDiscover: &off}
	cfg.Providers["operator"] = config.ProviderConfig{BaseURL: g.operator.URL, APIKey: "fagi-op", APIFormat: "openai",
		Models: []string{"gpt-4o"}, AutoDiscover: &off}
	if o.directModels != nil {
		cfg.Providers["zz-direct"] = config.ProviderConfig{BaseURL: g.direct.URL, APIKey: "fagi-direct", APIFormat: "openai",
			Managed: true, AutoDiscover: &off}
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("registry: %v", err)
	}
	store := catalog.NewStore()
	store.Put(&catalog.Snapshot{ProviderID: "openrouter", Source: "bundled", CatalogSource: catalog.SourceOpenRouter, FetchedAt: time.Now(), Models: o.orModels})
	if o.directModels != nil {
		store.Put(&catalog.Snapshot{ProviderID: "zz-direct", Source: "bundled", CatalogSource: catalog.SourcePriceCard, FetchedAt: time.Now(), Models: o.directModels})
	}
	var mdb atomic.Pointer[modeldb.ModelDB]
	mdb.Store(BuildModelDB(nil, store))
	g.tenants = tenant.NewStore()
	g.tenants.Set(eligOrg, o.org)
	keyStore := auth.NewKeyStore(cfg.Auth)
	ledger := creditsplugin.NewOrgLedger(nil)
	ledger.SetMicros(eligOrg, auth.USDToMicros(50))
	cost := costplugin.New(true, mdb.Load, g.tenants, nil)
	credits := creditsplugin.New(true, keyStore)
	credits.SetOrgLedger(ledger, true)
	g.srv = New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keyStore, true), cost, credits), keyStore, nil, nil, nil, &mdb, g.tenants, nil)
	g.srv.EnableManaged(ManagedSettings{Catalog: store, Ledger: ledger, MarkupPercent: 5, RequireCredits: true})
	g.srv.handlers.SetGenerationRecorder(func(s *models.TraceSnapshot) { g.gens <- s })
	g.srv.ready.Store(true)
	return g
}

func (g *eligGateway) chat(t *testing.T, body string, headers ...string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", bytes.NewBufferString(body))
	req.Header.Set("Authorization", "Bearer "+eligKey)
	req.Header.Set("Content-Type", "application/json")
	for i := 0; i+1 < len(headers); i += 2 {
		req.Header.Set(headers[i], headers[i+1])
	}
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// snapshot is the generation record of the last request.
func (g *eligGateway) snapshot(t *testing.T) *models.TraceSnapshot {
	t.Helper()
	return waitSnapshot(t, g.gens)
}

// rejected is the auto decision's rejections of the last request.
func (g *eligGateway) rejected(t *testing.T) []routing.AutoRejection {
	t.Helper()
	var d routing.AutoDecision
	if err := json.Unmarshal([]byte(g.snapshot(t).Metadata["auto_decision"]), &d); err != nil {
		t.Fatalf("auto decision: %v", err)
	}
	return d.Rejected
}

func hasRejection(rs []routing.AutoRejection, model, reason string) bool {
	for _, r := range rs {
		if r.Model == model && r.Reason == reason {
			return true
		}
	}
	return false
}

// eligModel is a fixed-price text chat model at prompt USD per token
// (completion twice that), reachable through eps.
func eligModel(id string, prompt float64, eps ...catalog.Endpoint) *catalog.Model {
	return &catalog.Model{ID: id, Created: 1790000000, ContextLength: 200000, MaxOutputTokens: 32000,
		InputModalities: []string{"text"}, OutputModalities: []string{"text"}, SupportedParameters: []string{"tools"},
		InputPerToken: prompt, OutputPerToken: 2 * prompt, Endpoints: eps}
}

// orEndpoint is an OpenRouter endpoint of model behind upstream tag.
func orEndpoint(model, tag string, prompt float64, mods ...func(*catalog.Endpoint)) catalog.Endpoint {
	e := catalog.Endpoint{ID: catalog.EndpointID(catalog.SourceOpenRouter, tag, model), Source: catalog.SourceOpenRouter,
		UpstreamProvider: strings.SplitN(tag, "/", 2)[0], Tag: tag, Regions: catalog.RegionsFromTag(tag),
		Prices: catalog.Prices{Prompt: prompt, Completion: 2 * prompt}, DataCollection: catalog.DataCollectionUnknown,
		ZDR: new(bool), SupportedParams: []string{"tools"}}
	for _, f := range mods {
		f(&e)
	}
	return e
}

func zdrEndpoint(e *catalog.Endpoint) {
	on := true
	e.ZDR, e.DataCollection = &on, catalog.DataCollectionDeny
}

func collectingEndpoint(e *catalog.Endpoint) { e.DataCollection = catalog.DataCollectionAllow }

// directEndpoint is the direct source's price-card endpoint of model, which
// collects no data.
func directEndpoint(model string, prompt float64, regions ...string) catalog.Endpoint {
	return catalog.Endpoint{ID: catalog.EndpointID(catalog.SourcePriceCard, "anthropic", model), Source: catalog.SourcePriceCard,
		UpstreamProvider: "anthropic", Tag: "anthropic", Regions: regions, Prices: catalog.Prices{Prompt: prompt, Completion: 2 * prompt},
		DataCollection: catalog.DataCollectionDeny}
}

func eligChat(model, extra string) string {
	b := `{"model":"` + model + `","messages":[{"role":"user","content":"hi"}]`
	if extra != "" {
		b += "," + extra
	}
	return b + "}"
}

func optedIn() *tenant.OrgConfig {
	return &tenant.OrgConfig{ManagedModels: &tenant.ManagedModelsConfig{Enabled: true}}
}

func sentProvider(t *testing.T, u *hookUpstream) map[string]any {
	t.Helper()
	p, _ := lastBody(t, u)["provider"].(map[string]any)
	if p == nil {
		t.Fatalf("no provider object reached the upstream: %v", lastBody(t, u))
	}
	return p
}

// strList is a JSON list of strings; anything else is empty.
func strList(v any) []string {
	var out []string
	list, _ := v.([]any)
	for _, x := range list {
		if s, ok := x.(string); ok {
			out = append(out, s)
		}
	}
	return out
}

func expectRefused(t *testing.T, w *httptest.ResponseRecorder, status int, code, reason string) {
	t.Helper()
	var body struct {
		Error struct {
			Code     string            `json:"code"`
			Metadata map[string]string `json:"metadata"`
		} `json:"error"`
	}
	_ = json.Unmarshal(w.Body.Bytes(), &body)
	if w.Code != status || body.Error.Code != code || body.Error.Metadata["reason"] != reason {
		t.Fatalf("status %d code %q reason %q, body %s; want %d %s reason %s",
			w.Code, body.Error.Code, body.Error.Metadata["reason"], w.Body, status, code, reason)
	}
}

// A key restricted to one model gets that model from auto, never a 403 for
// a model it may not use.
func TestAutoRespectsKeyAllowedModels(t *testing.T) {
	g := newEligGateway(t, eligOpts{org: optedIn(), keyModels: []string{"openai/gpt-4o-mini"}, orModels: map[string]*catalog.Model{
		"openai/gpt-4o-mini":          eligModel("openai/gpt-4o-mini", 0.15e-6),
		"qwen/qwen3.7-flash":          eligModel("qwen/qwen3.7-flash", 0.03e-6),
		"anthropic/claude-sonnet-5.5": eligModel("anthropic/claude-sonnet-5.5", 3e-6),
	}})
	w := g.chat(t, eligChat("auto", ""))
	if w.Code != http.StatusOK || w.Header().Get("x-agentcc-auto-model") != "openai/gpt-4o-mini" {
		t.Fatalf("status %d, auto model %q, body %s; want 200 choosing the key's model", w.Code, w.Header().Get("x-agentcc-auto-model"), w.Body)
	}
	if got := lastBody(t, g.or)["model"]; got != "openai/gpt-4o-mini" {
		t.Fatalf("upstream got %v", got)
	}
	if rs := g.rejected(t); !hasRejection(rs, "qwen/qwen3.7-flash", eligibility.CodeKeyModelDenied) {
		t.Fatalf("rejections %v, want qwen rejected by the key ACL", rs)
	}
}

// A key in access groups gets from auto a model its groups allow, judged
// per candidate, never a 403 for the literal auto; an explicit model
// outside its groups is refused.
func TestAutoRespectsKeyAccessGroups(t *testing.T) {
	g := newEligGateway(t, eligOpts{org: optedIn(), keyGroups: "basic",
		groups: config.AccessGroupsConfig{"basic": {Models: []string{"openai/gpt-4o-mini"}}},
		orModels: map[string]*catalog.Model{
			"openai/gpt-4o-mini":          eligModel("openai/gpt-4o-mini", 0.15e-6),
			"qwen/qwen3.7-flash":          eligModel("qwen/qwen3.7-flash", 0.03e-6),
			"anthropic/claude-sonnet-5.5": eligModel("anthropic/claude-sonnet-5.5", 3e-6),
		}})
	w := g.chat(t, eligChat("auto", ""))
	if w.Code != http.StatusOK || w.Header().Get("x-agentcc-auto-model") != "openai/gpt-4o-mini" {
		t.Fatalf("status %d, auto model %q, body %s; want 200 choosing the group's model", w.Code, w.Header().Get("x-agentcc-auto-model"), w.Body)
	}
	if rs := g.rejected(t); !hasRejection(rs, "qwen/qwen3.7-flash", eligibility.CodeKeyModelDenied) {
		t.Fatalf("rejections %v, want qwen rejected by the key's access groups", rs)
	}
	if w := g.chat(t, eligChat("qwen/qwen3.7-flash", "")); w.Code != http.StatusForbidden || len(g.or.received()) != 1 {
		t.Fatalf("explicit model outside the groups: status %d, upstream calls %d, body %s", w.Code, len(g.or.received()), w.Body)
	}
}

// The org's provider lock refuses an explicit model on a denied provider
// before any upstream call, and keeps auto off it.
func TestProviderLockDenyEnforced(t *testing.T) {
	org := optedIn()
	org.Providers = map[string]*tenant.ProviderConfig{"operator": {APIKey: "org-op", Enabled: true, Models: []string{"gpt-4o"}}}
	org.Routing = &tenant.RoutingConfig{
		ProviderLock: &tenant.ProviderLockConfig{Enabled: true, DenyProviders: []string{"openrouter"}},
		Auto:         &tenant.AutoRoutingConfig{Candidates: []string{"anthropic/claude-sonnet-5.5", "gpt-4o"}},
	}
	g := newEligGateway(t, eligOpts{org: org, orModels: map[string]*catalog.Model{
		"anthropic/claude-sonnet-5.5": eligModel("anthropic/claude-sonnet-5.5", 0.1e-6),
	}})
	expectRefused(t, g.chat(t, eligChat("anthropic/claude-sonnet-5.5", "")), http.StatusForbidden, codeModelNotPermitted, eligibility.CodeOrgProviderDenied)
	if n := len(g.or.received()); n != 0 {
		t.Fatalf("the denied provider was called %d times", n)
	}
	w := g.chat(t, eligChat("auto", ""))
	if w.Code != http.StatusOK || w.Header().Get("x-agentcc-auto-model") != "gpt-4o" || len(g.operator.received()) != 1 {
		t.Fatalf("auto: status %d, model %q, operator calls %d, body %s", w.Code, w.Header().Get("x-agentcc-auto-model"), len(g.operator.received()), w.Body)
	}
	if rs := g.rejected(t); !hasRejection(rs, "anthropic/claude-sonnet-5.5", eligibility.CodeOrgProviderDenied) {
		t.Fatalf("rejections %v", rs)
	}

	org.Routing.ProviderLock.Enabled = false
	if w := g.chat(t, eligChat("anthropic/claude-sonnet-5.5", "")); w.Code != http.StatusOK {
		t.Fatalf("with the lock off: status %d, body %s", w.Code, w.Body)
	}
}

// A fallback hop is judged again before it is dispatched: one the org's
// managed policy denies is never sent, and the chain moves on.
func TestFallbackHopRechecked(t *testing.T) {
	org := &tenant.OrgConfig{
		ManagedModels: &tenant.ManagedModelsConfig{Enabled: true, DenyVendors: []string{"x-ai"}},
		Routing: &tenant.RoutingConfig{
			Failover:       &tenant.FailoverConfig{Enabled: true, OnStatusCodes: []int{http.StatusBadGateway}},
			ModelFallbacks: map[string][]string{"qwen/qwen3.7-flash": {"x-ai/grok-5", "openai/gpt-6-sol"}},
		},
	}
	g := newEligGateway(t, eligOpts{org: org, orFails: 1, orModels: map[string]*catalog.Model{
		"qwen/qwen3.7-flash": eligModel("qwen/qwen3.7-flash", 0.03e-6),
		"x-ai/grok-5":        eligModel("x-ai/grok-5", 1e-6),
		"openai/gpt-6-sol":   eligModel("openai/gpt-6-sol", 5e-6),
	}})
	w := g.chat(t, eligChat("qwen/qwen3.7-flash", ""))
	if w.Code != http.StatusOK {
		t.Fatalf("status %d, body %s", w.Code, w.Body)
	}
	var sent []string
	for _, b := range g.or.received() {
		sent = append(sent, b["model"].(string))
	}
	if strings.Join(sent, ",") != "qwen/qwen3.7-flash,openai/gpt-6-sol" {
		t.Fatalf("upstream calls %v: the denied hop must never be dispatched", sent)
	}
}

// A deprecated model is served on an explicit route with its expiration
// date in a header, and left out of auto; an expired one is 410.
func TestDeprecatedExcludedExpired410(t *testing.T) {
	soon := time.Now().AddDate(0, 0, 7).Format(time.DateOnly)
	gone := time.Now().AddDate(0, 0, -1).Format(time.DateOnly)
	dep := eligModel("openai/gpt-6-sol", 5e-6)
	dep.ExpirationDate = soon
	exp := eligModel("anthropic/claude-sonnet-5.5", 3e-6)
	exp.ExpirationDate = gone
	org := optedIn()
	org.Routing = &tenant.RoutingConfig{Auto: &tenant.AutoRoutingConfig{Preference: "quality", Candidates: []string{"openai/gpt-6-sol", "qwen/qwen3.7-flash"}}}
	g := newEligGateway(t, eligOpts{org: org, orModels: map[string]*catalog.Model{
		"openai/gpt-6-sol": dep, "anthropic/claude-sonnet-5.5": exp, "qwen/qwen3.7-flash": eligModel("qwen/qwen3.7-flash", 0.03e-6),
	}})
	w := g.chat(t, eligChat("openai/gpt-6-sol", ""))
	if w.Code != http.StatusOK || w.Header().Get(headerModelDeprecated) != soon {
		t.Fatalf("deprecated: status %d, %s = %q, body %s", w.Code, headerModelDeprecated, w.Header().Get(headerModelDeprecated), w.Body)
	}
	g.snapshot(t)
	expectRefused(t, g.chat(t, eligChat("anthropic/claude-sonnet-5.5", "")), http.StatusGone, eligibility.CodeModelExpired, eligibility.CodeModelExpired)
	w = g.chat(t, eligChat("auto", ""))
	if w.Code != http.StatusOK || w.Header().Get("x-agentcc-auto-model") != "qwen/qwen3.7-flash" || w.Header().Get(headerModelDeprecated) != "" {
		t.Fatalf("auto: status %d, model %q, body %s", w.Code, w.Header().Get("x-agentcc-auto-model"), w.Body)
	}
	if rs := g.rejected(t); !hasRejection(rs, "openai/gpt-6-sol", eligibility.CodeModelDeprecated) {
		t.Fatalf("rejections %v", rs)
	}
}

// provider.only and provider.ignore narrow the endpoints; nothing left is
// 404 no_provider_matches, before any upstream call.
func TestOnlyIgnoreEmpty404(t *testing.T) {
	const m = "anthropic/claude-sonnet-5.5"
	g := newEligGateway(t, eligOpts{org: optedIn(), orModels: map[string]*catalog.Model{
		m: eligModel(m, 3e-6, orEndpoint(m, "deepinfra", 3e-6), orEndpoint(m, "together", 3e-6)),
	}})
	expectRefused(t, g.chat(t, eligChat(m, `"provider":{"ignore":["deepinfra","Together"]}`)), http.StatusNotFound,
		eligibility.CodeNoProviderMatches, eligibility.CodeNoProviderMatches)
	if n := len(g.or.received()); n != 0 {
		t.Fatalf("a refused request reached the upstream %d times", n)
	}
	w := g.chat(t, eligChat(m, `"provider":{"only":["together"]}`))
	if w.Code != http.StatusOK {
		t.Fatalf("status %d, body %s", w.Code, w.Body)
	}
	p := sentProvider(t, g.or)
	if strings.Join(strList(p["order"]), ",") != "together" || strings.Join(strList(p["only"]), ",") != "together" {
		t.Fatalf("provider object %v, want order and only [together]", p)
	}
}

// With org ZDR, only ZDR endpoints are pinned, and a model whose endpoints'
// ZDR is unknown, or which has no endpoint data, is refused.
func TestZdrFailsClosed(t *testing.T) {
	const m = "anthropic/claude-sonnet-5.5"
	unknown := func(e *catalog.Endpoint) { e.ZDR = nil }
	org := optedIn()
	org.Routing = &tenant.RoutingConfig{DataPolicy: &tenant.DataPolicyConfig{ZDR: true}}
	g := newEligGateway(t, eligOpts{org: org, orModels: map[string]*catalog.Model{
		m:                    eligModel(m, 3e-6, orEndpoint(m, "deepinfra", 1e-6, unknown), orEndpoint(m, "anthropic", 3e-6, zdrEndpoint)),
		"openai/gpt-6-sol":   eligModel("openai/gpt-6-sol", 5e-6, orEndpoint("openai/gpt-6-sol", "openai", 5e-6, unknown)),
		"qwen/qwen3.7-flash": eligModel("qwen/qwen3.7-flash", 0.03e-6),
	}})
	w := g.chat(t, eligChat(m, ""))
	if w.Code != http.StatusOK {
		t.Fatalf("status %d, body %s", w.Code, w.Body)
	}
	p := sentProvider(t, g.or)
	if p["zdr"] != true || strings.Join(strList(p["only"]), ",") != "anthropic" {
		t.Fatalf("provider object %v, want zdr true and only the ZDR endpoint", p)
	}
	for _, model := range []string{"openai/gpt-6-sol", "qwen/qwen3.7-flash"} {
		expectRefused(t, g.chat(t, eligChat(model, "")), http.StatusForbidden, codeModelNotPermitted, eligibility.CodeDataPolicyZDR)
	}
}

// A preview model is refused in an env the org blocks previews in.
func TestPreviewBlockedInEnv(t *testing.T) {
	const m = "google/gemini-3-pro-preview"
	org := optedIn()
	org.Routing = &tenant.RoutingConfig{DataPolicy: &tenant.DataPolicyConfig{BlockPreviewInEnvs: []string{"prod"}}}
	g := newEligGateway(t, eligOpts{org: org, orModels: map[string]*catalog.Model{m: eligModel(m, 1e-6)}})
	expectRefused(t, g.chat(t, eligChat(m, ""), "x-agentcc-env", "prod"), http.StatusForbidden, codeModelNotPermitted, eligibility.CodePreviewBlocked)
	if w := g.chat(t, eligChat(m, ""), "x-agentcc-env", "staging"); w.Code != http.StatusOK {
		t.Fatalf("staging: status %d, body %s", w.Code, w.Body)
	}
}

// The endpoint set is the top five eligible endpoints by health × 1/price²,
// sent as order and only.
func TestEndpointSetRankedTop5(t *testing.T) {
	const m = "anthropic/claude-sonnet-5.5"
	up := func(u float64) func(*catalog.Endpoint) { return func(e *catalog.Endpoint) { e.UptimeLast30m = &u } }
	g := newEligGateway(t, eligOpts{org: optedIn(), orModels: map[string]*catalog.Model{m: eligModel(m, 3e-6,
		orEndpoint(m, "p6", 6e-6), orEndpoint(m, "p1", 1e-6), orEndpoint(m, "p4", 4e-6), orEndpoint(m, "p2", 2e-6),
		orEndpoint(m, "p1-flaky", 1e-6, up(20)), orEndpoint(m, "p3", 3e-6), orEndpoint(m, "p5", 5e-6),
	)}})
	w := g.chat(t, eligChat(m, ""))
	if w.Code != http.StatusOK {
		t.Fatalf("status %d, body %s", w.Code, w.Body)
	}
	p := sentProvider(t, g.or)
	// p1-flaky scores 0.2 of p1: below p2 (0.25), above p3 (0.11).
	if got := strings.Join(strList(p["order"]), ","); got != "p1,p2,p1-flaky,p3,p4" || strings.Join(strList(p["only"]), ",") != got {
		t.Fatalf("provider object %v, want order and only p1,p2,p1-flaky,p3,p4", p)
	}
}

// At equal list price a direct price-card source beats OpenRouter on landed
// cost, and the choice is recorded; a direct source more than 1% dearer
// loses to the billed price.
func TestDirectSourcePreferredAtEqualPrice(t *testing.T) {
	const m = "anthropic/claude-sonnet-5.5"
	for _, tc := range []struct {
		name        string
		directPrice float64
		wantChosen  string
	}{
		{"equal list price", 3e-6, "zz-direct"},
		{"direct 2% dearer", 3.06e-6, "openrouter"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			org := optedIn()
			org.Routing = &tenant.RoutingConfig{Auto: &tenant.AutoRoutingConfig{Candidates: []string{m}}}
			g := newEligGateway(t, eligOpts{org: org,
				orModels:     map[string]*catalog.Model{m: eligModel(m, 3e-6, orEndpoint(m, "anthropic", 3e-6, zdrEndpoint))},
				directModels: map[string]*catalog.Model{m: eligModel(m, tc.directPrice, directEndpoint(m, tc.directPrice))},
			})
			w := g.chat(t, eligChat("auto", ""))
			if w.Code != http.StatusOK || w.Header().Get("x-agentcc-provider") != tc.wantChosen {
				t.Fatalf("status %d, provider %q, body %s; want %s", w.Code, w.Header().Get("x-agentcc-provider"), w.Body, tc.wantChosen)
			}
			var choices []sourceChoice
			if err := json.Unmarshal([]byte(g.snapshot(t).Metadata[metaSourceChoice]), &choices); err != nil || len(choices) != 1 {
				t.Fatalf("source_choice %v (%v)", choices, err)
			}
			c := choices[0]
			if c.Model != m || c.Chosen != tc.wantChosen || len(c.Over) != 1 || c.LandedRatio <= 0 {
				t.Fatalf("source choice %+v", c)
			}
			if tc.wantChosen == "zz-direct" && math.Abs(c.LandedRatio-math.Round(1e4/1.055)/1e4) > 1e-9 {
				t.Fatalf("landed ratio %v, want 1/1.055", c.LandedRatio)
			}
		})
	}
}

// Managed attempts deny data collection by default: an endpoint that
// collects is never pinned, and the provider object says deny; the org's
// opt-in allows both.
func TestManagedDataCollectionDenyDefault(t *testing.T) {
	const m = "anthropic/claude-sonnet-5.5"
	cat := func() map[string]*catalog.Model {
		return map[string]*catalog.Model{m: eligModel(m, 3e-6,
			orEndpoint(m, "collector", 1e-6, collectingEndpoint), orEndpoint(m, "deepinfra", 2e-6), orEndpoint(m, "anthropic", 3e-6, zdrEndpoint))}
	}
	g := newEligGateway(t, eligOpts{org: optedIn(), orModels: cat()})
	if w := g.chat(t, eligChat(m, "")); w.Code != http.StatusOK {
		t.Fatalf("status %d, body %s", w.Code, w.Body)
	}
	p := sentProvider(t, g.or)
	if p["data_collection"] != "deny" || strings.Join(strList(p["order"]), ",") != "deepinfra,anthropic" {
		t.Fatalf("default: provider object %v, want deny without the collecting endpoint", p)
	}
	optIn := optedIn()
	optIn.ManagedModels.AllowDataCollection = true
	g = newEligGateway(t, eligOpts{org: optIn, orModels: cat()})
	if w := g.chat(t, eligChat(m, "")); w.Code != http.StatusOK {
		t.Fatalf("opt-in: status %d, body %s", w.Code, w.Body)
	}
	p = sentProvider(t, g.or)
	if p["data_collection"] != "allow" || strings.Join(strList(p["order"]), ",") != "collector,deepinfra,anthropic" {
		t.Fatalf("opt-in: provider object %v, want allow with every endpoint", p)
	}
}

// A transcript with unsigned tool calls is never sent to a Gemini 3 model:
// auto picks another, and an explicit route is refused.
func TestToolHistoryIncompatibleGemini(t *testing.T) {
	const gem = "google/gemini-3-pro"
	org := optedIn()
	org.Routing = &tenant.RoutingConfig{Auto: &tenant.AutoRoutingConfig{Preference: "cost", Candidates: []string{gem, "openai/gpt-6-sol"}}}
	g := newEligGateway(t, eligOpts{org: org, orModels: map[string]*catalog.Model{
		gem: eligModel(gem, 0.1e-6), "openai/gpt-6-sol": eligModel("openai/gpt-6-sol", 5e-6),
	}})
	transcript := func(model string) string {
		return `{"model":"` + model + `","messages":[{"role":"user","content":"weather?"},` +
			`{"role":"assistant","tool_calls":[{"id":"call_1","type":"function","function":{"name":"w","arguments":"{}"}}]},` +
			`{"role":"tool","tool_call_id":"call_1","content":"sunny"}]}`
	}
	w := g.chat(t, transcript("auto"))
	if w.Code != http.StatusOK || w.Header().Get("x-agentcc-auto-model") != "openai/gpt-6-sol" {
		t.Fatalf("auto: status %d, model %q, body %s", w.Code, w.Header().Get("x-agentcc-auto-model"), w.Body)
	}
	if rs := g.rejected(t); !hasRejection(rs, gem, eligibility.CodeToolHistoryIncompatible) {
		t.Fatalf("rejections %v", rs)
	}
	expectRefused(t, g.chat(t, transcript(gem)), http.StatusForbidden, codeModelNotPermitted, eligibility.CodeToolHistoryIncompatible)
}

// fakeModelHealth reports a fixed snapshot per model.
type fakeModelHealth map[string]routing.ModelHealthSnapshot

func (f fakeModelHealth) Snapshot(_, model string) routing.ModelHealthSnapshot { return f[model] }

// Auto ranks two managed models behind one provider on each model's own
// health: a failing model loses to a healthy one the provider's shared
// health could not tell apart.
func TestModelHealthSeparatesManagedCandidates(t *testing.T) {
	const sick, well = "anthropic/claude-sonnet-5.5", "openai/gpt-6-sol"
	org := optedIn()
	org.Routing = &tenant.RoutingConfig{Auto: &tenant.AutoRoutingConfig{Candidates: []string{sick, well}}}
	g := newEligGateway(t, eligOpts{org: org, orModels: map[string]*catalog.Model{
		sick: eligModel(sick, 3e-6), well: eligModel(well, 3e-6),
	}})
	g.srv.handlers.SetInjectedDeps(InjectedDeps{ModelHealth: fakeModelHealth{
		sick: {N: 50, SuccessRate: 0.2, ErrorRate: 0.8, P50Ms: 900},
		well: {N: 50, SuccessRate: 1, P50Ms: 900},
	}})
	if w := g.chat(t, eligChat("auto", "")); w.Code != http.StatusOK || w.Header().Get("x-agentcc-auto-model") != well {
		t.Fatalf("status %d, auto model %q; want the healthy %s", w.Code, w.Header().Get("x-agentcc-auto-model"), well)
	}
	c := g.srv.handlers.autoCandidate("openrouter", sick)
	if c.Health == nil || c.Health.RequestCount != 50 || c.Health.SuccessRate != 0.2 || c.Health.ErrorCount != 40 || c.Health.LatencyEWMAMs != 900 {
		t.Fatalf("candidate health %+v, want the model's own", c.Health)
	}
	// A model with no recent calls keeps the provider's figures: having no
	// history must not make it look fastest.
	monitor := g.srv.handlers.healthMonitor
	if monitor == nil {
		t.Fatal("the test gateway has no provider health monitor; the cold-model check proves nothing")
	}
	monitor.RecordSuccess("openrouter")
	monitor.RecordError("openrouter", errors.New("upstream down"))
	cold := g.srv.handlers.autoCandidate("openrouter", "qwen/never-called")
	if want := monitor.GetHealth("openrouter"); cold.Health == nil || want.ErrorCount == 0 ||
		cold.Health.RequestCount != want.RequestCount || cold.Health.SuccessRate != want.SuccessRate {
		t.Fatalf("cold candidate health %+v, want the provider's %+v", cold.Health, want)
	}
}

// With allowed_regions, a managed OpenRouter endpoint is refused even when
// tagged in region, while a BYOK provider in region and a price-card source
// in region are eligible.
func TestAllowedRegionsRejectsManagedOpenRouter(t *testing.T) {
	const viaOR, direct = "anthropic/claude-sonnet-5.5", "anthropic/claude-opus-5"
	org := optedIn()
	org.Providers = map[string]*tenant.ProviderConfig{"operator": {APIKey: "org-op", Enabled: true, Models: []string{"gpt-4o"}, DataRegion: "eu"}}
	org.Routing = &tenant.RoutingConfig{DataPolicy: &tenant.DataPolicyConfig{AllowedRegions: []string{"eu"}}}
	g := newEligGateway(t, eligOpts{org: org,
		orModels:     map[string]*catalog.Model{viaOR: eligModel(viaOR, 3e-6, orEndpoint(viaOR, "google-vertex/eu", 3e-6, zdrEndpoint))},
		directModels: map[string]*catalog.Model{direct: eligModel(direct, 15e-6, directEndpoint(direct, 15e-6, "eu-west-1"))},
	})
	expectRefused(t, g.chat(t, eligChat(viaOR, "")), http.StatusForbidden, codeModelNotPermitted, eligibility.CodeRegionUnpinnable)
	if w := g.chat(t, eligChat("gpt-4o", "")); w.Code != http.StatusOK || len(g.operator.received()) != 1 {
		t.Fatalf("BYOK in region: status %d, body %s", w.Code, w.Body)
	}
	if w := g.chat(t, eligChat(direct, "")); w.Code != http.StatusOK || len(g.direct.received()) != 1 {
		t.Fatalf("price-card source in region: status %d, body %s", w.Code, w.Body)
	}
	if n := len(g.or.received()); n != 0 {
		t.Fatalf("the aggregator was called %d times", n)
	}
}

// autoFilterGateway lists one managed OpenRouter candidate, claude through
// deepinfra (1 USD/M prompt) and together (3 USD/M), plus extra.
func autoFilterGateway(t *testing.T, extra ...string) *eligGateway {
	t.Helper()
	const m = "anthropic/claude-sonnet-5.5"
	org := optedIn()
	org.Providers = map[string]*tenant.ProviderConfig{"operator": {APIKey: "org-op", Enabled: true, Models: []string{"gpt-4o"}}}
	org.Routing = &tenant.RoutingConfig{Auto: &tenant.AutoRoutingConfig{Candidates: append([]string{m}, extra...)}}
	return newEligGateway(t, eligOpts{org: org, orModels: map[string]*catalog.Model{
		m: eligModel(m, 3e-6, orEndpoint(m, "deepinfra", 1e-6), orEndpoint(m, "together", 3e-6)),
	}})
}

// autoFilterCases are caller filters that only the managed endpoint pin
// enforces, with the endpoints an explicit model is pinned to under each.
var autoFilterCases = []struct{ name, filter, wantPinned string }{
	{"ignore", `"provider":{"ignore":["deepinfra"]}`, "together"},
	{"max_price", `"provider":{"max_price":{"prompt":2,"completion":4}}`, "deepinfra"},
}

// On model=auto, a managed OpenRouter candidate is never sent with a caller
// filter its provider object would drop: the candidate auto chooses is
// judged again as the route, so it is pinned to the endpoints the filter
// leaves, exactly as the explicit model is.
func TestAutoManagedEndpointFiltersNeverUnpinned(t *testing.T) {
	const m = "anthropic/claude-sonnet-5.5"
	for _, tc := range autoFilterCases {
		t.Run(tc.name, func(t *testing.T) {
			g := autoFilterGateway(t)
			for _, model := range []string{"auto", m} {
				w := g.chat(t, eligChat(model, tc.filter))
				if w.Code != http.StatusOK || (model == "auto" && w.Header().Get("x-agentcc-auto-model") != m) {
					t.Fatalf("%s: status %d, auto model %q, body %s", model, w.Code, w.Header().Get("x-agentcc-auto-model"), w.Body)
				}
				p := sentProvider(t, g.or)
				if strings.Join(strList(p["order"]), ",") != tc.wantPinned || strings.Join(strList(p["only"]), ",") != tc.wantPinned {
					t.Fatalf("%s: provider object %v, want order and only [%s]", model, p, tc.wantPinned)
				}
			}
		})
	}
}

// A listed managed candidate named with its provider prefix
// ("openrouter/x-ai/grok-5", sent upstream as "x-ai/grok-5") is judged on the
// model it is sent as: the org's managed deny list keeps it out of auto, as
// it refuses the explicit model, and auto serves another candidate.
func TestAutoListedPrefixedManagedJudgedOnUpstream(t *testing.T) {
	const listed, upstream, other = "openrouter/x-ai/grok-5", "x-ai/grok-5", "anthropic/claude-sonnet-5.5"
	org := optedIn()
	org.ManagedModels.DenyVendors = []string{"x-ai"}
	org.Routing = &tenant.RoutingConfig{Auto: &tenant.AutoRoutingConfig{Candidates: []string{listed, other}}}
	g := newEligGateway(t, eligOpts{org: org, orModels: map[string]*catalog.Model{
		upstream: eligModel(upstream, 0.1e-6),
		other:    eligModel(other, 3e-6),
	}})
	w := g.chat(t, eligChat("auto", ""))
	if w.Code != http.StatusOK || w.Header().Get("x-agentcc-auto-model") != other {
		t.Fatalf("status %d, auto model %q, body %s; want %s", w.Code, w.Header().Get("x-agentcc-auto-model"), w.Body, other)
	}
	if got := lastBody(t, g.or)["model"]; got != other {
		t.Fatalf("upstream got %v", got)
	}
	if rs := g.rejected(t); !hasRejection(rs, listed, eligibility.CodeManagedModelNotAllowed) {
		t.Fatalf("rejections %v, want %s refused by the managed deny list", rs, listed)
	}
	expectRefused(t, g.chat(t, eligChat(upstream, "")), http.StatusForbidden, codeModelNotPermitted, eligibility.CodeManagedModelNotAllowed)
}

// deniedHopGateway is newRefusedHopGateway's setup, with the hop to second
// refused by eligibility (the org's provider lock) instead of the hold.
func deniedHopGateway(t *testing.T) *attemptsGateway {
	t.Helper()
	g := newAttemptsGateway(t, attemptsGatewayOpts{failover: true, retries: 2, breaker: true})
	g.first.status.Store(http.StatusBadGateway)
	g.srv.handlers.tenantStore.Set("org-attempts", &tenant.OrgConfig{Routing: &tenant.RoutingConfig{
		ProviderLock: &tenant.ProviderLockConfig{Enabled: true, DenyProviders: []string{"second"}},
	}})
	return g
}

// A hop eligibility refuses is skipped: never dispatched, never retried,
// its breaker untouched, recorded as a skipped attempt for policy, and the
// response is the previous attempt's error.
func TestDeniedHopNotRetriedOrTripped(t *testing.T) {
	g := deniedHopGateway(t)
	w := g.chat(t, attemptsChat)
	if w.Code != http.StatusBadGateway || g.second.calls.Load() != 0 || !strings.Contains(w.Body.String(), "upstream down") {
		t.Fatalf("status %d, second called %d times, body %s; want first's 502", w.Code, g.second.calls.Load(), w.Body)
	}
	if n := g.first.calls.Load(); n != 3 {
		t.Fatalf("first called %d times, want its call and 2 retries", n)
	}
	if state := g.breakers.Get(routing.BreakerKey("second", "gpt-4o", "")).State(); state != routing.StateClosed {
		t.Fatalf("second's breaker is %v after a hop the gateway refused", state)
	}
	attempts, _, _, _ := g.capture.last()
	skipped := 0
	for _, a := range attempts {
		if a.Provider == "second" {
			skipped++
			if a.Outcome != models.OutcomeSkipped || a.Reason != models.ReasonPolicy || a.ErrorClass != models.ErrClassPolicy {
				t.Fatalf("refused hop's attempt = %+v, want skipped for policy", a)
			}
		}
	}
	if skipped != 1 || len(attempts) != 4 {
		t.Fatalf("attempts %+v: want first's three and one skipped hop", attempts)
	}
	checkAttemptsMatchSchema(t, attempts)
}

// fallbackHopAllowed judges any (provider, model), whichever target
// checkEligibility judged: Failover.Execute's first target can differ from
// resolveRoute's, and the failover path calls this for it.
func TestFallbackHopAllowedForUncheckedFirstTarget(t *testing.T) {
	g := deniedHopGateway(t)
	h := g.srv.handlers
	orgCfg := h.tenantStore.Get("org-attempts")
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model, rc.Provider = "gpt-4o", "first"
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o"}
	rc.Metadata["key_type"] = "internal"
	if h.fallbackHopAllowed(rc, orgCfg, "second", "gpt-4o") {
		t.Fatal("second, which the org's lock denies, was allowed")
	}
	if !h.fallbackHopAllowed(rc, orgCfg, "first", "gpt-4o") {
		t.Fatal("first was refused")
	}
}

// --- the effective config ---

// rolloutOrg is an org at config version 3 whose stable policy is stable
// and whose rollout candidate (v4) on percent of traffic is candidate.
func rolloutOrg(percent int, stable *tenant.ManagedModelsConfig, candidate *tenant.ManagedModelsConfig) *tenant.OrgConfig {
	return &tenant.OrgConfig{
		ManagedModels: stable,
		Meta:          &tenant.ConfigMeta{Version: 3},
		Routing: &tenant.RoutingConfig{Rollout: &tenant.RolloutConfig{
			Version: "v4", Percent: percent, StickyBy: "request",
			Routing: &tenant.RoutingConfig{}, ManagedModels: candidate,
		}},
	}
}

// The candidate's managed_models decides an explicit managed model for
// candidate traffic: a rollout-only opt-in serves it managed at 100% and
// refuses it at 0%.
func TestCandidateOnlyManagedOptInExplicitModel(t *testing.T) {
	const model = "anthropic/claude-sonnet-5.5"
	for _, tc := range []struct {
		percent  int
		wantCode int
	}{{100, http.StatusOK}, {0, http.StatusForbidden}} {
		g := newManagedTestGateway(t, rolloutOrg(tc.percent, &tenant.ManagedModelsConfig{Enabled: false}, &tenant.ManagedModelsConfig{Enabled: true}))
		g.ledger.SetMicros("org-m", auth.USDToMicros(5))
		w := g.chat(t, chatBody(model), nil)
		if w.Code != tc.wantCode {
			t.Fatalf("rollout at %d%%: status %d, body %s; want %d", tc.percent, w.Code, w.Body, tc.wantCode)
		}
		if tc.wantCode == http.StatusOK {
			if got := w.Header().Get("x-agentcc-billing-mode"); got != "managed" || g.managed.lastModel() != model {
				t.Fatalf("rollout at 100%%: billing mode %q, upstream model %q; want the model served managed", got, g.managed.lastModel())
			}
		} else if g.managed.calls() != 0 {
			t.Fatalf("rollout at 0%%: the managed upstream was called %d times", g.managed.calls())
		}
	}
}

// A candidate that takes the explicit managed model away while stable
// offers it refuses candidate traffic: by turning managed models off, and by
// denying the model.
func TestCandidateDeniesManagedExplicitModel(t *testing.T) {
	const model = "anthropic/claude-sonnet-5.5"
	stable := &tenant.ManagedModelsConfig{Enabled: true}
	for name, candidate := range map[string]*tenant.ManagedModelsConfig{
		"managed off":  {Enabled: false},
		"model denied": {Enabled: true, DenyModels: []string{"anthropic/*"}},
	} {
		t.Run(name, func(t *testing.T) {
			for _, tc := range []struct {
				percent  int
				wantCode int
			}{{100, http.StatusForbidden}, {0, http.StatusOK}} {
				g := newManagedTestGateway(t, rolloutOrg(tc.percent, stable, candidate))
				g.ledger.SetMicros("org-m", auth.USDToMicros(5))
				w := g.chat(t, chatBody(model), nil)
				if w.Code != tc.wantCode {
					t.Fatalf("rollout at %d%%: status %d, body %s; want %d", tc.percent, w.Code, w.Body, tc.wantCode)
				}
				if tc.wantCode == http.StatusForbidden && g.managed.calls() != 0 {
					t.Fatalf("rollout at 100%%: the managed upstream was called %d times", g.managed.calls())
				}
			}
		})
	}
}

// timeoutOrg is an opted-in org whose stable model timeout for qwen is 30s
// and whose candidate's, on percent of traffic, is 5s.
func timeoutOrg(percent int) *tenant.OrgConfig {
	cfg := managedOptIn()
	cfg.Meta = &tenant.ConfigMeta{Version: 3}
	cfg.Routing = &tenant.RoutingConfig{
		ModelTimeouts: map[string]string{"qwen/qwen3.7-flash": "30s"},
		Rollout: &tenant.RolloutConfig{
			Version: "v4", Percent: percent, StickyBy: "request",
			Routing: &tenant.RoutingConfig{ModelTimeouts: map[string]string{"qwen/qwen3.7-flash": "5s"}},
		},
	}
	return cfg
}

// The candidate's routing.model_timeouts applies to candidate traffic.
func TestCandidateModelTimeoutApplies(t *testing.T) {
	for _, tc := range []struct {
		percent int
		want    string
	}{{100, "5000"}, {0, "30000"}} {
		g := newManagedTestGateway(t, timeoutOrg(tc.percent))
		g.ledger.SetMicros("org-m", auth.USDToMicros(5))
		w := g.chat(t, chatBody("qwen/qwen3.7-flash"), nil)
		if w.Code != http.StatusOK {
			t.Fatalf("rollout at %d%%: status %d, body %s", tc.percent, w.Code, w.Body)
		}
		if got := w.Header().Get("x-agentcc-timeout-ms"); got != tc.want {
			t.Errorf("rollout at %d%%: x-agentcc-timeout-ms = %q, want %q", tc.percent, got, tc.want)
		}
	}
}

// The stable timeout is on the request from the start, so a response written
// inside steps 1 to 10 carries it as x-agentcc-timeout-ms; after step 10 the
// candidate's replaces it.
func TestTimeoutHeaderOnEarlyError(t *testing.T) {
	g := newManagedTestGateway(t, timeoutOrg(100))
	g.ledger.SetMicros("org-m", auth.USDToMicros(5))
	early := map[string]string{}
	g.srv.handlers.hookObserver = func(hook, _ string, rc *models.RequestContext) {
		switch hook {
		case hookPeekKeyOrgID, hookIdempotencyBegin:
			w := httptest.NewRecorder()
			g.srv.handlers.setAgentccHeaders(w, rc)
			early[hook] = w.Header().Get("x-agentcc-timeout-ms")
		}
	}
	w := g.chat(t, chatBody("qwen/qwen3.7-flash"), nil)
	if w.Code != http.StatusOK {
		t.Fatalf("status %d, body %s", w.Code, w.Body)
	}
	if early[hookPeekKeyOrgID] != "30000" || early[hookIdempotencyBegin] != "30000" {
		t.Errorf("headers rendered inside steps 1-10 carried x-agentcc-timeout-ms %v, want the stable 30000 at both", early)
	}
	if got := w.Header().Get("x-agentcc-timeout-ms"); got != "5000" {
		t.Errorf("final x-agentcc-timeout-ms = %q, want the candidate's 5000", got)
	}
}

// The node's rule for a model (its config.yaml model timeout, else the server
// default) lives in nodeModelTimeout only. resolveTimeout's last two
// steps give nodeTimeout's answer. requestTimeout and requestTimeoutFor reach
// nodeModelTimeout rather than reading the model timeouts themselves, and
// resolveTimeout and resolveTimeoutFor are those two held at the ceiling, so
// a synchronous request and its async job resolve one timeout.
func TestResolveTimeoutUsesNodeTimeout(t *testing.T) {
	h := &Handlers{defaultTimeout: 60 * time.Second}
	mt := map[string]time.Duration{"m-config": 77 * time.Second}
	h.modelTimeouts.Store(&mt)
	r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
	for _, model := range []string{"m-config", "m-other"} {
		rc := models.AcquireRequestContext()
		rc.Model = model
		if got, want := h.resolveTimeout(rc, r), h.nodeTimeout(model); got != want {
			t.Errorf("%s: resolveTimeout = %v, nodeTimeout = %v", model, got, want)
		}
		if got, want := h.resolveTimeoutFor(rc, r, &tenant.OrgConfig{}), h.nodeTimeout(model); got != want {
			t.Errorf("%s: resolveTimeoutFor = %v, nodeTimeout = %v", model, got, want)
		}
		if got, want := h.requestTimeout(rc, r), h.nodeModelTimeout(model); got != want {
			t.Errorf("%s: requestTimeout = %v, nodeModelTimeout = %v", model, got, want)
		}
		if got, want := h.requestTimeoutFor(rc, r, &tenant.OrgConfig{}), h.nodeModelTimeout(model); got != want {
			t.Errorf("%s: requestTimeoutFor = %v, nodeModelTimeout = %v", model, got, want)
		}
		rc.Release()
	}

	_, here, _, _ := runtime.Caller(0)
	src, err := os.ReadFile(filepath.Join(filepath.Dir(here), "handlers.go"))
	if err != nil {
		t.Fatal(err)
	}
	f, err := parser.ParseFile(token.NewFileSet(), "handlers.go", src, 0)
	if err != nil {
		t.Fatal(err)
	}
	// Each resolver's body must call the function that carries the rule.
	reach := map[string]string{
		"requestTimeout":    "nodeModelTimeout",
		"requestTimeoutFor": "nodeModelTimeout",
		"resolveTimeout":    "requestTimeout",
		"resolveTimeoutFor": "requestTimeoutFor",
	}
	checked := 0
	for _, d := range f.Decls {
		fn, ok := d.(*ast.FuncDecl)
		if !ok || reach[fn.Name.Name] == "" {
			continue
		}
		checked++
		reaches, readsTimeouts := false, false
		ast.Inspect(fn.Body, func(n ast.Node) bool {
			if sel, ok := n.(*ast.SelectorExpr); ok {
				switch sel.Sel.Name {
				case reach[fn.Name.Name]:
					reaches = true
				case "modelTimeouts", "defaultTimeout":
					readsTimeouts = true
				}
			}
			return true
		})
		if !reaches || readsTimeouts {
			t.Errorf("%s: reaches %s %v, reads the timeouts itself %v; want the rule in nodeModelTimeout only", fn.Name.Name, reach[fn.Name.Name], reaches, readsTimeouts)
		}
	}
	if checked != len(reach) {
		t.Fatalf("found %d of the %d timeout resolvers in handlers.go", checked, len(reach))
	}
}

// route_reason.conditional_rule is stamped where the rule takes effect: a
// rule whose provider is not in the registry falls through to normal
// routing and leaves no stamp.
func TestConditionalRuleStampedWhereApplied(t *testing.T) {
	up := startModelRecorder(t)
	srv := createTestServer(t, up.URL)
	h := srv.handlers
	cr, err := routing.NewConditionalRouter([]config.ConditionalRouteConfig{
		{Name: "to-missing", Priority: 20, Condition: config.ConditionConfig{Field: "model", Op: "$eq", Value: "gpt-4o"},
			Action: config.RouteActionConfig{Provider: "no-such-provider"}},
		{Name: "to-openai", Priority: 10, Condition: config.ConditionConfig{Field: "model", Op: "$eq", Value: "gpt-4o-mini"},
			Action: config.RouteActionConfig{Provider: "openai"}},
	})
	if err != nil {
		t.Fatal(err)
	}
	h.conditionalRouter.Store(cr)
	for _, tc := range []struct{ model, wantStamp string }{
		{"gpt-4o", ""},
		{"gpt-4o-mini", "to-openai"},
	} {
		rc := models.AcquireRequestContext()
		rc.Model = tc.model
		rc.Request = &models.ChatCompletionRequest{Model: tc.model}
		p, _, err := h.resolveProvider(context.Background(), rc, tc.model)
		if err != nil || p == nil || p.ID() != "openai" {
			t.Fatalf("%s: provider %v, err %v; want openai", tc.model, p, err)
		}
		stamp, stamped := rc.Metadata[routing.RouteReasonConditional]
		if stamp != tc.wantStamp || stamped != (tc.wantStamp != "") {
			t.Errorf("%s: route_reason.conditional_rule = %q (set %v), want %q", tc.model, stamp, stamped, tc.wantStamp)
		}
		rc.Release()
	}
}
