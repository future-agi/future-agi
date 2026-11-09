package server

// R7-ORGMODELS: model "auto" as an org key sees it.
//
//   - NG-33: auto prices a candidate from the org's Cost Tracking prices
//     (custom_pricing) and its Model Database overrides (NG-14's overlay),
//     with the cost plugin's precedence; Add Provider preset ids
//     ("together", "fireworks") find their models under the model
//     database's prefixes ("together_ai/", "fireworks_ai/"); a candidate
//     nothing prices is still set aside with its reason; a managed model's
//     price is never the org's.
//   - NG-27: the agent context's route-hint (cost, balanced, quality) is
//     auto's preference when no x-agentcc-auto-preference is sent, and
//     "latency" is refused under auto; require filters the pool by
//     capability and names the requirement when nothing qualifies.
//
// The harness is the no-gimmick CA-F13 probe's (console-a-b3/
// caf13_probe_test.go) and VB1 G4's (gateway-b1/probe), rebuilt on helpers
// that exist at the base so the tests run red there.

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
	r7omOrg    = "org-r7om"
	r7omKey    = "sk-agentcc-r7om-customer-0123456789abcdef0123"
	r7omOwn    = "my-llama-70b" // a self-hosted name no model database knows
	r7omPriced = "gpt-4o-mini"  // in the bundled model database ($0.15 / $0.60 per 1M)
)

// r7omUpstream answers every chat completion and records the model and the
// raw body it was sent.
type r7omUpstream struct {
	*httptest.Server
	mu     sync.Mutex
	models []string
	bodies []map[string]json.RawMessage
}

func (u *r7omUpstream) seen() []string {
	u.mu.Lock()
	defer u.mu.Unlock()
	return append([]string(nil), u.models...)
}

func (u *r7omUpstream) lastBody() map[string]json.RawMessage {
	u.mu.Lock()
	defer u.mu.Unlock()
	if len(u.bodies) == 0 {
		return nil
	}
	return u.bodies[len(u.bodies)-1]
}

func startR7omUpstream(t *testing.T) *r7omUpstream {
	t.Helper()
	u := &r7omUpstream{}
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
		_, _ = io.WriteString(w, `{"id":"chatcmpl-r7om","object":"chat.completion","created":1,"model":"`+model+
			`","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],`+
			`"usage":{"prompt_tokens":1000,"completion_tokens":500,"total_tokens":1500}}`)
	}))
	t.Cleanup(u.Close)
	return u
}

// r7omGateway serves providerID (on the node, layered by the org's own
// provider of the same id, which holds the org's key) listing models, to a
// customer key of r7omOrg (key type byok, as Django mints them). extra
// plugins run after auth.
func r7omGateway(t *testing.T, up *r7omUpstream, providerID string, models []string, org *tenant.OrgConfig, extra ...pipeline.Plugin) *Server {
	t.Helper()
	off := false
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "customer", Key: r7omKey, Owner: r7omOrg, KeyType: "byok", Metadata: map[string]string{"org_id": r7omOrg}},
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
	tenants.Set(r7omOrg, org)
	plugins := append([]pipeline.Plugin{authplugin.New(keyStore, true)}, extra...)
	srv := New(cfg, "", registry, pipeline.NewEngine(plugins...), keyStore, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	return srv
}

