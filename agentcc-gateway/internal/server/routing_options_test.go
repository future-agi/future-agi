package server

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"regexp"
	"slices"
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
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing/reqopts"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// routingGateway is a gateway whose upstreams keep every body they receive:
// "openrouter" is managed (type openrouter) and is also what the org's BYOK
// OpenRouter key layers onto; "operator" is a plain OpenAI-format provider
// the org can bring its own key for.
type routingGateway struct {
	srv        *Server
	openrouter *hookUpstream
	operator   *hookUpstream
}

const (
	routingOrg      = "org-r"
	routingKey      = "sk-agentcc-routing-org"
	managedModel    = "anthropic/claude-sonnet-5.5"
	byokOperatorKey = "byok-operator-key"
	byokRouterKey   = "byok-openrouter-key"
)

func newRoutingGateway(t *testing.T, orgCfg *tenant.OrgConfig) *routingGateway {
	t.Helper()
	return newFailingRoutingGateway(t, orgCfg, 0, 0)
}

// newFailingRoutingGateway is newRoutingGateway with upstreams that fail
// their first requests with 502.
func newFailingRoutingGateway(t *testing.T, orgCfg *tenant.OrgConfig, openrouterFails, operatorFails int32) *routingGateway {
	t.Helper()
	g := &routingGateway{openrouter: startHookUpstream(t, openrouterFails), operator: startHookUpstream(t, operatorFails)}
	off := false
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "routing", Key: routingKey, Owner: "key-owner", KeyType: "byok",
		Metadata: map[string]string{"org_id": routingOrg}}}
	cfg.Providers["openrouter"] = config.ProviderConfig{BaseURL: g.openrouter.URL, APIKey: "fagi-openrouter-key",
		APIFormat: "openai", Type: "openrouter", Managed: true, AutoDiscover: &off}
	cfg.Providers["operator"] = config.ProviderConfig{BaseURL: g.operator.URL, APIKey: "fagi-operator-key",
		APIFormat: "openai", Models: []string{"gpt-4o"}, AutoDiscover: &off}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("registry: %v", err)
	}
	store := catalog.NewStore()
	store.Put(&catalog.Snapshot{ProviderID: "openrouter", Source: "bundled", FetchedAt: time.Now(), Models: testCatalogModels})
	var mdb atomic.Pointer[modeldb.ModelDB]
	mdb.Store(BuildModelDB(nil, store))
	tenants := tenant.NewStore()
	tenants.Set(routingOrg, orgCfg)
	keyStore := auth.NewKeyStore(cfg.Auth)
	ledger := creditsplugin.NewOrgLedger(nil)
	ledger.SetMicros(routingOrg, auth.USDToMicros(5))
	cost := costplugin.New(true, mdb.Load, tenants, nil)
	credits := creditsplugin.New(true, keyStore)
	credits.SetOrgLedger(ledger, true)
	g.srv = New(cfg, "", registry, pipeline.NewEngine(cost, credits), keyStore, nil, nil, nil, &mdb, tenants, nil)
	g.srv.EnableManaged(ManagedSettings{Catalog: store, Ledger: ledger, MarkupPercent: 5, RequireCredits: true})
	g.srv.ready.Store(true)
	return g
}

// managedOrg has opted in to managed models and holds no keys of its own.
func managedOrg() *tenant.OrgConfig {
	return &tenant.OrgConfig{ManagedModels: &tenant.ManagedModelsConfig{Enabled: true}}
}

// byokOrg brings its own keys: OpenRouter for the managed model's id, the
// operator provider for gpt-4o.
func byokOrg() *tenant.OrgConfig {
	return &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openrouter": {APIKey: byokRouterKey, Enabled: true, Models: []string{managedModel}},
		"operator":   {APIKey: byokOperatorKey, Enabled: true, Models: []string{"gpt-4o"}},
	}}
}

