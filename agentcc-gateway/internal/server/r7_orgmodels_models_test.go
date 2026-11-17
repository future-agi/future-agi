package server

// /v1/models and GET /v1/models/{id} list, for a key
// limited to its org's providers (every customer key), only what that key
// can call: its org's own provider models, "auto", and the managed catalog
// when the org has opted in. An earlier probe: at the base the org
// key listed 12 models, two of them the operator's that chat refuses it
// (403). Internal keys list as before.

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sort"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	r7omInternalKey = "sk-r7om-internal"
	r7omNoOpenAIKey = "sk-r7om-anthropic-only"
	r7omOneModelKey = "sk-r7om-one-model"
)

// r7omModelsServer: the operator's provider "openai" (FutureAGI's key)
// serves m-realtime and m-global-only; org aclEveryOrg's own "openai"
// serves aclModels. Keys: the org's customer key, an internal key, an org
// key limited to provider anthropic, an org key limited to model m-chat.
func r7omModelsServer(t *testing.T) *Server {
	t.Helper()
	up := startACLUpstream(t)
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	org := map[string]string{"org_id": aclEveryOrg}
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "org-byok", Key: aclEveryKey, Owner: "t", KeyType: "byok", Metadata: org},
		{Name: "internal", Key: r7omInternalKey, Owner: "t", KeyType: "internal"},
		{Name: "anthropic-only", Key: r7omNoOpenAIKey, Owner: "t", KeyType: "byok", Providers: []string{"anthropic"}, Metadata: org},
		{Name: "one-model", Key: r7omOneModelKey, Owner: "t", KeyType: "byok", Models: []string{"m-chat"}, Metadata: org},
	}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "operator-key", APIFormat: "openai", Models: []string{"m-realtime", "m-global-only"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("registry: %v", err)
	}
	tenants := tenant.NewStore()
	tenants.Set(aclEveryOrg, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai": {APIKey: "org-own-key", Enabled: true, Models: aclModels},
	}})
	keyStore := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keyStore, true)), keyStore, nil, nil, nil, testModelDBPtr(), tenants, nil)
	releaseTestServer(t, srv)
	srv.ready.Store(true)
	return srv
}

