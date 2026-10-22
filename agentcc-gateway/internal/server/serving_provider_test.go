package server

// x-agentcc-timeout-ms reads the client timeout of the provider instance that
// really served a request (appliedTimeoutMs). Chat passes the instance that
// served (chat_nonstream.go, chat_stream.go). Every other route that sets
// timeout_ms (embeddings, rerank, image, the four audio routes, legacy
// completions and responses) asks servingProvider, which reads only the
// org-provider cache and the registry: orgInstanceServed says whether the
// org's own instance served, and an internal key keeps the operator's.
// These tests pin that answer against the org-only guards (orgProvidersOnly,
// resolveProviderFor's org-only return, the key ACL), for org keys, internal
// keys and an org with its own provider instance, and pin that a refused
// request writes no header and calls nothing.
//
// Every case runs against one upstream that records the bearer each call
// carried: the operator's key or the org's.

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	spOrgA        = "org-sp-a"
	spOrgB        = "org-sp-b"
	spOrgKeyA     = "sk-agentcc-sp-org-a"
	spOrgKeyB     = "sk-agentcc-sp-org-b"
	spInternalKey = "sk-agentcc-sp-internal-a"

	spOperatorBearer = "Bearer platform-openai-key"
	spOrgABearer     = "Bearer org-a-openai-key"
	spOrgBBearer     = "Bearer org-b-openai-key"

	spModel     = "text-embedding-3-small"
	spChatModel = "gpt-4o"
)

// spUpstream answers embeddings, responses and assistants calls and records
// the Authorization header of each call.
type spUpstream struct {
	*httptest.Server
	mu      sync.Mutex
	bearers []string
}

func newSPUpstream(t *testing.T) *spUpstream {
	t.Helper()
	u := &spUpstream{}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost { // provider model discovery (GET /v1/models) is not a request's call
			u.mu.Lock()
			u.bearers = append(u.bearers, r.Method+" "+r.URL.Path+" "+r.Header.Get("Authorization"))
			u.mu.Unlock()
		}
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.URL.Path == "/v1/embeddings":
			fmt.Fprintf(w, `{"object":"list","model":%q,"data":[{"object":"embedding","index":0,"embedding":[0.1,0.2]}],"usage":{"prompt_tokens":1,"total_tokens":1}}`, spModel)
		case r.URL.Path == "/v1/responses":
			fmt.Fprintf(w, `{"id":"resp_sp","object":"response","status":"completed","model":%q,"output":[],"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}`, spChatModel)
		case strings.HasPrefix(r.URL.Path, "/v1/assistants"):
			fmt.Fprint(w, `{"id":"asst_sp","object":"assistant"}`)
		default:
			w.WriteHeader(http.StatusNotFound)
			fmt.Fprint(w, `{"error":{"message":"not found","type":"invalid_request_error"}}`)
		}
	}))
	t.Cleanup(u.Close)
	return u
}

func (u *spUpstream) calls() []string {
	u.mu.Lock()
	defer u.mu.Unlock()
	return append([]string(nil), u.bearers...)
}

// spGateway is one gateway with auth on: an org key and an internal key of
// org A, an org key of org B, and the operator's "openai" provider (client
// timeout 30s) serving the embedding model, with a 120s model timeout, plus
// a second operator provider for another model.
func spGateway(t *testing.T, up *spUpstream, orgs map[string]*tenant.OrgConfig) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "org-a", Key: spOrgKeyA, Owner: "user", KeyType: "byok", Metadata: map[string]string{"org_id": spOrgA}},
		{Name: "org-b", Key: spOrgKeyB, Owner: "user", KeyType: "byok", Metadata: map[string]string{"org_id": spOrgB}},
		{Name: "internal-a", Key: spInternalKey, Owner: "futureagi-backend", KeyType: "internal", Metadata: map[string]string{"org_id": spOrgA}},
	}
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL:        up.URL,
		APIKey:         "platform-openai-key",
		APIFormat:      "openai",
		Models:         []string{spModel, spChatModel},
		DefaultTimeout: 30 * time.Second,
	}
	// A second operator provider, so the registry has no single-provider
	// default and a model no operator provider lists is not found there.
	cfg.Providers["other"] = config.ProviderConfig{
		BaseURL:        up.URL,
		APIKey:         "platform-other-key",
		APIFormat:      "openai",
		Models:         []string{"other-model"},
		DefaultTimeout: 30 * time.Second,
	}
	// An org provider with its own base_url (org-only ids) points at the
	// test upstream on loopback.
	cfg.OrgProviders.AllowPrivateURLs = true
	cfg.Routing.ModelTimeouts = map[string]time.Duration{spModel: 120 * time.Second, spChatModel: 120 * time.Second, "org-only-embedding": 120 * time.Second}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	t.Cleanup(func() { registry.Close() })
	store := tenant.NewStore()
	for id, oc := range orgs {
		store.Set(id, oc)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), store, nil)
	srv.ready.Store(true)
	return srv
}

