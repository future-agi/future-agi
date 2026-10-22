package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// upstreamCost is what the mock managed upstream reports charging per request.
const upstreamCost = 0.007

// recordingUpstream is an OpenAI-compatible upstream that reports a cost on
// every response, as OpenRouter does, and remembers what it was sent.
type recordingUpstream struct {
	*httptest.Server
	mu      sync.Mutex
	models  []string
	auths   []string
	status  atomic.Int32 // non-zero: reply with this status
	failFor sync.Map     // model → status to reply with for that model
	noUsage atomic.Bool  // streams end without a usage chunk
}

func (u *recordingUpstream) calls() int {
	u.mu.Lock()
	defer u.mu.Unlock()
	return len(u.models)
}

func (u *recordingUpstream) lastModel() string {
	u.mu.Lock()
	defer u.mu.Unlock()
	if len(u.models) == 0 {
		return ""
	}
	return u.models[len(u.models)-1]
}

func startRecordingUpstream(t *testing.T) *recordingUpstream {
	t.Helper()
	u := &recordingUpstream{}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasSuffix(r.URL.Path, "/chat/completions") {
			w.WriteHeader(http.StatusOK)
			return
		}
		var req models.ChatCompletionRequest
		_ = json.NewDecoder(r.Body).Decode(&req)
		u.mu.Lock()
		u.models = append(u.models, req.Model)
		u.auths = append(u.auths, r.Header.Get("Authorization"))
		u.mu.Unlock()
		if s, ok := u.failFor.Load(req.Model); ok {
			w.WriteHeader(s.(int))
			w.Write([]byte(`{"error":{"message":"upstream down","type":"server_error"}}`))
			return
		}
		if s := u.status.Load(); s != 0 {
			w.WriteHeader(int(s))
			w.Write([]byte(`{"error":{"message":"upstream down","type":"server_error"}}`))
			return
		}
		usage := fmt.Sprintf(`{"prompt_tokens":1000,"completion_tokens":500,"total_tokens":1500,"cost":%g}`, upstreamCost)
		if req.Stream {
			w.Header().Set("Content-Type", "text/event-stream")
			fmt.Fprintf(w, "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"model\":%q,\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\",\"content\":\"hi\"}}]}\n\n", req.Model)
			if !u.noUsage.Load() {
				fmt.Fprintf(w, "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"model\":%q,\"choices\":[],\"usage\":%s}\n\n", req.Model, usage)
			}
			fmt.Fprint(w, "data: [DONE]\n\n")
			return
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"id":"c1","object":"chat.completion","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"hi"},"finish_reason":"stop"}],"usage":%s}`, req.Model, usage)
	}))
	t.Cleanup(u.Close)
	return u
}

var testCatalogModels = map[string]*catalog.Model{
	"anthropic/claude-sonnet-5.5": {
		ID: "anthropic/claude-sonnet-5.5", Created: 1790000000, ContextLength: 1000000, MaxOutputTokens: 64000,
		InputModalities: []string{"text", "image"}, OutputModalities: []string{"text"},
		SupportedParameters: []string{"tools", "structured_outputs", "reasoning"},
		InputPerToken:       2e-6, OutputPerToken: 10e-6,
	},
	"openai/gpt-6-sol": {
		ID: "openai/gpt-6-sol", Created: 1790000000, ContextLength: 1000000, MaxOutputTokens: 64000,
		InputModalities: []string{"text", "image"}, OutputModalities: []string{"text"},
		SupportedParameters: []string{"tools", "structured_outputs"},
		InputPerToken:       5e-6, OutputPerToken: 25e-6,
	},
	"qwen/qwen3.7-flash": {
		ID: "qwen/qwen3.7-flash", Created: 1790000000, ContextLength: 1000000, MaxOutputTokens: 32000,
		InputModalities: []string{"text"}, OutputModalities: []string{"text"},
		SupportedParameters: []string{"tools"},
		InputPerToken:       0.03e-6, OutputPerToken: 0.13e-6,
	},
	"qwen/qwen3.7-flash:free": {
		ID: "qwen/qwen3.7-flash:free", Created: 1790000000, ContextLength: 1000000,
		OutputModalities: []string{"text"}, SupportedParameters: []string{"tools"},
	},
}

type managedTestGateway struct {
	srv      *Server
	ledger   *creditsplugin.OrgLedger
	credits  *creditsplugin.Plugin
	tenants  *tenant.Store
	managed  *recordingUpstream
	operator *recordingUpstream
}