func r7omChat(t *testing.T, srv *Server, body string, hdr map[string]string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+r7omKey)
	req.Header.Set("Content-Type", "application/json")
	for k, v := range hdr {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// r7omErr is the response's error code and message.
func r7omErr(w *httptest.ResponseRecorder) (string, string) {
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
func wantAutoPick(t *testing.T, w *httptest.ResponseRecorder, up *r7omUpstream, model string) {
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

func r7omAuto(candidates []string, pref string, maxIn float64) *tenant.RoutingConfig {
	return &tenant.RoutingConfig{Auto: &tenant.AutoRoutingConfig{Candidates: candidates, Preference: pref, MaxInputPerMTok: maxIn}}
}

func r7omCustom(enabled bool, model string, inPerM, outPerM float64) *tenant.CostTrackingConfig {
	return &tenant.CostTrackingConfig{Enabled: enabled, CustomPricing: map[string]*tenant.CustomPricing{
		model: {InputPerMTok: inPerM, OutputPerMTok: outPerM},
	}}
}

func r7omOverridePrice(model string, inPerTok, outPerTok float64) *tenant.ModelDatabaseOrgConfig {
	return &tenant.ModelDatabaseOrgConfig{Overrides: map[string]*tenant.ModelOverrideOrgConfig{
		model: {Pricing: &tenant.PricingOverrideOrg{InputPerToken: &inPerTok, OutputPerToken: &outPerTok}},
	}}
}

const r7omHello = `{"model":"auto","messages":[{"role":"user","content":"hello"}]}`

// TestR7AutoOrgPricing: an own model the org priced is a candidate auto can
// pick (CA-F13 cases 1, 3 and 4: 400 no_auto_candidate at the base); the
// org's prices rank it with the cost plugin's precedence; a model nothing
// prices is still set aside, with the reason.
func TestR7AutoOrgPricing(t *testing.T) {
	own := []string{r7omOwn}
	both := []string{r7omOwn, r7omPriced}

	t.Run("priced by Cost Tracking alone, sole candidate", func(t *testing.T) {
		up := startR7omUpstream(t)
		srv := r7omGateway(t, up, "alpha", own, &tenant.OrgConfig{
			CostTracking: r7omCustom(true, r7omOwn, 0.05, 0.2), Routing: r7omAuto(own, "", 0)})
		for _, pref := range []string{"", "cost", "quality"} {
			wantAutoPick(t, r7omChat(t, srv, r7omHello, map[string]string{autoPreferenceHeader: pref}), up, r7omOwn)
		}
	})
	t.Run("priced by Model Database alone, sole candidate", func(t *testing.T) {
		up := startR7omUpstream(t)
		srv := r7omGateway(t, up, "alpha", own, &tenant.OrgConfig{
			ModelDatabase: r7omOverridePrice(r7omOwn, 0.05e-6, 0.2e-6), Routing: r7omAuto(own, "", 0)})
		wantAutoPick(t, r7omChat(t, srv, r7omHello, nil), up, r7omOwn)
	})
	t.Run("the default pool (no candidates listed) takes the priced own model", func(t *testing.T) {
		up := startR7omUpstream(t)
		srv := r7omGateway(t, up, "alpha", own, &tenant.OrgConfig{CostTracking: r7omCustom(true, r7omOwn, 0.05, 0.2)})
		wantAutoPick(t, r7omChat(t, srv, r7omHello, nil), up, r7omOwn)
	})
	t.Run("an input cap of $0.10/M keeps the own model at $0.05/M and sets aside gpt-4o-mini at $0.15/M", func(t *testing.T) {
		up := startR7omUpstream(t)
		srv := r7omGateway(t, up, "alpha", both, &tenant.OrgConfig{
			CostTracking: r7omCustom(true, r7omOwn, 0.05, 0.2), Routing: r7omAuto(both, "cost", 0.10)})
		wantAutoPick(t, r7omChat(t, srv, r7omHello, nil), up, r7omOwn)
	})
	t.Run("Cost Tracking wins over Model Database (the cost plugin's precedence)", func(t *testing.T) {
		// Cost Tracking $0.05/M is under the $0.10/M cap; the Model Database's
		// $0.50/M is over it. Auto must judge the price the call is billed at.
		org := func(costTrackingOn bool) *tenant.OrgConfig {
			return &tenant.OrgConfig{
				CostTracking:  r7omCustom(costTrackingOn, r7omOwn, 0.05, 0.2),
				ModelDatabase: r7omOverridePrice(r7omOwn, 0.5e-6, 2e-6),
				Routing:       r7omAuto(own, "cost", 0.10),
			}
		}
		up := startR7omUpstream(t)
		wantAutoPick(t, r7omChat(t, r7omGateway(t, up, "alpha", own, org(true)), r7omHello, nil), up, r7omOwn)

		// With cost tracking off its price is not the org's: the Model
		// Database's $0.50/M applies and is over the cap.
		w := r7omChat(t, r7omGateway(t, startR7omUpstream(t), "alpha", own, org(false)), r7omHello, nil)
		code, msg := r7omErr(w)
		if w.Code != http.StatusBadRequest || code != "no_auto_candidate" || !strings.Contains(msg, r7omOwn) {
			t.Fatalf("cost tracking off: %d %s %q; want 400 no_auto_candidate naming %s over the cap", w.Code, code, msg, r7omOwn)
		}
	})
	t.Run("a model nothing prices is still set aside, and the reason names it", func(t *testing.T) {
		up := startR7omUpstream(t)
		srv := r7omGateway(t, up, "alpha", own, &tenant.OrgConfig{Routing: r7omAuto(own, "", 0)})
		w := r7omChat(t, srv, r7omHello, nil)
		code, msg := r7omErr(w)
		if w.Code != http.StatusBadRequest || code != "no_auto_candidate" || !strings.Contains(msg, r7omOwn+": no price or capability data") {
			t.Fatalf("unpriced own model: %d %s %q", w.Code, code, msg)
		}
		if n := len(up.seen()); n != 0 {
			t.Fatalf("upstream called %d times", n)
		}
	})
}

// TestR7AutoOrgPricingPresetProviders: an org whose only provider is a
// Together or Fireworks preset can use auto (the model database keys their
// models "together_ai/..." and "fireworks_ai/...").
func TestR7AutoOrgPricingPresetProviders(t *testing.T) {
	for _, c := range []struct{ provider, model string }{
		{"together", "meta-llama/Llama-3.3-70B-Instruct-Turbo"},
		{"fireworks", "accounts/fireworks/models/llama-v3p3-70b-instruct"},
	} {
		t.Run(c.provider, func(t *testing.T) {
			up := startR7omUpstream(t)
			srv := r7omGateway(t, up, c.provider, []string{c.model}, &tenant.OrgConfig{})
			wantAutoPick(t, r7omChat(t, srv, r7omHello, nil), up, c.model)
		})
	}
}

// TestR7AutoOrgPricingManagedUnchanged: an org's price for a managed model
// never moves auto (a managed call is billed on FutureAGI's card): an
// override that would make Claude the cheapest leaves cost preference on
// qwen flash.
func TestR7AutoOrgPricingManagedUnchanged(t *testing.T) {
	org := managedOptIn()
	org.Routing = r7omAuto(nil, "cost", 0)
	org.ModelDatabase = r7omOverridePrice("anthropic/claude-sonnet-5.5", 1e-12, 1e-12)
	org.CostTracking = r7omCustom(true, "anthropic/claude-sonnet-5.5", 0.000001, 0.000001)
	g := newManagedTestGateway(t, org)
	g.grant(t, "org-m", 1_000_000)
	w := g.chat(t, chatBody("auto"), nil)
	if w.Code != http.StatusOK || w.Header().Get("x-agentcc-auto-model") != "qwen/qwen3.7-flash" {
		t.Fatalf("auto -> %d %q (%s); the org's price must not move a managed pick off qwen/qwen3.7-flash",
			w.Code, w.Header().Get("x-agentcc-auto-model"), w.Body.String())
	}
}

// r7omManaged is the managed test gateway (catalog: claude-sonnet-5.5 text
// and image with reasoning, gpt-6-sol text and image, qwen3.7-flash text
// only) for an opted-in org with auto preference pref.
func r7omManaged(t *testing.T, pref string) *managedTestGateway {
	t.Helper()
	org := managedOptIn()
	if pref != "" {
		org.Routing = r7omAuto(nil, pref, 0)
	}
	g := newManagedTestGateway(t, org)
	g.grant(t, "org-m", 1_000_000)
	return g
}

func wantManagedPick(t *testing.T, w *httptest.ResponseRecorder, model string) {
	t.Helper()
	if w.Code != http.StatusOK || w.Header().Get("x-agentcc-auto-model") != model {
		code, msg := r7omErr(w)
		t.Fatalf("auto -> %d, auto-model %q (reason %q, error %s %q); want %q",
			w.Code, w.Header().Get("x-agentcc-auto-model"), w.Header().Get("x-agentcc-auto-reason"), code, msg, model)
	}
}

// TestR7RouteHint: route-hint moves auto as x-agentcc-auto-preference does
// (VB1 G4: it did not at the base); the header wins over it; latency is
// refused under auto, and accepted (recorded) on a named model.
func TestR7RouteHint(t *testing.T) {
	const claude, qwen = "anthropic/claude-sonnet-5.5", "qwen/qwen3.7-flash"

	t.Run("quality moves the pick as the preference header does", func(t *testing.T) {
		g := r7omManaged(t, "")
		wantManagedPick(t, g.chat(t, chatBody("auto"), nil), qwen)
		wantManagedPick(t, g.chat(t, chatBody("auto"), map[string]string{autoPreferenceHeader: "quality"}), claude)
		w := g.chat(t, chatBody("auto"), map[string]string{"x-agentcc-route-hint": "quality"})
		wantManagedPick(t, w, claude)
		if !strings.Contains(w.Header().Get("x-agentcc-auto-reason"), "quality preference") {
			t.Fatalf("auto reason %q does not name the quality preference", w.Header().Get("x-agentcc-auto-reason"))
		}
	})
	t.Run("cost and balanced move the pick off an org preference of quality", func(t *testing.T) {
		g := r7omManaged(t, "quality")
		wantManagedPick(t, g.chat(t, chatBody("auto"), nil), claude)
		wantManagedPick(t, g.chat(t, chatBody("auto"), map[string]string{"x-agentcc-route-hint": "cost"}), qwen)
		wantManagedPick(t, g.chat(t, chatBody("auto"), map[string]string{"x-omega-route-hint": "balanced"}), qwen)
	})
	t.Run("x-agentcc-auto-preference wins over route-hint", func(t *testing.T) {
		g := r7omManaged(t, "")
		wantManagedPick(t, g.chat(t, chatBody("auto"), map[string]string{
			autoPreferenceHeader: "quality", "x-agentcc-route-hint": "cost"}), claude)
		wantManagedPick(t, g.chat(t, chatBody("auto"), map[string]string{
			autoPreferenceHeader: "cost", "x-agentcc-route-hint": "quality"}), qwen)
	})
	t.Run("latency is refused under auto and names the value", func(t *testing.T) {
		g := r7omManaged(t, "")
		w := g.chat(t, chatBody("auto"), map[string]string{"x-agentcc-route-hint": "latency"})
		code, msg := r7omErr(w)
		if w.Code != http.StatusBadRequest || code != "route_hint_unsupported" || !strings.Contains(msg, `"latency"`) {
			t.Fatalf("route-hint latency under auto -> %d %s %q; want 400 route_hint_unsupported naming \"latency\"", w.Code, code, msg)
		}
		if m := g.managed.lastModel(); m != "" {
			t.Fatalf("upstream called (last model %q)", m)
		}
		// With the preference header the hint is not used, so nothing is refused.
		wantManagedPick(t, g.chat(t, chatBody("auto"), map[string]string{
			autoPreferenceHeader: "quality", "x-agentcc-route-hint": "latency"}), claude)
		// On a named model the hint moves nothing and is accepted, as before.
		if w := g.chat(t, chatBody(qwen), map[string]string{"x-agentcc-route-hint": "latency"}); w.Code != http.StatusOK {
			t.Fatalf("route-hint latency on a named model -> %d %s", w.Code, w.Body.String())
		}
	})
}

// TestR7RequireCapability: require sets aside every candidate that lacks a
// required capability (VB1 G4: require vision still chose the text-only
// qwen flash at the base) and names the requirement when none has it; the
// org's Model Database capability switches count (NG-14).
func TestR7RequireCapability(t *testing.T) {
	const claude, sol, qwen = "anthropic/claude-sonnet-5.5", "openai/gpt-6-sol", "qwen/qwen3.7-flash"

	t.Run("vision leaves only the models with image input", func(t *testing.T) {
		g := r7omManaged(t, "")
		w := g.chat(t, chatBody("auto"), map[string]string{"x-agentcc-require": "vision"})
		if got := w.Header().Get("x-agentcc-auto-model"); w.Code != http.StatusOK || (got != claude && got != sol) {
			t.Fatalf("require vision -> %d %q (%s); want claude or gpt-6-sol, never %s", w.Code, got, w.Body.String(), qwen)
		}
		if !strings.Contains(w.Header().Get("x-agentcc-auto-reason"), "requires vision") {
			t.Fatalf("auto reason %q does not name the requirement", w.Header().Get("x-agentcc-auto-reason"))
		}
	})
	t.Run("vision and reasoning leave only claude", func(t *testing.T) {
		g := r7omManaged(t, "")
		wantManagedPick(t, g.chat(t, chatBody("auto"), map[string]string{"x-agentcc-require": "vision,reasoning"}), claude)
	})
	t.Run("audio: no model has it, 400 no_auto_candidate naming audio", func(t *testing.T) {
		g := r7omManaged(t, "")
		w := g.chat(t, chatBody("auto"), map[string]string{"x-agentcc-require": "audio"})
		code, msg := r7omErr(w)
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
		code, msg := r7omErr(w)
		if w.Code != http.StatusBadRequest || code != "no_auto_candidate" || !strings.Contains(msg, "vision") {
			t.Fatalf("require vision,reasoning with claude's vision off -> %d %s %q; want 400 no_auto_candidate", w.Code, code, msg)
		}
		wantManagedPick(t, g.chat(t, chatBody("auto"), map[string]string{"x-agentcc-require": "vision"}), sol)
	})
}