func spPost(t *testing.T, srv *Server, path, key, body string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, path, bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	if key != "" {
		req.Header.Set("Authorization", "Bearer "+key)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

func spWarm(t *testing.T, srv *Server, orgID, providerID string, pc *tenant.ProviderConfig) {
	t.Helper()
	if _, err := srv.OrgProviderCache.GetOrCreateWithTenantConfig(orgID, providerID, pc.APIKey, pc); err != nil {
		t.Fatalf("warming %s/%s: %v", orgID, providerID, err)
	}
}

func spWantOneCall(t *testing.T, up *spUpstream, bearer string) {
	t.Helper()
	calls := up.calls()
	if len(calls) != 1 || !strings.HasSuffix(calls[0], " "+bearer) {
		t.Fatalf("upstream calls = %q, want exactly one with %q", calls, bearer)
	}
}

const spEmbeddingBody = `{"model":"` + spModel + `","input":"hello"}`

// An org key whose org lists the model is served by the org's own instance
// (the org_provider marker path: the hosted path for every org key on these
// routes), and the header reads that instance's 20s, not the operator's 30s.
func TestOrgKeyOnEmbeddingsReportsTheOrgInstance(t *testing.T) {
	up := newSPUpstream(t)
	srv := spGateway(t, up, map[string]*tenant.OrgConfig{
		spOrgA: {Providers: map[string]*tenant.ProviderConfig{
			"openai": {Enabled: true, APIKey: "org-a-openai-key", Models: []string{spModel}, Timeout: 20},
		}},
	})

	w := spPost(t, srv, "/v1/embeddings", spOrgKeyA, spEmbeddingBody)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200: %s", w.Code, w.Body.String())
	}
	spWantOneCall(t, up, spOrgABearer)
	if got := w.Header().Get("x-agentcc-timeout-ms"); got != "20000" {
		t.Errorf("x-agentcc-timeout-ms = %q, want 20000 (the org instance that served, not the operator's 30000)", got)
	}
}

// An org key whose org does not list the model is refused before any call,
// even with an instance of the org's "openai" already cached (a decoy), and
// the refusal carries no timeout header.
func TestOrgKeyRefusedOnEmbeddingsCallsNothing(t *testing.T) {
	up := newSPUpstream(t)
	other := &tenant.ProviderConfig{Enabled: true, APIKey: "org-a-openai-key", Models: []string{"text-embedding-3-large"}, Timeout: 20}
	srv := spGateway(t, up, map[string]*tenant.OrgConfig{
		spOrgA: {Providers: map[string]*tenant.ProviderConfig{"openai": other}},
	})
	spWarm(t, srv, spOrgA, "openai", other)

	w := spPost(t, srv, "/v1/embeddings", spOrgKeyA, spEmbeddingBody)

	if w.Code != http.StatusForbidden {
		t.Fatalf("status = %d, want 403: %s", w.Code, w.Body.String())
	}
	if calls := up.calls(); len(calls) != 0 {
		t.Fatalf("upstream calls = %q, want none", calls)
	}
	if got := w.Header().Get("x-agentcc-timeout-ms"); got != "" {
		t.Errorf("x-agentcc-timeout-ms = %q on a refusal, want none", got)
	}
}

// An internal key of org A keeps the operator's instance on embeddings even
// when org A lists the model and its instance is cached: the call carries
// the operator's key and the header reads the operator's 30s.
func TestInternalKeyOnEmbeddingsReportsTheOperatorInstance(t *testing.T) {
	up := newSPUpstream(t)
	own := &tenant.ProviderConfig{Enabled: true, APIKey: "org-a-openai-key", Models: []string{spModel}, Timeout: 20}
	srv := spGateway(t, up, map[string]*tenant.OrgConfig{
		spOrgA: {Providers: map[string]*tenant.ProviderConfig{"openai": own}},
	})
	spWarm(t, srv, spOrgA, "openai", own)

	w := spPost(t, srv, "/v1/embeddings", spInternalKey, spEmbeddingBody)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200: %s", w.Code, w.Body.String())
	}
	spWantOneCall(t, up, spOperatorBearer)
	if got := w.Header().Get("x-agentcc-timeout-ms"); got != "30000" {
		t.Errorf("x-agentcc-timeout-ms = %q, want 30000 (the operator instance that served, not the cached org decoy's 20000)", got)
	}
}