const (
	managedOrgKey = "sk-agentcc-managed-org"
	adminToken    = "test-admin-token"
)

// newManagedTestGateway builds a gateway with a managed OpenRouter-style
// provider ("openrouter"), an operator provider that is not managed
// ("operator", serving gpt-4o with FutureAGI's key), and one org ("org-m")
// whose key is managedOrgKey. Managed requests are held on an in-memory
// ledger (managed.require_redis=false), priced from the gateway's injected
// prices, and fallback hops reserve through the credits plugin's hooks, as
// wiring_credits.go installs them.
func newManagedTestGateway(t *testing.T, orgCfg *tenant.OrgConfig) *managedTestGateway {
	t.Helper()
	managed := startRecordingUpstream(t)
	operator := startRecordingUpstream(t)

	off := false
	cfg := config.DefaultConfig()
	cfg.Admin.Token = adminToken
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name: "org-user", Key: managedOrgKey, Owner: "user", KeyType: "byok",
		Metadata: map[string]string{"org_id": "org-m"},
	}}
	cfg.Providers["openrouter"] = config.ProviderConfig{
		BaseURL: managed.URL, APIKey: "fagi-openrouter-key", APIFormat: "openai",
		Type: "openrouter", Managed: true, AutoDiscover: &off,
	}
	cfg.Providers["operator"] = config.ProviderConfig{
		BaseURL: operator.URL, APIKey: "fagi-operator-key", APIFormat: "openai",
		Models: []string{"gpt-4o"}, AutoDiscover: &off,
	}
	cfg.Routing.ProviderLock = config.ProviderLockConfig{Enabled: true}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("registry: %v", err)
	}
	store := catalog.NewStore()
	store.Put(&catalog.Snapshot{ProviderID: "openrouter", Source: "bundled", CatalogSource: catalog.SourceOpenRouter,
		FetchedAt: time.Now(), Models: testCatalogModels})

	var mdb atomic.Pointer[modeldb.ModelDB]
	mdb.Store(BuildModelDB(nil, store))
	tenants := tenant.NewStore()
	tenants.Set("org-m", orgCfg)

	keyStore := auth.NewKeyStore(cfg.Auth)
	ledger := creditsplugin.NewOrgLedger(nil)
	cost := costplugin.New(true, mdb.Load, tenants, nil)
	cost.SetManagedMarkup(5)
	credits := creditsplugin.New(true, keyStore)
	credits.SetOrgLedger(ledger, true)

	srv := New(cfg, "", registry, pipeline.NewEngine(cost, credits), keyStore, nil, nil, nil, &mdb, tenants, nil)
	srv.EnableManaged(ManagedSettings{Catalog: store, Ledger: ledger, MarkupPercent: 5, RequireCredits: true})
	h := srv.handlers
	credits.SetPrices(func(providerID, model, endpointID string) (models.PriceCard, bool) {
		return h.Deps().Prices(providerID, model, endpointID)
	})
	credits.SetOrgMaxTokens(h.OrgDefaultMaxTokensEstimate)
	h.SetBilling(credits)
	srv.ready.Store(true)
	return &managedTestGateway{srv: srv, ledger: ledger, credits: credits, tenants: tenants, managed: managed, operator: operator}
}

// outbox is the org's settlement records on the test gateway's memory ledger.
func (g *managedTestGateway) outbox(org string) []map[string]string {
	return g.ledger.Ledger().(*billing.MemoryLedger).Outbox(org)
}

// account is the org's hot-path account.
func (g *managedTestGateway) account(t *testing.T, org string) billing.Account {
	t.Helper()
	a, err := g.ledger.Account(context.Background(), org)
	if err != nil {
		t.Fatal(err)
	}
	return a
}

// grant pushes a granted total to the org's account (a fresh account at
// version 1 when it has none).
func (g *managedTestGateway) grant(t *testing.T, org string, micros int64) {
	t.Helper()
	a := g.account(t, org)
	if _, err := g.ledger.Grant(context.Background(), org, billing.GrantPush{GrantedTotalMicros: micros, Version: a.GrantedVersion + 1}); err != nil {
		t.Fatal(err)
	}
}

func managedOptIn() *tenant.OrgConfig {
	return &tenant.OrgConfig{ManagedModels: &tenant.ManagedModelsConfig{Enabled: true}}
}

func (g *managedTestGateway) chat(t *testing.T, body string, headers map[string]string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewBufferString(body))
	req.Header.Set("Authorization", "Bearer "+managedOrgKey)
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

func chatBody(model string) string {
	return `{"model":"` + model + `","messages":[{"role":"user","content":"hi"}]}`
}

