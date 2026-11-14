package server

import (
	"encoding/json"
	"math"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The price table keys Perplexity's models under the provider
// ("perplexity/sonar"), and the provider lists them bare ("sonar"). The model
// listings must show that entry, as the cost plugin prices the request.
const deprecatedSonar = "llama-3.1-sonar-small-128k-online" // perplexity/… has a deprecation_date

func createPerplexityTestServer(t *testing.T, mockURL string) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: mockURL, APIFormat: "openai", Models: []string{"gpt-4o"}}
	cfg.Providers["perplexity"] = config.ProviderConfig{BaseURL: mockURL, APIFormat: "openai", Models: []string{"sonar", deprecatedSonar}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	return srv
}

func getJSON(t *testing.T, srv *Server, path string, out any) {
	t.Helper()
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, httptest.NewRequest(http.MethodGet, path, nil))
	if w.Code != http.StatusOK {
		t.Fatalf("GET %s: status = %d, want 200", path, w.Code)
	}
	if err := json.Unmarshal(w.Body.Bytes(), out); err != nil {
		t.Fatalf("GET %s: parsing response: %v", path, err)
	}
}

// checkListedFacts checks what a listing shows for sonar, the deprecated
// sonar model and gpt-4o, which the table keys bare and must stay as it was.
func checkListedFacts(t *testing.T, srv *Server, got map[string]models.EnrichedModelObject) {
	t.Helper()
	if p := got["sonar"].Pricing; p == nil || math.Abs(p.InputPerMTokens-1) > 1e-9 || math.Abs(p.OutputPerMTokens-1) > 1e-9 {
		t.Errorf("sonar pricing = %+v, want perplexity/sonar's $1 per M in and out", p)
	}
	if !got[deprecatedSonar].Deprecated {
		t.Errorf("%s is not shown deprecated", deprecatedSonar)
	}
	want, _ := srv.handlers.ModelDB().GetPricing("gpt-4o")
	if p := got["gpt-4o"].Pricing; p == nil || p.InputPerMTokens != want.InputPerMTok() || p.OutputPerMTokens != want.OutputPerMTok() {
		t.Errorf("gpt-4o pricing = %+v, want the table's %+v", p, want)
	}
}

func TestListModels_ProviderKeyedModelShowsItsEntry(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()
	srv := createPerplexityTestServer(t, mock.URL)

	var resp models.EnrichedModelListResponse
	getJSON(t, srv, "/v1/models", &resp)

	got := map[string]models.EnrichedModelObject{}
	for _, m := range resp.Data {
		got[m.ID] = m
	}
	checkListedFacts(t, srv, got)
}

func TestGetModel_ProviderKeyedModelShowsItsEntry(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()
	srv := createPerplexityTestServer(t, mock.URL)

	got := map[string]models.EnrichedModelObject{}
	for _, id := range []string{"sonar", deprecatedSonar, "gpt-4o"} {
		var m models.EnrichedModelObject
		getJSON(t, srv, "/v1/models/"+id, &m)
		got[id] = m
	}
	checkListedFacts(t, srv, got)
}

// An org's Gemini provider of any name shows gemini-4-argon's price, which the
// table keys only as "gemini/…" and "vertex_ai/…", from the org's config: the
// listing is the same before the org's first request builds the provider and
// after it.
func TestListModels_OrgProviderPriceStableAcrossFirstRequest(t *testing.T) {
	const orgKey = "sk-agentcc-org-gemini"
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "org-user", Key: orgKey, Owner: "user", KeyType: "byok", Metadata: map[string]string{"org_id": "org-1"}}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	// The IP literal passes the base URL check without a lookup; nothing is sent to it.
	pc := &tenant.ProviderConfig{APIKey: "org-key", Enabled: true, BaseURL: "https://8.8.8.8", APIFormat: "gemini", Models: []string{"gemini-4-argon"}}
	store := tenant.NewStore()
	store.Set("org-1", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{"team-gemini": pc}})
	db := testModelDBPtr()
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, db, store, nil)
	srv.ready.Store(true)

	if _, ok := db.Load().Get("gemini-4-argon"); ok {
		t.Fatal("the table now lists gemini-4-argon bare; pick a model it lists only under a provider")
	}
	entry, ok := db.Load().Get("gemini/gemini-4-argon")
	if !ok {
		t.Fatal("the table no longer lists gemini/gemini-4-argon")
	}
	argon := func(when string) models.EnrichedModelObject {
		t.Helper()
		req := httptest.NewRequest(http.MethodGet, "/v1/models", nil)
		req.Header.Set("Authorization", "Bearer "+orgKey)
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		if w.Code != http.StatusOK {
			t.Fatalf("%s: status %d: %s", when, w.Code, w.Body.String())
		}
		var resp models.EnrichedModelListResponse
		if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
			t.Fatalf("%s: parsing response: %v", when, err)
		}
		for _, m := range resp.Data {
			if m.ID == "gemini-4-argon" {
				return m
			}
		}
		t.Fatalf("%s: gemini-4-argon not listed: %s", when, w.Body.String())
		return models.EnrichedModelObject{}
	}

	before := argon("before the first request")
	if p := before.Pricing; p == nil || math.Abs(p.InputPerMTokens-entry.Pricing.InputPerMTok()) > 1e-9 {
		t.Errorf("before the first request: pricing = %+v, want gemini/gemini-4-argon's %v per M in", p, entry.Pricing.InputPerMTok())
	}
	// What the org's first request through team-gemini does.
	if _, err := srv.OrgProviderCache.GetOrCreateWithTenantConfig("org-1", "team-gemini", pc.APIKey, pc); err != nil {
		t.Fatalf("creating org provider: %v", err)
	}
	after := argon("after the first request")
	b, _ := json.Marshal(before)
	a, _ := json.Marshal(after)
	if string(b) != string(a) {
		t.Errorf("listing changed after the first request:\nbefore %s\nafter  %s", b, a)
	}
}