// servingProvider's own contract, clause by clause, on a gateway whose org A
// has an "openai" instance cached (20s) beside the operator's (30s). The
// marker paths are what org keys take on every header-writing non-chat
// route; an internal key without a marker keeps the operator's instance; no
// org, or another org with nothing cached, reads the operator's instance and
// never org A's. (An internal key reaches the org_provider marker end to end
// only through an org provider id the registry lacks, which needs a public
// base_url; the server package cannot stub DNS, so this case is pinned here.)
func TestServingProviderNamesTheInstanceEachPathUsed(t *testing.T) {
	up := newSPUpstream(t)
	own := &tenant.ProviderConfig{Enabled: true, APIKey: "org-a-openai-key", Models: []string{spModel}, Timeout: 20}
	srv := spGateway(t, up, map[string]*tenant.OrgConfig{
		spOrgA: {Providers: map[string]*tenant.ProviderConfig{"openai": own}},
	})
	spWarm(t, srv, spOrgA, "openai", own)
	h := srv.handlers

	for _, tc := range []struct {
		name string
		meta map[string]string
		want string // "org" (org A's 20s instance), "operator" (30s) or "" (none)
	}{
		{"org_provider marker, org key", map[string]string{"org_id": spOrgA, "key_type": "byok", "org_provider": "true"}, "org"},
		{"org_provider marker, internal key", map[string]string{"org_id": spOrgA, "key_type": "internal", "org_provider": "true"}, "org"},
		{"org_provider_model_match marker, internal key", map[string]string{"org_id": spOrgA, "key_type": "internal", "org_provider_model_match": spModel}, "org"},
		{"no marker, a key that is not internal (applyOrgProviderOverride's case)", map[string]string{"org_id": spOrgA, "key_type": "byok"}, "org"},
		{"no marker, internal key", map[string]string{"org_id": spOrgA, "key_type": "internal"}, "operator"},
		{"no org", map[string]string{"key_type": "byok", "org_provider": "true"}, "operator"},
		{"another org with nothing cached", map[string]string{"org_id": spOrgB, "key_type": "byok", "org_provider": "true"}, "operator"},
		{"no provider", map[string]string{"org_id": spOrgA, "key_type": "byok", "org_provider": "true", "-provider": ""}, ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Provider = "openai"
			for k, v := range tc.meta {
				if k == "-provider" {
					rc.Provider = v
					continue
				}
				rc.Metadata[k] = v
			}
			p, ok := h.servingProvider(rc)
			got := ""
			if ok && p != nil {
				got = "operator"
				if providers.IsOrgInstance(p, spOrgA) {
					got = "org"
				}
			}
			if got != tc.want {
				t.Fatalf("servingProvider = %q (ok %v), want %q", got, ok, tc.want)
			}
			wantMs := map[string]string{"org": "20000", "operator": "30000", "": "120000"}[tc.want]
			if ms := appliedTimeoutMs(p, "120000"); ms != wantMs {
				t.Errorf("appliedTimeoutMs = %s, want %s", ms, wantMs)
			}
		})
	}
}

// Responses resolves an org key to its org's provider first
// (org_provider_model_match marker); the header reads that instance.
func TestOrgKeyOnResponsesReportsTheOrgInstance(t *testing.T) {
	up := newSPUpstream(t)
	srv := spGateway(t, up, map[string]*tenant.OrgConfig{
		spOrgA: {Providers: map[string]*tenant.ProviderConfig{
			"openai": {Enabled: true, APIKey: "org-a-openai-key", Models: []string{spChatModel}, Timeout: 20},
		}},
	})

	w := spPost(t, srv, "/v1/responses", spOrgKeyA, `{"model":"`+spChatModel+`","input":"hi","store":false}`)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200: %s", w.Code, w.Body.String())
	}
	spWantOneCall(t, up, spOrgABearer)
	if got := w.Header().Get("x-agentcc-timeout-ms"); got != "20000" {
		t.Errorf("x-agentcc-timeout-ms = %q, want 20000 (the org instance that served)", got)
	}
}