func TestManagedModelIsBilledWithMarkup(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	g.ledger.SetMicros("org-m", auth.USDToMicros(1))

	w := g.chat(t, chatBody("anthropic/claude-sonnet-5.5"), nil)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d; body %s", w.Code, w.Body)
	}
	if g.managed.lastModel() != "anthropic/claude-sonnet-5.5" || g.managed.auths[0] != "Bearer fagi-openrouter-key" {
		t.Fatalf("upstream got model %q auth %q", g.managed.lastModel(), g.managed.auths[0])
	}
	if got := w.Header().Get("x-agentcc-billing-mode"); got != "managed" {
		t.Errorf("billing mode header = %q", got)
	}
	billed := upstreamCost * 1.05
	if got := w.Header().Get("x-agentcc-cost"); got != fmt.Sprintf("%.6f", billed) {
		t.Errorf("x-agentcc-cost = %s, want upstream cost plus 5%% (%.6f)", got, billed)
	}
	var resp models.ChatCompletionResponse
	json.Unmarshal(w.Body.Bytes(), &resp)
	if resp.Usage == nil || resp.Usage.Cost == nil || fmt.Sprintf("%.6f", *resp.Usage.Cost) != fmt.Sprintf("%.6f", billed) {
		t.Errorf("usage.cost must be the billed amount, not the upstream's charge; body %s", w.Body)
	}
	if bal := g.ledger.BalanceUSD("org-m"); fmt.Sprintf("%.6f", bal) != fmt.Sprintf("%.6f", 1-billed) {
		t.Errorf("org balance = %.6f, want %.6f", bal, 1-billed)
	}
}

func TestManagedModelsNeedTheOrgToOptIn(t *testing.T) {
	g := newManagedTestGateway(t, &tenant.OrgConfig{})
	g.ledger.SetMicros("org-m", auth.USDToMicros(1))

	w := g.chat(t, chatBody("anthropic/claude-sonnet-5.5"), nil)

	if w.Code != http.StatusForbidden || g.managed.calls() != 0 {
		t.Fatalf("status = %d, upstream calls = %d; an org that has not opted in must not reach the managed provider", w.Code, g.managed.calls())
	}
}

func TestManagedModelsNeedCredits(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	g.grant(t, "org-m", 0) // an account pushed with nothing granted

	w := g.chat(t, chatBody("anthropic/claude-sonnet-5.5"), nil)

	if w.Code != http.StatusPaymentRequired || !strings.Contains(w.Body.String(), "managed_credits_exhausted") {
		t.Fatalf("status = %d body %s, want 402 managed_credits_exhausted", w.Code, w.Body)
	}
	if g.managed.calls() != 0 {
		t.Fatal("an org with no credit reached the managed provider")
	}
}

// An opted-in org whose account hash is missing (never pushed, or lost with
// Redis) is told billing is being restored, never that it has no credit, and
// the drain is asked to re-push its account.
func TestManagedModelsWithoutAccountGet503(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())

	w := g.chat(t, chatBody("anthropic/claude-sonnet-5.5"), nil)

	var body struct {
		Error struct {
			Code     string         `json:"code"`
			Metadata map[string]any `json:"metadata"`
		} `json:"error"`
	}
	_ = json.Unmarshal(w.Body.Bytes(), &body)
	if w.Code != http.StatusServiceUnavailable || body.Error.Code != "managed_billing_unavailable" ||
		body.Error.Metadata["reason"] != models.ReasonAccountStateMissing || w.Header().Get("Retry-After") != "30" {
		t.Fatalf("status = %d, Retry-After %q, body %s; want 503 managed_billing_unavailable account_state_missing",
			w.Code, w.Header().Get("Retry-After"), w.Body)
	}
	if g.managed.calls() != 0 {
		t.Fatal("an org with no account reached the managed provider")
	}
	if !g.ledger.Ledger().(*billing.MemoryLedger).NeedsPush("org-m") {
		t.Fatal("the org was not added to mc:needs_push")
	}
}

func TestManagedProviderPrefixIsStrippedForUncataloguedModels(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	g.ledger.SetMicros("org-m", auth.USDToMicros(1))

	w := g.chat(t, chatBody("openrouter/x-ai/grok-4.7"), nil)

	if w.Code != http.StatusOK || g.managed.lastModel() != "x-ai/grok-4.7" {
		t.Fatalf("status = %d, upstream model = %q, want x-ai/grok-4.7", w.Code, g.managed.lastModel())
	}
}