func (g *routingGateway) chat(t *testing.T, body string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", bytes.NewBufferString(body))
	req.Header.Set("Authorization", "Bearer "+routingKey)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// observe reports every hook to fn, with the request it runs on.
func (g *routingGateway) observe(fn func(hook string, rc *models.RequestContext)) {
	g.srv.handlers.hookObserver = func(hook, _ string, rc *models.RequestContext) { fn(hook, rc) }
}

func body(model, fields string) string {
	b := `{"model":"` + model + `","messages":[{"role":"user","content":"hi"}]`
	if fields != "" {
		b += "," + fields
	}
	return b + "}"
}

// apiError decodes an error response's code and param.
func apiError(t *testing.T, w *httptest.ResponseRecorder) (code, param string) {
	t.Helper()
	var e struct {
		Error struct {
			Code  string  `json:"code"`
			Param *string `json:"param"`
		} `json:"error"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &e); err != nil {
		t.Fatalf("decoding error body %s: %v", w.Body, err)
	}
	if e.Error.Param != nil {
		param = *e.Error.Param
	}
	return e.Error.Code, param
}

func expect400(t *testing.T, w *httptest.ResponseRecorder, code, param string) {
	t.Helper()
	if w.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400 %s; body %s", w.Code, code, w.Body)
	}
	if gotCode, gotParam := apiError(t, w); gotCode != code || gotParam != param {
		t.Fatalf("error = %s param %q, want %s param %q", gotCode, gotParam, code, param)
	}
}

// jsonList is v as a JSON array, nil when it is not one.
func jsonList(v any) []any {
	l, _ := v.([]any)
	return l
}

func lastBody(t *testing.T, u *hookUpstream) map[string]any {
	t.Helper()
	bodies := u.received()
	if len(bodies) == 0 {
		t.Fatal("the upstream received nothing")
	}
	return bodies[len(bodies)-1]
}

// routingBodyKeys are the body keys the gateway owns; none may reach an
// upstream that is not OpenRouter.
var routingBodyKeys = []string{"provider", "providerOptions", "models", "fallbacks", "route", "plugins",
	"transforms", "preset", "session_id", "caching", "extra_body", "usage"}

// gatedFieldBodies is a request body fragment for each interpreted field.
var gatedFieldBodies = map[string]string{
	reqopts.FieldProviderOrder:                  `"provider":{"order":["anthropic"]}`,
	reqopts.FieldProviderOnly:                   `"provider":{"only":["anthropic"]}`,
	reqopts.FieldProviderIgnore:                 `"provider":{"ignore":["azure"]}`,
	reqopts.FieldProviderAllowFallbacks:         `"provider":{"allow_fallbacks":false}`,
	reqopts.FieldProviderRequireParameters:      `"provider":{"require_parameters":true}`,
	reqopts.FieldProviderDataCollection:         `"provider":{"data_collection":"deny"}`,
	reqopts.FieldProviderZDR:                    `"provider":{"zdr":true}`,
	reqopts.FieldProviderQuantizations:          `"provider":{"quantizations":["fp8"]}`,
	reqopts.FieldProviderSort:                   `"provider":{"sort":"price"}`,
	reqopts.FieldProviderMaxPrice:               `"provider":{"max_price":{"prompt":1}}`,
	reqopts.FieldProviderPreferredMaxLatency:    `"provider":{"preferred_max_latency":2}`,
	reqopts.FieldProviderPreferredMinThroughput: `"provider":{"preferred_min_throughput":50}`,
	reqopts.FieldGatewayOrder:                   `"providerOptions":{"gateway":{"order":["anthropic"]}}`,
	reqopts.FieldGatewayOnly:                    `"providerOptions":{"gateway":{"only":["anthropic"]}}`,
	reqopts.FieldGatewaySort:                    `"providerOptions":{"gateway":{"sort":"cost"}}`,
	reqopts.FieldGatewayModels:                  `"providerOptions":{"gateway":{"models":["gpt-4o"]}}`,
	reqopts.FieldGatewayTags:                    `"providerOptions":{"gateway":{"tags":["t"]}}`,
	reqopts.FieldGatewayUser:                    `"providerOptions":{"gateway":{"user":"u"}}`,
	reqopts.FieldGatewaySessionID:               `"providerOptions":{"gateway":{"sessionId":"s"}}`,
	reqopts.FieldGatewayZeroDataRetention:       `"providerOptions":{"gateway":{"zeroDataRetention":true}}`,
	reqopts.FieldModels:                         `"models":["gpt-4o"]`,
	reqopts.FieldFallbacks:                      `"fallbacks":[{"model":"gpt-4o"}]`,
	reqopts.FieldPreset:                         `"preset":"cheap"`,
	reqopts.FieldCaching:                        `"caching":"auto"`,
}

// TestRoutingFieldsNeverReachUpstream: on an upstream that is not
// OpenRouter, no routing field, extra_body or gateway field reaches the
// upstream, streamed or not; an interpreted field the gateway does not
// support is refused before any upstream call.
func TestRoutingFieldsNeverReachUpstream(t *testing.T) {
	g := newRoutingGateway(t, byokOrg())
	passThrough := `"plugins":[{"id":"web"}],"transforms":["middle-out"],"route":"fallback","session_id":"s-1",` +
		`"providerOptions":{"openai":{"store":true}},"extra_body":{"reasoning_effort":"low"}`
	for _, stream := range []bool{false, true} {
		fields := passThrough
		if stream {
			fields += `,"stream":true`
		}
		w := g.chat(t, body("gpt-4o", fields))
		if w.Code != http.StatusOK {
			t.Fatalf("stream=%v: status %d body %s", stream, w.Code, w.Body)
		}
		got := lastBody(t, g.operator)
		for _, k := range routingBodyKeys {
			if _, ok := got[k]; ok {
				t.Errorf("stream=%v: %s reached the upstream: %v", stream, k, got)
			}
		}
		if got["reasoning_effort"] != "low" {
			t.Errorf("stream=%v: the hoisted extra_body field is missing: %v", stream, got)
		}
	}
	for _, f := range reqopts.InterpretedFields {
		t.Run(f, func(t *testing.T) {
			before := len(g.operator.received())
			w := g.chat(t, body("gpt-4o", gatedFieldBodies[f]))
			if !reqopts.Supported(f) {
				expect400(t, w, reqopts.CodeNotYetSupported, f)
				if len(g.operator.received()) != before {
					t.Fatal("a refused request reached the upstream")
				}
				return
			}
			if want, refused := eligibilityRefusals[f]; refused {
				expectEligibilityRefusal(t, w, want)
				if len(g.operator.received()) != before {
					t.Fatal("a refused request reached the upstream")
				}
				return
			}
			if w.Code != http.StatusOK {
				t.Fatalf("status %d body %s", w.Code, w.Body)
			}
			for _, k := range routingBodyKeys {
				if _, ok := lastBody(t, g.operator)[k]; ok {
					t.Errorf("%s reached the upstream", k)
				}
			}
		})
	}
}

// eligibilityRefusal is how the eligibility engine refuses a route:
// the status, the error code and, for model_not_permitted, the rule's code
// in metadata.reason.
type eligibilityRefusal struct {
	status       int
	code, reason string
}

// eligibilityRefusals are the registered provider filters gatedFieldBodies
// sends that the operator's unattested BYOK gpt-4o cannot satisfy, so the
// eligibility engine refuses them before any upstream call: they are never
// accepted and then ignored. Every other registered field is served.
var eligibilityRefusals = map[string]eligibilityRefusal{
	reqopts.FieldProviderOnly:           {http.StatusNotFound, "no_provider_matches", "no_provider_matches"},
	reqopts.FieldProviderDataCollection: {http.StatusForbidden, "model_not_permitted", "data_policy_collection"},
	reqopts.FieldProviderZDR:            {http.StatusForbidden, "model_not_permitted", "data_policy_zdr"},
	reqopts.FieldProviderQuantizations:  {http.StatusForbidden, "model_not_permitted", "quantization"},
	reqopts.FieldProviderMaxPrice:       {http.StatusForbidden, "model_not_permitted", "price_cap"},
}

func expectEligibilityRefusal(t *testing.T, w *httptest.ResponseRecorder, want eligibilityRefusal) {
	t.Helper()
	var body struct {
		Error struct {
			Code     string            `json:"code"`
			Metadata map[string]string `json:"metadata"`
		} `json:"error"`
	}
	_ = json.Unmarshal(w.Body.Bytes(), &body)
	if w.Code != want.status || body.Error.Code != want.code || body.Error.Metadata["reason"] != want.reason {
		t.Fatalf("status %d code %q reason %q, body %s; want %d %s reason %s",
			w.Code, body.Error.Code, body.Error.Metadata["reason"], w.Body, want.status, want.code, want.reason)
	}
}

// TestNotYetSupportedRejected: every interpreted field the gateway does not
// support is a 400 naming it, on BYOK and managed requests alike, with
// no upstream call.
func TestNotYetSupportedRejected(t *testing.T) {
	var unsupported []string
	for _, f := range reqopts.InterpretedFields {
		if !reqopts.Supported(f) {
			unsupported = append(unsupported, f)
		}
	}
	if len(unsupported) == 0 {
		t.Skip("every interpreted field is registered")
	}
	for _, c := range []struct {
		name  string
		org   *tenant.OrgConfig
		model string
	}{{"byok", byokOrg(), "gpt-4o"}, {"managed", managedOrg(), managedModel}} {
		g := newRoutingGateway(t, c.org)
		for _, f := range unsupported {
			expect400(t, g.chat(t, body(c.model, gatedFieldBodies[f])), reqopts.CodeNotYetSupported, f)
		}
		if n := len(g.operator.received()) + len(g.openrouter.received()); n != 0 {
			t.Fatalf("%s: %d upstream calls for refused requests", c.name, n)
		}
	}
}

// TestManagedPluginsRejected: plugins, route and transforms are refused on a
// managed request, streamed or not, before it is reserved or dispatched.
func TestManagedPluginsRejected(t *testing.T) {
	g := newRoutingGateway(t, managedOrg())
	for field, fragment := range map[string]string{
		"plugins":    `"plugins":[{"id":"web"}]`,
		"route":      `"route":"fallback"`,
		"transforms": `"transforms":["middle-out"]`,
	} {
		expect400(t, g.chat(t, body(managedModel, fragment)), reqopts.CodeUnsupportedRoutingField, field)
		expect400(t, g.chat(t, body(managedModel, fragment+`,"stream":true`)), reqopts.CodeUnsupportedRoutingField, field)
	}
	if n := len(g.openrouter.received()); n != 0 {
		t.Fatalf("%d refused managed requests reached FutureAGI's OpenRouter account", n)
	}
}

// TestManagedAllowlist: a managed request may send only the allowlisted
// parameters; anything else, typed or not, hoisted or not, is a 400 naming
// it, and an allowlisted request reaches the upstream with nothing else.
func TestManagedAllowlist(t *testing.T) {
	g := newRoutingGateway(t, managedOrg())
	allowed := `"tools":[{"type":"function","function":{"name":"lookup"}}],"tool_choice":"auto","temperature":0.2,` +
		`"max_tokens":32,"seed":7,"reasoning_effort":"low","metadata":{"team":"a"},"n":1,"user":"alice"`
	w := g.chat(t, body(managedModel, allowed))
	if w.Code != http.StatusOK {
		t.Fatalf("allowlisted request: status %d body %s", w.Code, w.Body)
	}
	for k := range lastBody(t, g.openrouter) {
		if k != "provider" && k != "usage" && !strings.Contains(","+allowedKeys+",", ","+k+",") {
			t.Errorf("%s reached the managed upstream", k)
		}
	}
	for param, fragment := range map[string]string{
		"web_search_options": `"web_search_options":{"search_context_size":"high"}`,
		"modalities":         `"modalities":["text","audio"]`,
		"audio":              `"audio":{"voice":"alloy","format":"mp3"}`,
		"prediction":         `"prediction":{"type":"content","content":"x"}`,
		"n":                  `"n":3`,
		"tools[0].type":      `"tools":[{"type":"web_search_20250305","name":"web_search","max_uses":50}]`,
	} {
		before := len(g.openrouter.received())
		expect400(t, g.chat(t, body(managedModel, fragment)), reqopts.CodeUnsupportedFieldForManaged, param)
		if len(g.openrouter.received()) != before {
			t.Fatalf("%s: a refused managed request reached the upstream", param)
		}
	}
}

const allowedKeys = "model,messages,tools,tool_choice,parallel_tool_calls,response_format,temperature,top_p,top_k," +
	"max_tokens,max_completion_tokens,stop,seed,presence_penalty,frequency_penalty,repetition_penalty,logit_bias," +
	"logprobs,top_logprobs,user,stream,stream_options,reasoning,reasoning_effort,metadata,n"

// TestCheckRoutedRequestRefusesManagedFields is checkRoutedRequest on its
// own: it refuses for a request routed as managed and lets a BYOK one pass.
func TestCheckRoutedRequestRefusesManagedFields(t *testing.T) {
	h := &Handlers{}
	for _, c := range []struct {
		fields, mode, code, param string
	}{
		{`"plugins":[{"id":"web"}]`, billingManaged, reqopts.CodeUnsupportedRoutingField, "plugins"},
		{`"web_search_options":{}`, billingManaged, reqopts.CodeUnsupportedFieldForManaged, "web_search_options"},
		{`"tools":[{"type":"web_search_20250305","name":"web_search"}]`, billingManaged, reqopts.CodeUnsupportedFieldForManaged, "tools[0].type"},
		{`"temperature":0.1`, billingManaged, "", ""},
		{`"plugins":[{"id":"web"}],"web_search_options":{}`, billingBYOK, "", ""},
	} {
		rc := models.AcquireRequestContext()
		var req models.ChatCompletionRequest
		if err := json.Unmarshal([]byte(body("m", c.fields)), &req); err != nil {
			t.Fatal(err)
		}
		rc.Request = &req
		if apiErr := h.applyRoutingOptions(rc, &req); apiErr != nil {
			t.Fatal(apiErr.Message)
		}
		rc.Metadata["billing_mode"] = c.mode
		apiErr := h.checkRoutedRequest(rc)
		switch {
		case c.code == "" && apiErr != nil:
			t.Errorf("%s %s: refused %s", c.mode, c.fields, apiErr.Code)
		case c.code != "" && (apiErr == nil || apiErr.Code != c.code || *apiErr.Param != c.param):
			t.Errorf("%s %s: %+v, want %s param %s", c.mode, c.fields, apiErr, c.code, c.param)
		}
		rc.Release()
	}
}

// TestManagedOpenRouterEndpointSet: a managed OpenRouter call carries the
// gateway-built provider object (order and only from rc.Billing.EndpointSet,
// which the eligibility step fills; set directly here), usage.include=true, and the object is
// logged as upstream_provider_object.
func TestManagedOpenRouterEndpointSet(t *testing.T) {
	g := newRoutingGateway(t, managedOrg())
	var logged string
	g.observe(func(hook string, rc *models.RequestContext) {
		switch hook {
		case hookRewriteUpstream:
			rc.Billing.EndpointSet = []models.EndpointRef{
				{ID: "openrouter:anthropic:" + managedModel, UpstreamProvider: "anthropic"},
				{ID: "openrouter:amazon-bedrock:" + managedModel, UpstreamProvider: "amazon-bedrock"},
			}
		case hookSetAgentccHeaders:
			logged = rc.Metadata[metaUpstreamProviderObject]
		}
	})
	w := g.chat(t, body(managedModel, `"tools":[{"type":"function","function":{"name":"f"}}]`))
	if w.Code != http.StatusOK {
		t.Fatalf("status %d body %s", w.Code, w.Body)
	}
	got := lastBody(t, g.openrouter)
	prov, _ := got["provider"].(map[string]any)
	wantSet := []any{"anthropic", "amazon-bedrock"}
	if !slices.Equal(jsonList(prov["order"]), wantSet) || !slices.Equal(jsonList(prov["only"]), wantSet) {
		t.Errorf("provider = %v, want order and only %v", prov, wantSet)
	}
	if prov["allow_fallbacks"] != false || prov["require_parameters"] != true || prov["data_collection"] != "deny" {
		t.Errorf("provider = %v", prov)
	}
	if u, _ := got["usage"].(map[string]any); u["include"] != true {
		t.Errorf("usage = %v, want include true", got["usage"])
	}
	if logged == "" || !strings.Contains(logged, `"allow_fallbacks":false`) {
		t.Errorf("upstream_provider_object metadata = %q", logged)
	}
}

// TestExtraBodyHoisted: a literal extra_body reaches the upstream as
// top-level fields, never as extra_body, on managed and BYOK requests.
func TestExtraBodyHoisted(t *testing.T) {
	g := newRoutingGateway(t, managedOrg())
	w := g.chat(t, body(managedModel, `"extra_body":{"reasoning_effort":"low"}`))
	if w.Code != http.StatusOK {
		t.Fatalf("managed: status %d body %s", w.Code, w.Body)
	}
	got := lastBody(t, g.openrouter)
	if got["reasoning_effort"] != "low" || got["extra_body"] != nil {
		t.Errorf("managed upstream body = %v", got)
	}

	b := newRoutingGateway(t, byokOrg())
	w = b.chat(t, body("gpt-4o", `"extra_body":{"temperature":0.25,"plugins":[{"id":"web"}]}`))
	if w.Code != http.StatusOK {
		t.Fatalf("byok: status %d body %s", w.Code, w.Body)
	}
	got = lastBody(t, b.operator)
	if got["temperature"] != 0.25 || got["extra_body"] != nil || got["plugins"] != nil {
		t.Errorf("byok upstream body = %v", got)
	}
}

func TestExtraBodyConflict400(t *testing.T) {
	g := newRoutingGateway(t, byokOrg())
	expect400(t, g.chat(t, body("gpt-4o", `"temperature":0.5,"extra_body":{"temperature":0.7}`)),
		reqopts.CodeExtraBodyConflict, "extra_body.temperature")
	expect400(t, g.chat(t, body("gpt-4o", `"extra_body":{"stream":true}`)), reqopts.CodeExtraBodyConflict, "extra_body.stream")
	expect400(t, g.chat(t, body("gpt-4o", `"extra_body":"reasoning_effort=low"`)), reqopts.CodeInvalidExtraBody, "extra_body")
	if n := len(g.operator.received()); n != 0 {
		t.Fatalf("%d refused requests reached the upstream", n)
	}
	if w := g.chat(t, body("gpt-4o", `"temperature":0.5,"extra_body":{"temperature":0.5}`)); w.Code != http.StatusOK {
		t.Fatalf("an equal extra_body value was refused: %d %s", w.Code, w.Body)
	}
}

var upstreamUserShape = regexp.MustCompile(`^fa_org_[0-9a-f]{24}$`)

// unkeyedUpstreamUser is the upstream user without a configured hash key.
func unkeyedUpstreamUser(orgID string) string {
	sum := sha256.Sum256([]byte("agentcc:" + orgID))
	return "fa_org_" + hex.EncodeToString(sum[:])[:24]
}

// TestHashedUpstreamUser: a managed attempt sends the hashed org id as
// user, never the caller's, and records it; the caller's user stays on the
// request context for the log. A BYOK attempt sends the caller's user.
func TestHashedUpstreamUser(t *testing.T) {
	g := newRoutingGateway(t, managedOrg())
	var recorded, caller string
	g.observe(func(hook string, rc *models.RequestContext) {
		if hook == hookSetAgentccHeaders {
			recorded, caller = rc.Billing.UpstreamUser, rc.UserID
		}
	})
	if w := g.chat(t, body(managedModel, `"user":"alice@example.com"`)); w.Code != http.StatusOK {
		t.Fatalf("status %d body %s", w.Code, w.Body)
	}
	sent, _ := lastBody(t, g.openrouter)["user"].(string)
	if !upstreamUserShape.MatchString(sent) || sent != reqopts.UpstreamUser(routingOrg) || recorded != sent {
		t.Errorf("upstream user %q, recorded %q, want the hashed org id %q", sent, recorded, reqopts.UpstreamUser(routingOrg))
	}
	if os.Getenv("AGENTCC_MANAGED_USER_HASH_KEY") == "" && sent != unkeyedUpstreamUser(routingOrg) {
		t.Errorf("without a configured key the user must be the unkeyed sha256 form, got %q", sent)
	}
	if caller != "alice@example.com" {
		t.Errorf("rc.UserID = %q, want the caller's user kept for the log", caller)
	}
	if w := g.chat(t, body(managedModel, "")); w.Code != http.StatusOK {
		t.Fatalf("no user: status %d", w.Code)
	} else if got, _ := lastBody(t, g.openrouter)["user"].(string); got != sent {
		t.Errorf("a managed call without a user sent %q, want %q", got, sent)
	}

	b := newRoutingGateway(t, byokOrg())
	if w := b.chat(t, body("gpt-4o", `"user":"alice@example.com"`)); w.Code != http.StatusOK {
		t.Fatalf("byok: status %d", w.Code)
	}
	if got := lastBody(t, b.operator)["user"]; got != "alice@example.com" {
		t.Errorf("byok upstream user = %v, want the caller's", got)
	}
}

// TestSessionIDKeptInRouting: body session_id is read by the agent parse
// step, kept in rc.Routing.SessionID and never sent upstream.
func TestSessionIDKeptInRouting(t *testing.T) {
	g := newRoutingGateway(t, byokOrg())
	var parsed, kept string
	g.observe(func(hook string, rc *models.RequestContext) {
		switch hook {
		case hookParseAgentContext:
			_ = json.Unmarshal(rc.Request.Extra["session_id"], &parsed)
		case hookApplyAgentContext:
			kept = rc.Routing.SessionID
		}
	})
	if w := g.chat(t, body("gpt-4o", `"session_id":"sess-42"`)); w.Code != http.StatusOK {
		t.Fatalf("status %d body %s", w.Code, w.Body)
	}
	if parsed != "sess-42" || kept != "sess-42" {
		t.Errorf("parse step saw %q, rc.Routing.SessionID = %q; want both sess-42", parsed, kept)
	}
	if _, ok := lastBody(t, g.operator)["session_id"]; ok {
		t.Error("session_id reached the upstream")
	}
	expect400(t, g.chat(t, body("gpt-4o", `"session_id":42`)), reqopts.CodeInvalidRoutingField, "session_id")
}

// TestSlugMatchingNotGatewayIDs: the managed provider object names upstream
// slugs, never a gateway provider id, and a gateway id does not match a
// direct provider's upstream slug.
func TestSlugMatchingNotGatewayIDs(t *testing.T) {
	g := newRoutingGateway(t, managedOrg())
	g.observe(func(hook string, rc *models.RequestContext) {
		if hook == hookRewriteUpstream {
			rc.Billing.EndpointSet = []models.EndpointRef{{ID: "openrouter:deepinfra/fp8:" + managedModel, UpstreamProvider: "deepinfra"}}
		}
	})
	if w := g.chat(t, body(managedModel, "")); w.Code != http.StatusOK {
		t.Fatalf("status %d body %s", w.Code, w.Body)
	}
	prov, _ := lastBody(t, g.openrouter)["provider"].(map[string]any)
	if !slices.Equal(jsonList(prov["only"]), []any{"deepinfra/fp8"}) {
		t.Errorf("only = %v, want the upstream slug deepinfra/fp8", prov["only"])
	}
	operator := config.ProviderConfig{APIFormat: "openai", BaseURL: g.operator.URL}
	if slug := reqopts.UpstreamSlug(operator); slug != "openai" || reqopts.SlugMatches("operator", slug, "") || !reqopts.SlugMatches("OpenAI", slug, "") {
		t.Errorf("operator's upstream slug = %q; only the slug, not the gateway id, may match it", slug)
	}
}

// TestByokOpenRouterPassthrough: a BYOK OpenRouter request re-emits the
// caller's plugins, transforms and route (the customer pays) and never a
// gateway-built provider object or usage; another BYOK upstream gets none.
func TestByokOpenRouterPassthrough(t *testing.T) {
	g := newRoutingGateway(t, byokOrg())
	fields := `"plugins":[{"id":"web","max_results":3}],"transforms":["middle-out"],"route":"fallback","user":"alice"`
	if w := g.chat(t, body(managedModel, fields)); w.Code != http.StatusOK {
		t.Fatalf("status %d body %s", w.Code, w.Body)
	}
	got := lastBody(t, g.openrouter)
	plugins, _ := json.Marshal(got["plugins"])
	if string(plugins) != `[{"id":"web","max_results":3}]` || got["route"] != "fallback" || got["transforms"] == nil {
		t.Errorf("BYOK OpenRouter body = %v, want the caller's plugins, transforms and route", got)
	}
	if got["provider"] != nil || got["usage"] != nil || got["user"] != "alice" || got["models"] != nil {
		t.Errorf("BYOK OpenRouter body = %v: no gateway-built provider or usage, and the caller's user", got)
	}
	if w := g.chat(t, body("gpt-4o", fields)); w.Code != http.StatusOK {
		t.Fatalf("operator: status %d", w.Code)
	}
	if op := lastBody(t, g.operator); op["plugins"] != nil || op["transforms"] != nil || op["route"] != nil {
		t.Errorf("a non-OpenRouter upstream got OpenRouter fields: %v", op)
	}
}

// TestFallbackHopRewrittenPerAttempt: each attempt is rewritten for its own
// billing mode. A BYOK primary's fields stop at a managed fallback hop (no
// plugins, nothing outside the allowlist, the hashed user and the pin), and
// a managed primary's hashed user and pin never reach a BYOK fallback.
func TestFallbackHopRewrittenPerAttempt(t *testing.T) {
	withFallback := func(from, to string) *tenant.OrgConfig {
		cfg := managedOrg()
		cfg.Providers = map[string]*tenant.ProviderConfig{
			"operator": {APIKey: byokOperatorKey, Enabled: true, Models: []string{"org-model"}},
		}
		cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, ModelFallbacks: map[string][]string{from: {to}}}
		return cfg
	}
	fields := `"plugins":[{"id":"web"}],"web_search_options":{},"user":"alice",` +
		`"tools":[{"type":"web_search_20250305","name":"web_search","max_uses":50},{"type":"function","function":{"name":"lookup"}}]`

	g := newFailingRoutingGateway(t, withFallback("org-model", managedModel), 0, 1)
	if w := g.chat(t, body("org-model", fields)); w.Code != http.StatusOK {
		t.Fatalf("byok to managed: status %d body %s", w.Code, w.Body)
	}
	byok, managed := lastBody(t, g.operator), lastBody(t, g.openrouter)
	if byok["user"] != "alice" || byok["web_search_options"] == nil || byok["plugins"] != nil {
		t.Errorf("BYOK primary body = %v", byok)
	}
	if managed["plugins"] != nil || managed["web_search_options"] != nil || managed["user"] != reqopts.UpstreamUser(routingOrg) {
		t.Errorf("managed fallback body = %v: no plugins, nothing outside the allowlist, the hashed user", managed)
	}
	if n := len(jsonList(byok["tools"])); n != 2 {
		t.Errorf("BYOK primary got %d tools, want both", n)
	}
	if tools := jsonList(managed["tools"]); len(tools) != 1 || tools[0].(map[string]any)["type"] != "function" {
		t.Errorf("managed fallback tools = %v: only the function tool, never the provider-run one", managed["tools"])
	}
	if prov, _ := managed["provider"].(map[string]any); prov["allow_fallbacks"] != false {
		t.Errorf("managed fallback provider object = %v", managed["provider"])
	}

	g = newFailingRoutingGateway(t, withFallback(managedModel, "org-model"), 1, 0)
	if w := g.chat(t, body(managedModel, `"user":"alice"`)); w.Code != http.StatusOK {
		t.Fatalf("managed to byok: status %d body %s", w.Code, w.Body)
	}
	byok = lastBody(t, g.operator)
	if _, ok := byok["user"]; ok || byok["provider"] != nil || byok["usage"] != nil {
		t.Errorf("BYOK fallback body = %v: no hashed user, provider object or usage from the managed attempt", byok)
	}
}

func TestModelsAndFallbacksTogether400(t *testing.T) {
	g := newRoutingGateway(t, byokOrg())
	expect400(t, g.chat(t, body("gpt-4o", `"models":["gpt-4o"],"fallbacks":[{"model":"gpt-4o"}]`)),
		reqopts.CodeInvalidRoutingField, "fallbacks")
	if len(g.operator.received()) != 0 {
		t.Fatal("a refused request reached the upstream")
	}
}

// TestRoutingHooksAllocateNothingWithoutFields: a request with no routing
// fields costs the two hooks no allocation (the benchmark guard counts
// allocs/op on exactly that request).
func TestRoutingHooksAllocateNothingWithoutFields(t *testing.T) {
	h := &Handlers{}
	rc := models.AcquireRequestContext()
	defer rc.Release()
	req := &models.ChatCompletionRequest{Model: "gpt-4o", Messages: []models.Message{{Role: "user"}}}
	rc.Request = req
	allocs := testing.AllocsPerRun(200, func() {
		_ = h.applyRoutingOptions(rc, req)
		h.rewriteUpstreamRouting(rc, nil, req)
	})
	if allocs != 0 {
		t.Fatalf("the routing hooks allocate %v times on a plain request, want 0", allocs)
	}
}