// servingProvider reads only the requesting key's org: org B's cached
// "openai" instance (10s) never names org A's call, and org B's key is
// refused a model only org A lists.
func TestAnotherOrgsCachedInstanceIsNeverReported(t *testing.T) {
	up := newSPUpstream(t)
	orgBOpenAI := &tenant.ProviderConfig{Enabled: true, APIKey: "org-b-openai-key", Models: []string{"text-embedding-3-large"}, Timeout: 10}
	srv := spGateway(t, up, map[string]*tenant.OrgConfig{
		spOrgA: {Providers: map[string]*tenant.ProviderConfig{
			"openai": {Enabled: true, APIKey: "org-a-openai-key", Models: []string{spModel}, Timeout: 20},
		}},
		spOrgB: {Providers: map[string]*tenant.ProviderConfig{"openai": orgBOpenAI}},
	})
	spWarm(t, srv, spOrgB, "openai", orgBOpenAI)

	w := spPost(t, srv, "/v1/embeddings", spOrgKeyA, spEmbeddingBody)
	if w.Code != http.StatusOK {
		t.Fatalf("org A: status = %d, want 200: %s", w.Code, w.Body.String())
	}
	spWantOneCall(t, up, spOrgABearer)
	if got := w.Header().Get("x-agentcc-timeout-ms"); got != "20000" {
		t.Errorf("org A: x-agentcc-timeout-ms = %q, want 20000 (org A's instance, never org B's 10000)", got)
	}

	w = spPost(t, srv, "/v1/embeddings", spOrgKeyB, spEmbeddingBody)
	if w.Code != http.StatusForbidden {
		t.Fatalf("org B: status = %d, want 403: %s", w.Code, w.Body.String())
	}
	if calls := up.calls(); len(calls) != 1 {
		t.Fatalf("upstream calls = %q, want only org A's one call", calls)
	}
	for _, c := range up.calls() {
		if strings.HasSuffix(c, spOrgBBearer) || strings.HasSuffix(c, spOperatorBearer) {
			t.Fatalf("upstream call %q used org B's or the operator's key", c)
		}
	}
}

// Assistants and Vector Stores set no timeout_ms, so they write no
// x-agentcc-timeout-ms at all, for an internal key or an org key; both keys'
// calls go to the org's own "openai" instance (resolveOpenAIProvider).
func TestAssistantsWriteNoTimeoutHeader(t *testing.T) {
	up := newSPUpstream(t)
	own := &tenant.ProviderConfig{Enabled: true, APIKey: "org-a-openai-key", Models: []string{spChatModel}, Timeout: 20}
	srv := spGateway(t, up, map[string]*tenant.OrgConfig{
		spOrgA: {Providers: map[string]*tenant.ProviderConfig{"openai": own}},
	})

	for _, key := range []string{spInternalKey, spOrgKeyA} {
		w := spPost(t, srv, "/v1/assistants", key, `{"model":"`+spChatModel+`"}`)
		if w.Code != http.StatusOK {
			t.Fatalf("%s: status = %d, want 200: %s", key, w.Code, w.Body.String())
		}
		if got := w.Header().Get("x-agentcc-timeout-ms"); got != "" {
			t.Errorf("%s: x-agentcc-timeout-ms = %q, want none (assistants set no timeout_ms)", key, got)
		}
	}
	for _, c := range up.calls() {
		if !strings.HasSuffix(c, spOrgABearer) {
			t.Errorf("upstream call %q, want org A's key on every assistants call", c)
		}
	}
}

// Chat's provider failover: a hop that succeeds without a model fallback
// records its instance as the one that served (runNonStreamWithFailover), so
// the header reads the backup's 25s rather than the resolved 120s.
func TestChatProviderFailoverHopReportsTheBackupInstance(t *testing.T) {
	primary := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusTooManyRequests)
		fmt.Fprint(w, `{"error":{"message":"rate limited","type":"rate_limit_error"}}`)
	}))
	defer primary.Close()
	backup := startMockOpenAI(t)
	defer backup.Close()

	cfg := config.DefaultConfig()
	cfg.Providers["primary"] = config.ProviderConfig{BaseURL: primary.URL, APIFormat: "openai", Models: []string{"sp-model"}, DefaultTimeout: 40 * time.Second}
	cfg.Providers["backup"] = config.ProviderConfig{BaseURL: backup.URL, APIFormat: "openai", Models: []string{"sp-model"}, DefaultTimeout: 25 * time.Second}
	cfg.Routing.DefaultStrategy = "round-robin"
	cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 2, OnStatusCodes: []int{http.StatusTooManyRequests}}
	cfg.Routing.ModelTimeouts = map[string]time.Duration{"sp-model": 120 * time.Second}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { registry.Close() })
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)

	for i := 0; i < 2; i++ { // round-robin: once from each starting target
		w := spPost(t, srv, "/v1/chat/completions", "", `{"model":"sp-model","messages":[{"role":"user","content":"hello"}]}`)
		if w.Code != http.StatusOK {
			t.Fatalf("status = %d, want 200: %s", w.Code, w.Body.String())
		}
		if got := w.Header().Get("x-agentcc-provider"); got != "backup" {
			t.Fatalf("x-agentcc-provider = %q, want backup", got)
		}
		var body map[string]any
		if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
			t.Fatalf("body: %v", err)
		}
		if got := w.Header().Get("x-agentcc-timeout-ms"); got != "25000" {
			t.Errorf("request %d: x-agentcc-timeout-ms = %q, want 25000 (the backup that served)", i, got)
		}
	}
}