func TestManagedStreamWithholdsUpstreamCost(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	g.ledger.SetMicros("org-m", auth.USDToMicros(1))

	w := g.chat(t, `{"model":"anthropic/claude-sonnet-5.5","stream":true,"messages":[{"role":"user","content":"hi"}]}`, nil)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d; body %s", w.Code, w.Body)
	}
	if strings.Contains(w.Body.String(), `"cost":0.007}`) {
		t.Fatalf("the upstream's charge reached the client: %s", w.Body)
	}
	if !strings.Contains(w.Body.String(), `"cost":0.00735}`) {
		t.Fatalf("the final usage chunk should carry the billed cost (0.007 + 5%%): %s", w.Body)
	}
	if bal := g.ledger.BalanceUSD("org-m"); bal >= 1 {
		t.Fatalf("a managed stream was not charged (balance %.6f)", bal)
	}
}

// A customer key must never be served by a global provider it has no
// credentials for — not through a provider lock, not through a fallback.
func TestCustomerKeyCannotLockOntoOperatorProvider(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())

	w := g.chat(t, chatBody("gpt-4o"), map[string]string{"x-agentcc-provider-lock": "operator"})

	if w.Code != http.StatusForbidden || g.operator.calls() != 0 {
		t.Fatalf("status = %d, operator calls = %d", w.Code, g.operator.calls())
	}
}

func TestCustomerKeyFallbackCannotReachOperatorProvider(t *testing.T) {
	cfg := managedOptIn()
	cfg.Routing = &tenant.RoutingConfig{
		FallbackEnabled: true,
		ModelFallbacks:  map[string][]string{"anthropic/claude-sonnet-5.5": {"gpt-4o"}},
	}
	g := newManagedTestGateway(t, cfg)
	g.ledger.SetMicros("org-m", auth.USDToMicros(1))
	g.managed.status.Store(http.StatusServiceUnavailable)

	w := g.chat(t, chatBody("anthropic/claude-sonnet-5.5"), nil)

	if g.operator.calls() != 0 {
		t.Fatalf("the fallback was served with FutureAGI's operator key (status %d)", w.Code)
	}
	if w.Code == http.StatusOK {
		t.Fatal("request succeeded with no permitted fallback")
	}
}

func TestAutoPicksFromTheManagedCatalog(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	g.ledger.SetMicros("org-m", auth.USDToMicros(1))

	w := g.chat(t, chatBody("auto"), nil)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d; body %s", w.Code, w.Body)
	}
	if g.managed.lastModel() != "qwen/qwen3.7-flash" {
		t.Errorf("a simple prompt went to %q, want the economical qwen/qwen3.7-flash", g.managed.lastModel())
	}
	if w.Header().Get("x-agentcc-auto-model") != "qwen/qwen3.7-flash" || w.Header().Get("x-agentcc-auto-tier") != "simple" {
		t.Errorf("auto headers = %q / %q", w.Header().Get("x-agentcc-auto-model"), w.Header().Get("x-agentcc-auto-tier"))
	}
	if w.Header().Get("x-agentcc-billing-mode") != "managed" {
		t.Error("an auto-routed managed request must be billed as managed")
	}

	quality := g.chat(t, chatBody("auto"), map[string]string{autoPreferenceHeader: "quality"})
	if quality.Code != http.StatusOK || g.managed.lastModel() == "qwen/qwen3.7-flash" {
		t.Errorf("quality preference still chose %q", g.managed.lastModel())
	}
}

func TestAutoHonoursTheOrgsCandidatesAndCaps(t *testing.T) {
	cfg := managedOptIn()
	cfg.Routing = &tenant.RoutingConfig{Auto: &tenant.AutoRoutingConfig{
		Preference:       "quality",
		Candidates:       []string{"anthropic/claude-sonnet-5.5", "openai/gpt-6-sol"},
		MaxOutputPerMTok: 20,
	}}
	g := newManagedTestGateway(t, cfg)
	g.ledger.SetMicros("org-m", auth.USDToMicros(1))

	w := g.chat(t, chatBody("auto"), nil)

	if w.Code != http.StatusOK || g.managed.lastModel() != "anthropic/claude-sonnet-5.5" {
		t.Fatalf("status %d, chose %q; gpt-6-sol is over the $20/M output cap", w.Code, g.managed.lastModel())
	}
}

func TestAutoWithoutReachableModelsExplainsWhy(t *testing.T) {
	g := newManagedTestGateway(t, &tenant.OrgConfig{})

	w := g.chat(t, chatBody("auto"), nil)

	if w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "no_auto_candidate") {
		t.Fatalf("status = %d body %s", w.Code, w.Body)
	}
}

