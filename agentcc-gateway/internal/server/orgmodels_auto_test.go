package server

// Model "auto" as an org key sees it.
//
//   - auto prices a candidate from the org's Cost Tracking prices
//     (custom_pricing) and its Model Database overrides,
//     with the cost plugin's precedence; Add Provider preset ids
//     ("together", "fireworks") find their models under the model
//     database's prefixes ("together_ai/", "fireworks_ai/"); a candidate
//     nothing prices is still set aside with its reason; a managed model's
//     price is never the org's.
//   - the agent context's route-hint (cost, balanced, quality) is
//     auto's preference when no x-agentcc-auto-preference is sent, and
//     "latency" is refused under auto; require filters the pool by
//     capability and names the requirement when nothing qualifies.

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	orgModelsOrg    = "org-orgmodels"
	orgModelsKey    = "sk-agentcc-orgmodels-customer-0123456789abcdef0123"
	orgModelsOwn    = "my-llama-70b" // a self-hosted name no model database knows
	orgModelsPriced = "gpt-4o-mini"  // in the bundled model database ($0.15 / $0.60 per 1M)
)

// orgModelsUpstream answers every chat completion and records the model and the
// raw body it was sent.
type orgModelsUpstream struct {
	*httptest.Server
	mu     sync.Mutex
	models []string
	bodies []map[string]json.RawMessage
}

func (u *orgModelsUpstream) seen() []string {
	u.mu.Lock()
	defer u.mu.Unlock()
	return append([]string(nil), u.models...)
}

func (u *orgModelsUpstream) lastBody() map[string]json.RawMessage {
	u.mu.Lock()
	defer u.mu.Unlock()
	if len(u.bodies) == 0 {
		return nil
	}
	return u.bodies[len(u.bodies)-1]
}

func startOrgModelsUpstream(t *testing.T) *orgModelsUpstream {
	t.Helper()
	u := &orgModelsUpstream{}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		if r.Method != http.MethodPost || !strings.HasSuffix(r.URL.Path, "/chat/completions") {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		var body map[string]json.RawMessage
		_ = json.Unmarshal(b, &body)
		var model string
		_ = json.Unmarshal(body["model"], &model)
		u.mu.Lock()
		u.models = append(u.models, model)
		u.bodies = append(u.bodies, body)
		u.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"id":"chatcmpl-orgmodels","object":"chat.completion","created":1,"model":"`+model+
			`","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],`+
			`"usage":{"prompt_tokens":1000,"completion_tokens":500,"total_tokens":1500}}`)
	}))
	t.Cleanup(u.Close)
	return u
}

// orgModelsGateway serves providerID (on the node, layered by the org's own
// provider of the same id, which holds the org's key) listing models, to a
// customer key of orgModelsOrg (key type byok, as Django mints them). extra
// plugins run after auth.
func orgModelsGateway(t *testing.T, up *orgModelsUpstream, providerID string, models []string, org *tenant.OrgConfig, extra ...pipeline.Plugin) *Server {
	t.Helper()
	off := false
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "customer", Key: orgModelsKey, Owner: orgModelsOrg, KeyType: "byok", Metadata: map[string]string{"org_id": orgModelsOrg}},
	}
	cfg.Providers[providerID] = config.ProviderConfig{BaseURL: up.URL + "/v1", APIKey: "node-key", APIFormat: "openai", Models: models, AutoDiscover: &off}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	tenants := tenant.NewStore()
	if org.Providers == nil {
		org.Providers = map[string]*tenant.ProviderConfig{}
	}
	if org.Providers[providerID] == nil {
		org.Providers[providerID] = &tenant.ProviderConfig{APIKey: "org-key", APIFormat: "openai", Models: models, Enabled: true}
	}
	tenants.Set(orgModelsOrg, org)
	plugins := append([]pipeline.Plugin{authplugin.New(keyStore, true)}, extra...)
	srv := New(cfg, "", registry, pipeline.NewEngine(plugins...), keyStore, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	return srv
}