func r7omGet(srv *Server, path, key string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodGet, path, nil)
	if key != "" {
		req.Header.Set("Authorization", "Bearer "+key)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// r7omListed is the sorted model ids /v1/models lists to key.
func r7omListed(t *testing.T, srv *Server, key string) []string {
	t.Helper()
	w := r7omGet(srv, "/v1/models", key)
	if w.Code != http.StatusOK {
		t.Fatalf("/v1/models -> %d %s", w.Code, w.Body.String())
	}
	var l struct {
		Data []struct {
			ID string `json:"id"`
		} `json:"data"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &l); err != nil {
		t.Fatal(err)
	}
	ids := make([]string, 0, len(l.Data))
	for _, d := range l.Data {
		ids = append(ids, d.ID)
	}
	sort.Strings(ids)
	return ids
}

func r7omSorted(ids ...string) []string {
	out := append([]string(nil), ids...)
	sort.Strings(out)
	return out
}

func r7omChatAs(srv *Server, key, model string) int {
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions",
		strings.NewReader(`{"model":"`+model+`","messages":[{"role":"user","content":"hi"}]}`))
	req.Header.Set("Authorization", "Bearer "+key)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w.Code
}

// TestR7ModelsListOrgScoped: what an org key lists is what it can call.
func TestR7ModelsListOrgScoped(t *testing.T) {
	srv := r7omModelsServer(t)

	t.Run("the org key lists its org's models and auto, never the operator's", func(t *testing.T) {
		got := r7omListed(t, srv, aclEveryKey)
		want := r7omSorted(append(append([]string(nil), aclModels...), "auto")...)
		if strings.Join(got, " ") != strings.Join(want, " ") {
			t.Fatalf("org key lists %v; want %v", got, want)
		}
		// Each listed chat model is callable; the operator's are not.
		if c := r7omChatAs(srv, aclEveryKey, "m-chat"); c != http.StatusOK {
			t.Fatalf("listed m-chat: chat %d", c)
		}
		if c := r7omChatAs(srv, aclEveryKey, "m-global-only"); c == http.StatusOK {
			t.Fatalf("unlisted m-global-only: chat %d, the key can call it", c)
		}
	})
	t.Run("GET /v1/models/{id} answers only for a callable model", func(t *testing.T) {
		for model, want := range map[string]int{
			"m-chat": http.StatusOK, "auto": http.StatusOK,
			"m-global-only": http.StatusNotFound, "m-realtime": http.StatusNotFound,
		} {
			if w := r7omGet(srv, "/v1/models/"+model, aclEveryKey); w.Code != want {
				t.Errorf("GET /v1/models/%s -> %d %s; want %d", model, w.Code, w.Body.String(), want)
			}
		}
	})
	t.Run("the key's provider and model ACLs narrow the list", func(t *testing.T) {
		if got := r7omListed(t, srv, r7omNoOpenAIKey); strings.Join(got, " ") != "auto" {
			t.Fatalf("key limited to anthropic lists %v; want only auto", got)
		}
		if got := r7omListed(t, srv, r7omOneModelKey); strings.Join(got, " ") != "auto m-chat" {
			t.Fatalf("key limited to m-chat lists %v; want auto and m-chat", got)
		}
		if w := r7omGet(srv, "/v1/models/m-embed", r7omOneModelKey); w.Code != http.StatusNotFound {
			t.Fatalf("GET m-embed with a key limited to m-chat -> %d", w.Code)
		}
	})
	t.Run("an internal key lists the gateway's models, as before", func(t *testing.T) {
		got := strings.Join(r7omListed(t, srv, r7omInternalKey), " ")
		for _, m := range []string{"m-global-only", "m-realtime", "auto"} {
			if !strings.Contains(" "+got+" ", " "+m+" ") {
				t.Fatalf("internal key lists %q; want %s among them", got, m)
			}
		}
		if w := r7omGet(srv, "/v1/models/m-global-only", r7omInternalKey); w.Code != http.StatusOK {
			t.Fatalf("internal GET m-global-only -> %d", w.Code)
		}
	})
}

// TestR7ModelsListOrgScopedManaged: an opted-in org's key lists the managed
// catalog (it can call it) and not the operator's gpt-4o (it cannot); an org
// that has not opted in lists neither.
func TestR7ModelsListOrgScopedManaged(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	got := strings.Join(r7omListed(t, g.srv, managedOrgKey), " ")
	want := "anthropic/claude-sonnet-5.5 auto openai/gpt-6-sol qwen/qwen3.7-flash qwen/qwen3.7-flash:free"
	if got != want {
		t.Fatalf("opted-in org key lists %q; want %q", got, want)
	}
	if w := r7omGet(g.srv, "/v1/models/gpt-4o", managedOrgKey); w.Code != http.StatusNotFound {
		t.Fatalf("GET the operator's gpt-4o -> %d; want 404", w.Code)
	}

	off := newManagedTestGateway(t, &tenant.OrgConfig{})
	if got := strings.Join(r7omListed(t, off.srv, managedOrgKey), " "); got != "auto" {
		t.Fatalf("org without managed models lists %q; want only auto", got)
	}
}

// TestR7ModelsListOrgScopedManagedOverlay: the org's Model Database limits
// and capability switches apply to its managed calls (the validation plugin
// holds them to it), so the managed catalog its key lists reports them too;
// the price stays FutureAGI's catalog price, whatever the override prices.
func TestR7ModelsListOrgScopedManagedOverlay(t *testing.T) {
	no, cheap, limit := false, 1e-9, 77
	org := managedOptIn()
	org.ModelDatabase = &tenant.ModelDatabaseOrgConfig{Overrides: map[string]*tenant.ModelOverrideOrgConfig{
		"anthropic/claude-sonnet-5.5": {
			MaxOutputTokens: &limit,
			Pricing:         &tenant.PricingOverrideOrg{InputPerToken: &cheap, OutputPerToken: &cheap},
			Capabilities:    &tenant.CapabilityOverrideOrg{Vision: &no, Reasoning: &no},
		},
	}}
	g := newManagedTestGateway(t, org)
	w := r7omGet(g.srv, "/v1/models", managedOrgKey)
	if w.Code != http.StatusOK {
		t.Fatalf("/v1/models -> %d %s", w.Code, w.Body.String())
	}
	var l struct {
		Data []struct {
			ID              string `json:"id"`
			MaxInputTokens  int    `json:"max_input_tokens"`
			MaxOutputTokens int    `json:"max_output_tokens"`
			Pricing         *struct {
				In  float64 `json:"input_per_m_tokens"`
				Out float64 `json:"output_per_m_tokens"`
			} `json:"pricing"`
			Capabilities *struct {
				Vision          bool `json:"vision"`
				FunctionCalling bool `json:"function_calling"`
				Reasoning       bool `json:"reasoning"`
			} `json:"capabilities"`
		} `json:"data"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &l); err != nil {
		t.Fatal(err)
	}
	found := 0
	for _, d := range l.Data {
		switch d.ID {
		case "anthropic/claude-sonnet-5.5":
			found++
			if d.MaxOutputTokens != limit || d.MaxInputTokens != 1000000 {
				t.Errorf("overridden managed model limits in %d out %d; want in 1000000 (catalog) out %d (the org's)",
					d.MaxInputTokens, d.MaxOutputTokens, limit)
			}
			if d.Capabilities == nil || d.Capabilities.Vision || d.Capabilities.Reasoning || !d.Capabilities.FunctionCalling {
				t.Errorf("overridden managed model capabilities %+v; want vision and reasoning off (the org's), function calling on (catalog)", d.Capabilities)
			}
			// FutureAGI's catalog price with the 5% markup, never the override's.
			if d.Pricing == nil || d.Pricing.In != 2.1 || d.Pricing.Out != 10.5 {
				t.Errorf("overridden managed model pricing %+v; want FutureAGI's 2.1 / 10.5 per M", d.Pricing)
			}
		case "openai/gpt-6-sol":
			found++
			if d.MaxOutputTokens != 64000 || d.Capabilities == nil || !d.Capabilities.Vision {
				t.Errorf("managed model without an override: out %d caps %+v; want the catalog's", d.MaxOutputTokens, d.Capabilities)
			}
		}
	}
	if found != 2 {
		t.Fatalf("listed %s; want both managed models", w.Body.String())
	}
}