func TestCatalogAndOrgCreditsAdminEndpoints(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	admin := func(method, path, body string) *httptest.ResponseRecorder {
		req := httptest.NewRequest(method, path, bytes.NewBufferString(body))
		req.Header.Set("Authorization", "Bearer "+adminToken)
		w := httptest.NewRecorder()
		g.srv.httpServer.Handler.ServeHTTP(w, req)
		return w
	}

	w := admin("GET", "/-/catalog", "")
	var cat struct {
		MarkupPercent float64 `json:"markup_percent"`
		Models        []struct {
			ID                  string   `json:"id"`
			OutputPerMTok       *float64 `json:"output_per_mtok"`
			BilledOutputPerMTok *float64 `json:"billed_output_per_mtok"`
		} `json:"models"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &cat); err != nil || w.Code != http.StatusOK {
		t.Fatalf("catalog: %d %s", w.Code, w.Body)
	}
	if cat.MarkupPercent != 5 || len(cat.Models) != len(testCatalogModels) {
		t.Fatalf("catalog = %+v", cat)
	}
	for _, m := range cat.Models {
		if m.ID == "anthropic/claude-sonnet-5.5" && (*m.OutputPerMTok != 10 || *m.BilledOutputPerMTok != 10.5) {
			t.Errorf("prices = %v / %v, want 10 upstream and 10.5 billed", *m.OutputPerMTok, *m.BilledOutputPerMTok)
		}
	}

	if w := admin("PUT", "/-/orgs/org-m/credits/granted", `{"granted_total_micros": 10000000, "version": 1, "settled_spend_floor_micros": 0}`); !strings.Contains(w.Body.String(), `"applied":true`) {
		t.Fatalf("push granted: %s", w.Body)
	}
	if w := admin("GET", "/-/orgs/org-m/credits", ""); !strings.Contains(w.Body.String(), `"balance_usd":10`) {
		t.Fatalf("read credits: %s", w.Body)
	}
	if w := admin("PUT", "/-/orgs/org-m/credits/granted", `{"granted_total_micros": 3000000, "version": 2, "settled_spend_floor_micros": 0}`); !strings.Contains(w.Body.String(), `"available_micros":3000000`) {
		t.Fatalf("push a newer total: %s", w.Body)
	}
	if w := admin("GET", "/-/orgs/org-m/credits", ""); !strings.Contains(w.Body.String(), `"balance_usd":3`) {
		t.Fatalf("read credits after the push: %s", w.Body)
	}

	req := httptest.NewRequest("GET", "/-/orgs/org-m/credits", nil)
	unauth := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(unauth, req)
	if unauth.Code != http.StatusUnauthorized {
		t.Fatalf("credits without the admin token = %d", unauth.Code)
	}
}

func TestModelListShowsManagedCatalogToOptedInOrgs(t *testing.T) {
	list := func(g *managedTestGateway) map[string]bool {
		req := httptest.NewRequest("GET", "/v1/models", nil)
		req.Header.Set("Authorization", "Bearer "+managedOrgKey)
		w := httptest.NewRecorder()
		g.srv.httpServer.Handler.ServeHTTP(w, req)
		var resp models.ModelListResponse
		json.Unmarshal(w.Body.Bytes(), &resp)
		ids := map[string]bool{}
		for _, m := range resp.Data {
			ids[m.ID] = true
		}
		return ids
	}
	if ids := list(newManagedTestGateway(t, managedOptIn())); !ids["anthropic/claude-sonnet-5.5"] || !ids["auto"] {
		t.Errorf("opted-in org sees %v", ids)
	}
	if ids := list(newManagedTestGateway(t, &tenant.OrgConfig{})); ids["anthropic/claude-sonnet-5.5"] {
		t.Error("an org that has not opted in must not be offered managed models")
	}
}

// Managed models serve chat completions only; other endpoints keep refusing
// a customer key a global provider.
func TestManagedModelsServeChatCompletionsOnly(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	g.ledger.SetMicros("org-m", auth.USDToMicros(1))

	req := httptest.NewRequest("POST", "/v1/embeddings", bytes.NewBufferString(`{"model":"anthropic/claude-sonnet-5.5","input":"hi"}`))
	req.Header.Set("Authorization", "Bearer "+managedOrgKey)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusForbidden || g.managed.calls() != 0 {
		t.Fatalf("status = %d, upstream calls = %d", w.Code, g.managed.calls())
	}
}

func (u *recordingUpstream) lastAuth() string {
	u.mu.Lock()
	defer u.mu.Unlock()
	if len(u.auths) == 0 {
		return ""
	}
	return u.auths[len(u.auths)-1]
}

// An org provider that shares a global provider's ID and cannot be built is
// never replaced by the global instance: a provider lock onto that ID fails
// without calling any upstream.
func TestUnbuildableOrgProviderNeverFallsBackToFutureAGIKeys(t *testing.T) {
	for _, target := range []string{"openrouter", "operator"} {
		t.Run(target, func(t *testing.T) {
			g := newManagedTestGateway(t, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
				target: {APIKey: "org-key", BaseURL: "http://10.255.255.1:8080", APIFormat: "openai", Enabled: true},
			}})

			w := g.chat(t, chatBody("gpt-4o"), map[string]string{"x-agentcc-provider-lock": target})

			if g.managed.calls() != 0 || g.operator.calls() != 0 {
				t.Fatalf("status %d: an upstream was called with FutureAGI's key (managed %d, operator %d)",
					w.Code, g.managed.calls(), g.operator.calls())
			}
			if w.Code == http.StatusOK {
				t.Fatal("request succeeded through a provider the org could not build")
			}
		})
	}
}

// A fallback after the org's own provider served the primary must use the
// org's credentials for the fallback provider too, never FutureAGI's.
func TestFallbackAfterOrgProviderUsesTheOrgsKey(t *testing.T) {
	g := newManagedTestGateway(t, &tenant.OrgConfig{
		Providers: map[string]*tenant.ProviderConfig{
			"operator": {APIKey: "org-operator-key", APIFormat: "openai", Models: []string{"org-model"}, Enabled: true},
		},
		Routing: &tenant.RoutingConfig{
			FallbackEnabled: true,
			ModelFallbacks:  map[string][]string{"org-model": {"gpt-4o"}},
		},
	})
	g.operator.failFor.Store("org-model", http.StatusServiceUnavailable)

	w := g.chat(t, chatBody("org-model"), nil)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d; body %s", w.Code, w.Body)
	}
	if got := g.operator.lastAuth(); got != "Bearer org-operator-key" {
		t.Fatalf("the fallback was sent with %q, want the org's own key", got)
	}
	if got := w.Header().Get("x-agentcc-billing-mode"); got != "byok" {
		t.Errorf("billing mode = %q", got)
	}
}

// A managed stream that ends before the upstream reports usage (client gone,
// upstream failure, guardrail cut) is billed on an estimate, not for free.
func TestManagedStreamWithoutUsageIsBilledOnAnEstimate(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	g.ledger.SetMicros("org-m", auth.USDToMicros(1))
	g.managed.noUsage.Store(true)

	w := g.chat(t, `{"model":"anthropic/claude-sonnet-5.5","stream":true,"messages":[{"role":"user","content":"hi"}]}`, nil)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d; body %s", w.Code, w.Body)
	}
	if bal := g.ledger.BalanceUSD("org-m"); bal >= 1 {
		t.Fatalf("a managed stream with no usage chunk was not charged (balance %.6f)", bal)
	}
}

// presentUsageCost must not write to the response it was handed: on a cache
// hit that is the cache's stored object, shared by concurrent requests.
func TestPresentUsageCostDoesNotMutateTheSharedResponse(t *testing.T) {
	upstream := 0.007
	cached := &models.ChatCompletionResponse{Usage: &models.Usage{PromptTokens: 1, Cost: &upstream}}
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Response = cached
	rc.Metadata["billing_mode"] = "managed"
	rc.Metadata["cost"] = "0.007350"

	presentUsageCost(rc)

	if *cached.Usage.Cost != upstream {
		t.Fatalf("the shared response's usage was rewritten to %v", *cached.Usage.Cost)
	}
	if rc.Response == cached || *rc.Response.Usage.Cost != 0.00735 {
		t.Fatalf("the client's copy should carry the billed cost; got %+v", rc.Response.Usage)
	}
}

// A fallback into a managed model gets the same credit check as a managed
// primary: an org with no credit must not be served once its own provider fails.
func TestManagedFallbackNeedsCredit(t *testing.T) {
	cfg := managedOptIn()
	cfg.Providers = map[string]*tenant.ProviderConfig{
		"operator": {APIKey: "org-operator-key", APIFormat: "openai", Models: []string{"org-model"}, Enabled: true},
	}
	cfg.Routing = &tenant.RoutingConfig{
		FallbackEnabled: true,
		ModelFallbacks:  map[string][]string{"org-model": {"anthropic/claude-sonnet-5.5"}},
	}
	g := newManagedTestGateway(t, cfg)
	g.operator.failFor.Store("org-model", http.StatusServiceUnavailable)

	w := g.chat(t, chatBody("org-model"), nil)

	if g.managed.calls() != 0 {
		t.Fatalf("status %d: a managed fallback was served to an org with no credit", w.Code)
	}
	if bal := g.ledger.BalanceUSD("org-m"); bal != 0 {
		t.Fatalf("balance = %.6f, want untouched", bal)
	}
}

// Deferred runs resolve their provider from the global registry when they
// run, so they are refused to keys limited to their org's providers.
func TestAsyncIsRefusedToOrgKeys(t *testing.T) {
	g := newManagedTestGateway(t, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"operator": {APIKey: "org-operator-key", APIFormat: "openai", Models: []string{"gpt-4o"}, Enabled: true},
	}})

	w := g.chat(t, chatBody("gpt-4o"), map[string]string{"x-agentcc-async": "true"})

	time.Sleep(50 * time.Millisecond) // a queued job would run in the background
	if w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "deferred_run_unavailable") {
		t.Fatalf("status = %d body %s", w.Code, w.Body)
	}
	if g.operator.calls() != 0 {
		t.Fatal("an async job ran for an org key")
	}
}

// Callers may not set the gateway's own billing and auto-routing metadata.
func TestCallerCannotSetBillingOrAutoMetadata(t *testing.T) {
	for _, key := range []string{"billing_mode", "auto_model", "managed_credits_remaining", "cost_base", "cost_markup_percent", "usage_estimated"} {
		if !isBlockedMetadataKey(key) {
			t.Errorf("%s must be reserved", key)
		}
	}
	for _, key := range []string{"cost_center", "automation", "team"} {
		if isBlockedMetadataKey(key) {
			t.Errorf("%s is an ordinary caller dimension", key)
		}
	}
}

// A cached answer costs nothing, and the stored charge is the upstream cost
// of the call that filled the entry: a cache hit never shows it, whatever the billing mode.
func TestCacheHitNeverShowsTheStoredCost(t *testing.T) {
	upstream := 0.007
	cached := &models.ChatCompletionResponse{Usage: &models.Usage{PromptTokens: 1, Cost: &upstream}}
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Response = cached
	rc.Flags.CacheHit = true
	rc.Metadata["billing_mode"] = "byok"

	presentUsageCost(rc)

	if rc.Response.Usage.Cost != nil || cached.Usage.Cost == nil {
		t.Fatalf("client copy cost = %v, stored cost = %v", rc.Response.Usage.Cost, cached.Usage.Cost)
	}
}

// TestManaged402Golden: the 402 a managed request with no credit gets is the
// error-402-v1 golden, end to end through the credits plugin. The model is
// uncatalogued, so it is held at managed.unpriced_reserve_usd, here $0.04.
func TestManaged402Golden(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	settings := creditsplugin.DefaultSettings()
	settings.UnpricedReserveUSD = 0.04
	g.credits.SetSettings(settings)
	g.grant(t, "org-m", 0)

	w := g.chat(t, chatBody("openrouter/x-ai/grok-4.7"), nil)

	if w.Code != http.StatusPaymentRequired || w.Header().Get("Retry-After") != "" {
		t.Fatalf("status %d, Retry-After %q, body %s", w.Code, w.Header().Get("Retry-After"), w.Body)
	}
	golden, err := os.ReadFile(filepath.Join("..", "..", "..", "api_contracts", "gateway", "examples", "error-402-v1.json"))
	if err != nil {
		t.Fatal(err)
	}
	var got, want any
	if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(golden, &want); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("402 body:\n got %s\nwant %s", w.Body.Bytes(), golden)
	}
	if g.managed.calls() != 0 {
		t.Fatal("a refused request reached the managed provider")
	}
}

// A managed fallback hop takes its own hold through the credits plugin's
// ReserveAdd, once: a refused hop is skipped, never retried or held twice,
// and the request returns the attempt before it; an admitted hop's hold is
// settled at what it billed.
func TestReserveAddOncePerHop(t *testing.T) {
	newFallbackGateway := func(t *testing.T) (*managedTestGateway, *atomic.Int32) {
		cfg := managedOptIn()
		cfg.Providers = map[string]*tenant.ProviderConfig{
			"operator": {APIKey: "org-operator-key", APIFormat: "openai", Models: []string{"org-model"}, Enabled: true},
		}
		cfg.Routing = &tenant.RoutingConfig{
			FallbackEnabled: true,
			ModelFallbacks:  map[string][]string{"org-model": {"anthropic/claude-sonnet-5.5"}},
		}
		g := newManagedTestGateway(t, cfg)
		g.operator.failFor.Store("org-model", http.StatusServiceUnavailable)
		var asked atomic.Int32
		g.srv.handlers.hookObserver = func(hook, _ string, _ *models.RequestContext) {
			if hook == hookReserveAdd {
				asked.Add(1)
			}
		}
		return g, &asked
	}

	t.Run("refused hop", func(t *testing.T) {
		g, asked := newFallbackGateway(t)
		// A positive balance (so the fallback resolves to the managed model)
		// below the hop's worst case of about $0.043.
		g.grant(t, "org-m", 1_000)

		w := g.chat(t, chatBody("org-model"), nil)

		if n := asked.Load(); n != 1 {
			t.Fatalf("ReserveAdd ran %d times for one hop, want 1", n)
		}
		if g.managed.calls() != 0 {
			t.Fatal("a hop whose hold was refused was dispatched")
		}
		if w.Code == http.StatusOK || !strings.Contains(w.Body.String(), "upstream down") {
			t.Fatalf("status %d body %s; want the primary attempt's error", w.Code, w.Body)
		}
		if a := g.account(t, "org-m"); a.HeldMicros != 0 || a.SpentMicros != 0 {
			t.Fatalf("account = %+v, want nothing held or spent", a)
		}
		if out := g.outbox("org-m"); len(out) != 0 {
			t.Fatalf("outbox = %v, want nothing settled for a request that held nothing", out)
		}
	})

	t.Run("admitted hop", func(t *testing.T) {
		g, asked := newFallbackGateway(t)
		g.grant(t, "org-m", 1_000_000)

		w := g.chat(t, chatBody("org-model"), nil)

		if w.Code != http.StatusOK || asked.Load() != 1 || g.managed.calls() != 1 {
			t.Fatalf("status %d, ReserveAdd %d, managed calls %d; body %s", w.Code, asked.Load(), g.managed.calls(), w.Body)
		}
		const billed = 7_350 // the upstream's $0.007 plus 5%
		if a := g.account(t, "org-m"); a.HeldMicros != 0 || a.SpentMicros != billed {
			t.Fatalf("account = %+v, want the hop's hold settled at %d", a, billed)
		}
		out := g.outbox("org-m")
		if len(out) != 1 || out[0]["kind"] != "spend" || out[0]["billed_micros"] != fmt.Sprint(billed) {
			t.Fatalf("outbox = %v, want one spend record", out)
		}
	})
}

// The credits plugin holds a managed request with no max_tokens at the org's
// default_max_tokens_estimate, read from its stored config. 2000, above the
// floor (billing.MinOrgDefaultMaxOut); a value below it holds the
// floor (managed_estimate_floor_test.go).
func TestOrgDefaultMaxTokensEstimate(t *testing.T) {
	estimate := 2000
	cfg := managedOptIn()
	cfg.ManagedModels.DefaultMaxTokensEstimate = &estimate
	g := newManagedTestGateway(t, cfg)
	h := g.srv.handlers
	if got := h.OrgDefaultMaxTokensEstimate("org-m"); got != 2000 {
		t.Fatalf("org-m = %d, want 2000", got)
	}
	if got := h.OrgDefaultMaxTokensEstimate("org-unknown"); got != 0 {
		t.Fatalf("an org with no config = %d, want 0", got)
	}
	g.grant(t, "org-m", 1_000_000)
	g.managed.status.Store(http.StatusServiceUnavailable) // keep the hold: the request fails, so it is voided
	var held int64
	g.credits.SetMaxOutput(func(string, string) int { return 64_000 })
	w := g.chat(t, chatBody("anthropic/claude-sonnet-5.5"), nil)
	if w.Code == http.StatusOK {
		t.Fatal("the failing upstream served the request")
	}
	out := g.outbox("org-m")
	if len(out) != 1 || out[0]["kind"] != "void" {
		t.Fatalf("outbox = %v, want one void", out)
	}
	fmt.Sscan(out[0]["hold_micros"], &held)
	// "hi" is 8 input tokens: (8 × $2 + 2000 × $10) per million × 1.05 =
	// 21,016.8 → 21,017 micros, the org's 2000 tokens under the model's 64,000.
	if held != 21_017 {
		t.Fatalf("held %d micros, want 21017 at the org's 2000-token estimate", held)
	}
}