func orgModelsChat(t *testing.T, srv *Server, body string, hdr map[string]string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+orgModelsKey)
	req.Header.Set("Content-Type", "application/json")
	for k, v := range hdr {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// orgModelsErr is the response's error code and message.
func orgModelsErr(w *httptest.ResponseRecorder) (string, string) {
	var e struct {
		Error struct {
			Code    string `json:"code"`
			Message string `json:"message"`
		} `json:"error"`
	}
	_ = json.Unmarshal(w.Body.Bytes(), &e)
	return e.Error.Code, e.Error.Message
}

// wantAutoPick checks a 200 whose auto choice, and upstream call, is model.
func wantAutoPick(t *testing.T, w *httptest.ResponseRecorder, up *orgModelsUpstream, model string) {
	t.Helper()
	if w.Code != http.StatusOK || w.Header().Get("x-agentcc-auto-model") != model {
		t.Fatalf("auto -> %d, auto-model %q (reason %q); want 200 choosing %q; body %s",
			w.Code, w.Header().Get("x-agentcc-auto-model"), w.Header().Get("x-agentcc-auto-reason"), model, w.Body.String())
	}
	if up != nil {
		if s := up.seen(); len(s) == 0 || s[len(s)-1] != model {
			t.Fatalf("upstream was sent %v, want %q last", s, model)
		}
	}
}

func orgModelsAuto(candidates []string, pref string, maxIn float64) *tenant.RoutingConfig {
	return &tenant.RoutingConfig{Auto: &tenant.AutoRoutingConfig{Candidates: candidates, Preference: pref, MaxInputPerMTok: maxIn}}
}

func orgModelsCustom(enabled bool, model string, inPerM, outPerM float64) *tenant.CostTrackingConfig {
	return &tenant.CostTrackingConfig{Enabled: enabled, CustomPricing: map[string]*tenant.CustomPricing{
		model: {InputPerMTok: inPerM, OutputPerMTok: outPerM},
	}}
}

func orgModelsOverridePrice(model string, inPerTok, outPerTok float64) *tenant.ModelDatabaseOrgConfig {
	return &tenant.ModelDatabaseOrgConfig{Overrides: map[string]*tenant.ModelOverrideOrgConfig{
		model: {Pricing: &tenant.PricingOverrideOrg{InputPerToken: &inPerTok, OutputPerToken: &outPerTok}},
	}}
}

const orgModelsHello = `{"model":"auto","messages":[{"role":"user","content":"hello"}]}`

// TestAutoOrgPricing: an own model the org priced is a candidate auto can
// pick; the org's prices rank it with the cost plugin's precedence; a model
// nothing prices is still set aside, with the reason.
func TestAutoOrgPricing(t *testing.T) {
	own := []string{orgModelsOwn}
	both := []string{orgModelsOwn, orgModelsPriced}

	t.Run("priced by Cost Tracking alone, sole candidate", func(t *testing.T) {
		up := startOrgModelsUpstream(t)
		srv := orgModelsGateway(t, up, "alpha", own, &tenant.OrgConfig{
			CostTracking: orgModelsCustom(true, orgModelsOwn, 0.05, 0.2), Routing: orgModelsAuto(own, "", 0)})
		for _, pref := range []string{"", "cost", "quality"} {
			wantAutoPick(t, orgModelsChat(t, srv, orgModelsHello, map[string]string{autoPreferenceHeader: pref}), up, orgModelsOwn)
		}
	})
	t.Run("priced by Model Database alone, sole candidate", func(t *testing.T) {
		up := startOrgModelsUpstream(t)
		srv := orgModelsGateway(t, up, "alpha", own, &tenant.OrgConfig{
			ModelDatabase: orgModelsOverridePrice(orgModelsOwn, 0.05e-6, 0.2e-6), Routing: orgModelsAuto(own, "", 0)})
		wantAutoPick(t, orgModelsChat(t, srv, orgModelsHello, nil), up, orgModelsOwn)
	})
	t.Run("the default pool (no candidates listed) takes the priced own model", func(t *testing.T) {
		up := startOrgModelsUpstream(t)
		srv := orgModelsGateway(t, up, "alpha", own, &tenant.OrgConfig{CostTracking: orgModelsCustom(true, orgModelsOwn, 0.05, 0.2)})
		wantAutoPick(t, orgModelsChat(t, srv, orgModelsHello, nil), up, orgModelsOwn)
	})
	t.Run("an input cap of $0.10/M keeps the own model at $0.05/M and sets aside gpt-4o-mini at $0.15/M", func(t *testing.T) {
		up := startOrgModelsUpstream(t)
		srv := orgModelsGateway(t, up, "alpha", both, &tenant.OrgConfig{
			CostTracking: orgModelsCustom(true, orgModelsOwn, 0.05, 0.2), Routing: orgModelsAuto(both, "cost", 0.10)})
		wantAutoPick(t, orgModelsChat(t, srv, orgModelsHello, nil), up, orgModelsOwn)
	})
	t.Run("Cost Tracking wins over Model Database (the cost plugin's precedence)", func(t *testing.T) {
		// Cost Tracking $0.05/M is under the $0.10/M cap; the Model Database's
		// $0.50/M is over it. Auto must judge the price the call is billed at.
		org := func(costTrackingOn bool) *tenant.OrgConfig {
			return &tenant.OrgConfig{
				CostTracking:  orgModelsCustom(costTrackingOn, orgModelsOwn, 0.05, 0.2),
				ModelDatabase: orgModelsOverridePrice(orgModelsOwn, 0.5e-6, 2e-6),
				Routing:       orgModelsAuto(own, "cost", 0.10),
			}
		}
		up := startOrgModelsUpstream(t)
		wantAutoPick(t, orgModelsChat(t, orgModelsGateway(t, up, "alpha", own, org(true)), orgModelsHello, nil), up, orgModelsOwn)

		// With cost tracking off its price is not the org's: the Model
		// Database's $0.50/M applies and is over the cap.
		w := orgModelsChat(t, orgModelsGateway(t, startOrgModelsUpstream(t), "alpha", own, org(false)), orgModelsHello, nil)
		code, msg := orgModelsErr(w)
		if w.Code != http.StatusBadRequest || code != "no_auto_candidate" || !strings.Contains(msg, orgModelsOwn) {
			t.Fatalf("cost tracking off: %d %s %q; want 400 no_auto_candidate naming %s over the cap", w.Code, code, msg, orgModelsOwn)
		}
	})
	t.Run("a model nothing prices is still set aside, and the reason names it", func(t *testing.T) {
		up := startOrgModelsUpstream(t)
		srv := orgModelsGateway(t, up, "alpha", own, &tenant.OrgConfig{Routing: orgModelsAuto(own, "", 0)})
		w := orgModelsChat(t, srv, orgModelsHello, nil)
		code, msg := orgModelsErr(w)
		if w.Code != http.StatusBadRequest || code != "no_auto_candidate" || !strings.Contains(msg, orgModelsOwn+": no price or capability data") {
			t.Fatalf("unpriced own model: %d %s %q", w.Code, code, msg)
		}
		if n := len(up.seen()); n != 0 {
			t.Fatalf("upstream called %d times", n)
		}
	})
}

// TestAutoOrgPricingPresetProviders: an org whose only provider is a
// Together or Fireworks preset can use auto (the model database keys their
// models "together_ai/..." and "fireworks_ai/...").
func TestAutoOrgPricingPresetProviders(t *testing.T) {
	for _, c := range []struct{ provider, model string }{
		{"together", "meta-llama/Llama-3.3-70B-Instruct-Turbo"},
		{"fireworks", "accounts/fireworks/models/llama-v3p3-70b-instruct"},
	} {
		t.Run(c.provider, func(t *testing.T) {
			up := startOrgModelsUpstream(t)
			srv := orgModelsGateway(t, up, c.provider, []string{c.model}, &tenant.OrgConfig{})
			wantAutoPick(t, orgModelsChat(t, srv, orgModelsHello, nil), up, c.model)
		})
	}
}

// TestAutoOrgPricingManagedUnchanged: an org's price for a managed model
// never moves auto (a managed call is billed on FutureAGI's card): an
// override that would make Claude the cheapest leaves cost preference on
// qwen flash.
func TestAutoOrgPricingManagedUnchanged(t *testing.T) {
	org := managedOptIn()
	org.Routing = orgModelsAuto(nil, "cost", 0)
	org.ModelDatabase = orgModelsOverridePrice("anthropic/claude-sonnet-5.5", 1e-12, 1e-12)
	org.CostTracking = orgModelsCustom(true, "anthropic/claude-sonnet-5.5", 0.000001, 0.000001)
	g := newManagedTestGateway(t, org)
	g.grant(t, "org-m", 1_000_000)
	w := g.chat(t, chatBody("auto"), nil)
	if w.Code != http.StatusOK || w.Header().Get("x-agentcc-auto-model") != "qwen/qwen3.7-flash" {
		t.Fatalf("auto -> %d %q (%s); the org's price must not move a managed pick off qwen/qwen3.7-flash",
			w.Code, w.Header().Get("x-agentcc-auto-model"), w.Body.String())
	}
}

// orgModelsManaged is the managed test gateway (catalog: claude-sonnet-5.5 text
// and image with reasoning, gpt-6-sol text and image, qwen3.7-flash text
// only) for an opted-in org with auto preference pref.
func orgModelsManaged(t *testing.T, pref string) *managedTestGateway {
	t.Helper()
	org := managedOptIn()
	if pref != "" {
		org.Routing = orgModelsAuto(nil, pref, 0)
	}
	g := newManagedTestGateway(t, org)
	g.grant(t, "org-m", 1_000_000)
	return g
}

func wantManagedPick(t *testing.T, w *httptest.ResponseRecorder, model string) {
	t.Helper()
	if w.Code != http.StatusOK || w.Header().Get("x-agentcc-auto-model") != model {
		code, msg := orgModelsErr(w)
		t.Fatalf("auto -> %d, auto-model %q (reason %q, error %s %q); want %q",
			w.Code, w.Header().Get("x-agentcc-auto-model"), w.Header().Get("x-agentcc-auto-reason"), code, msg, model)
	}
}

// TestRouteHint: route-hint moves auto as x-agentcc-auto-preference does;
// the header wins over it; latency is refused under auto, and accepted
// (recorded) on a named model.
func TestRouteHint(t *testing.T) {
	const claude, qwen = "anthropic/claude-sonnet-5.5", "qwen/qwen3.7-flash"

	t.Run("quality moves the pick as the preference header does", func(t *testing.T) {
		g := orgModelsManaged(t, "")
		wantManagedPick(t, g.chat(t, chatBody("auto"), nil), qwen)
		wantManagedPick(t, g.chat(t, chatBody("auto"), map[string]string{autoPreferenceHeader: "quality"}), claude)
		w := g.chat(t, chatBody("auto"), map[string]string{"x-agentcc-route-hint": "quality"})
		wantManagedPick(t, w, claude)
		if !strings.Contains(w.Header().Get("x-agentcc-auto-reason"), "quality preference") {
			t.Fatalf("auto reason %q does not name the quality preference", w.Header().Get("x-agentcc-auto-reason"))
		}
	})
	t.Run("cost and balanced move the pick off an org preference of quality", func(t *testing.T) {
		g := orgModelsManaged(t, "quality")
		wantManagedPick(t, g.chat(t, chatBody("auto"), nil), claude)
		wantManagedPick(t, g.chat(t, chatBody("auto"), map[string]string{"x-agentcc-route-hint": "cost"}), qwen)
		wantManagedPick(t, g.chat(t, chatBody("auto"), map[string]string{"x-omega-route-hint": "balanced"}), qwen)
	})
	t.Run("x-agentcc-auto-preference wins over route-hint", func(t *testing.T) {
		g := orgModelsManaged(t, "")
		wantManagedPick(t, g.chat(t, chatBody("auto"), map[string]string{
			autoPreferenceHeader: "quality", "x-agentcc-route-hint": "cost"}), claude)
		wantManagedPick(t, g.chat(t, chatBody("auto"), map[string]string{
			autoPreferenceHeader: "cost", "x-agentcc-route-hint": "quality"}), qwen)
	})
	t.Run("latency is refused under auto and names the value", func(t *testing.T) {
		g := orgModelsManaged(t, "")
		w := g.chat(t, chatBody("auto"), map[string]string{"x-agentcc-route-hint": "latency"})
		code, msg := orgModelsErr(w)
		if w.Code != http.StatusBadRequest || code != "route_hint_unsupported" || !strings.Contains(msg, `"latency"`) {
			t.Fatalf("route-hint latency under auto -> %d %s %q; want 400 route_hint_unsupported naming \"latency\"", w.Code, code, msg)
		}
		if m := g.managed.lastModel(); m != "" {
			t.Fatalf("upstream called (last model %q)", m)
		}
		// With the preference header the hint is not used, so nothing is refused.
		wantManagedPick(t, g.chat(t, chatBody("auto"), map[string]string{
			autoPreferenceHeader: "quality", "x-agentcc-route-hint": "latency"}), claude)
		// On a named model the hint moves nothing and is accepted.
		if w := g.chat(t, chatBody(qwen), map[string]string{"x-agentcc-route-hint": "latency"}); w.Code != http.StatusOK {
			t.Fatalf("route-hint latency on a named model -> %d %s", w.Code, w.Body.String())
		}
	})
}

// TestRequireCapability: require sets aside every candidate that lacks a
// required capability and names the requirement when none has it; the
// org's Model Database capability switches count.
func TestRequireCapability(t *testing.T) {
	const claude, sol, qwen = "anthropic/claude-sonnet-5.5", "openai/gpt-6-sol", "qwen/qwen3.7-flash"

	t.Run("vision leaves only the models with image input", func(t *testing.T) {
		g := orgModelsManaged(t, "")
		w := g.chat(t, chatBody("auto"), map[string]string{"x-agentcc-require": "vision"})
		if got := w.Header().Get("x-agentcc-auto-model"); w.Code != http.StatusOK || (got != claude && got != sol) {
			t.Fatalf("require vision -> %d %q (%s); want claude or gpt-6-sol, never %s", w.Code, got, w.Body.String(), qwen)
		}
		if !strings.Contains(w.Header().Get("x-agentcc-auto-reason"), "requires vision") {
			t.Fatalf("auto reason %q does not name the requirement", w.Header().Get("x-agentcc-auto-reason"))
		}
	})
	t.Run("vision and reasoning leave only claude", func(t *testing.T) {
		g := orgModelsManaged(t, "")
		wantManagedPick(t, g.chat(t, chatBody("auto"), map[string]string{"x-agentcc-require": "vision,reasoning"}), claude)
	})
	t.Run("audio: no model has it, 400 no_auto_candidate naming audio", func(t *testing.T) {
		g := orgModelsManaged(t, "")
		w := g.chat(t, chatBody("auto"), map[string]string{"x-agentcc-require": "audio"})
		code, msg := orgModelsErr(w)
		if w.Code != http.StatusBadRequest || code != "no_auto_candidate" || !strings.Contains(msg, "audio") {
			t.Fatalf("require audio -> %d %s %q; want 400 no_auto_candidate naming audio", w.Code, code, msg)
		}
		if m := g.managed.lastModel(); m != "" {
			t.Fatalf("upstream called (last model %q)", m)
		}
	})
	t.Run("the org's switch turning vision off for claude counts", func(t *testing.T) {
		off := false
		org := managedOptIn()
		org.ModelDatabase = &tenant.ModelDatabaseOrgConfig{Overrides: map[string]*tenant.ModelOverrideOrgConfig{
			claude: {Capabilities: &tenant.CapabilityOverrideOrg{Vision: &off}},
		}}
		g := newManagedTestGateway(t, org)
		g.grant(t, "org-m", 1_000_000)
		w := g.chat(t, chatBody("auto"), map[string]string{"x-agentcc-require": "vision,reasoning"})
		code, msg := orgModelsErr(w)
		if w.Code != http.StatusBadRequest || code != "no_auto_candidate" || !strings.Contains(msg, "vision") {
			t.Fatalf("require vision,reasoning with claude's vision off -> %d %s %q; want 400 no_auto_candidate", w.Code, code, msg)
		}
		wantManagedPick(t, g.chat(t, chatBody("auto"), map[string]string{"x-agentcc-require": "vision"}), sol)
	})
}
