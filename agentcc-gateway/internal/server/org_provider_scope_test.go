package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// These tests pin one rule: a key limited to its org's providers (any key but
// an internal one, with auth on) is never served by a global provider with
// the gateway's own credentials, whichever path picks the provider — the
// provider-lock header, the org's model fallbacks, provider failover or an
// async job.

const (
	platformKey = "Bearer platform-openai-key"
	orgUpKey    = "Bearer org-key"
)

// scopeUpstream is a fake OpenAI upstream that records the credential of
// every chat call. Calls made with a credential in failWith, or for a model in
// failModel, get a 503.
type scopeUpstream struct {
	srv       *httptest.Server
	mu        sync.Mutex
	seen      []string
	failWith  map[string]bool
	failModel map[string]bool
}

func newScopeUpstream(t *testing.T, failWith ...string) *scopeUpstream {
	t.Helper()
	u := &scopeUpstream{failWith: map[string]bool{}, failModel: map[string]bool{}}
	for _, c := range failWith {
		u.failWith[c] = true
	}
	u.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path != "/v1/chat/completions" {
			fmt.Fprint(w, `{"object":"list","data":[]}`)
			return
		}
		cred := r.Header.Get("Authorization")
		var body struct {
			Model  string `json:"model"`
			Stream bool   `json:"stream"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		u.mu.Lock()
		u.seen = append(u.seen, cred)
		fail := u.failWith[cred] || u.failModel[body.Model]
		u.mu.Unlock()
		if fail {
			w.WriteHeader(http.StatusServiceUnavailable)
			fmt.Fprint(w, `{"error":{"message":"upstream down","type":"server_error"}}`)
			return
		}
		if body.Stream {
			w.Header().Set("Content-Type", "text/event-stream")
			fmt.Fprint(w, "data: {\"id\":\"chatcmpl-x\",\"object\":\"chat.completion.chunk\",\"model\":\"gpt-4o\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"ok\"},\"finish_reason\":null}]}\n\n")
			fmt.Fprint(w, "data: [DONE]\n\n")
			return
		}
		fmt.Fprint(w, `{"id":"chatcmpl-x","object":"chat.completion","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
	}))
	t.Cleanup(u.srv.Close)
	return u
}

// take returns the credentials seen since the last call and forgets them.
func (u *scopeUpstream) take() []string {
	u.mu.Lock()
	defer u.mu.Unlock()
	s := u.seen
	u.seen = nil
	return s
}

// assertNoPlatformKey fails when any credential in seen is one of the
// gateway's own (every global provider here is keyed "platform-...").
func (u *scopeUpstream) assertNoPlatformKey(t *testing.T, what string, seen []string) {
	t.Helper()
	for _, c := range seen {
		if strings.HasPrefix(c, "Bearer platform-") {
			t.Errorf("%s: an org key was served by the global provider with the gateway's key (upstream saw %q)", what, seen)
			return
		}
	}
}

// scopeServer builds a gateway with auth on, a global "openai" provider on
// the platform key serving platformModels, one org key ("sk-org", org
// "org-scope") and one internal key ("sk-internal"). tweak edits the config
// before the registry is built.
func scopeServer(t *testing.T, up *scopeUpstream, org *tenant.OrgConfig, platformModels []string, tweak func(*config.Config)) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "org", Key: "sk-org", Owner: "o", KeyType: "byok", Metadata: map[string]string{"org_id": "org-scope"}},
		{Name: "internal", Key: "sk-internal", Owner: "ops", KeyType: "internal"},
	}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.srv.URL, APIKey: "platform-openai-key", APIFormat: "openai", Models: platformModels}
	if tweak != nil {
		tweak(cfg)
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	ts := tenant.NewStore()
	ts.Set("org-scope", org)
	ks := auth.NewKeyStore(cfg.Auth)
	var plugins []pipeline.Plugin
	if cfg.Auth.Enabled { // as cmd/agentcc wires it
		plugins = append(plugins, authplugin.New(ks, true))
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(plugins...), ks, nil, nil, nil, testModelDBPtr(), ts, nil)
	srv.ready.Store(true)
	t.Cleanup(srv.asyncWorker.Stop)
	return srv
}

// orgOpenAI is an org whose own "openai" provider lists models. It sets no
// base_url, so it is layered onto the global entry: the same upstream, called
// with the org's key.
func orgOpenAI(models ...string) *tenant.OrgConfig {
	return &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai": {Enabled: true, APIKey: "org-key", Models: models},
	}}
}

// objCall is the shared helper in stored_objects_org_scope_test.go.

const scopeChat = `{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`

// With the default config (provider locking on), an org key whose org has no
// provider of its own must not reach the global provider by naming it in
// x-agentcc-provider-lock, synchronously or as an async job.
func TestOrgKeyProviderLockCannotReachGlobalProvider(t *testing.T) {
	up := newScopeUpstream(t)
	srv := scopeServer(t, up, &tenant.OrgConfig{}, []string{"gpt-4o"}, nil)

	w := objCall(t, srv, "POST", "/v1/chat/completions", "sk-org", scopeChat, nil)
	if w.Code != http.StatusForbidden {
		t.Fatalf("no lock header: status %d, want 403; body %s", w.Code, w.Body.String())
	}
	up.take()

	w = objCall(t, srv, "POST", "/v1/chat/completions", "sk-org", scopeChat, map[string]string{"x-agentcc-provider-lock": "openai"})
	up.assertNoPlatformKey(t, "sync + lock", up.take())
	if w.Code != http.StatusForbidden {
		t.Errorf("sync + lock: status %d, want 403; body %s", w.Code, w.Body.String())
	}

	w = objCall(t, srv, "POST", "/v1/chat/completions", "sk-org", scopeChat, map[string]string{"x-agentcc-provider-lock": "openai", "x-agentcc-async": "true"})
	if w.Code == http.StatusAccepted {
		time.Sleep(300 * time.Millisecond)
	}
	up.assertNoPlatformKey(t, "async + lock", up.take())
	if w.Code != http.StatusForbidden {
		t.Errorf("async + lock: status %d, want 403; body %s", w.Code, w.Body.String())
	}
}

// A lock can only narrow: an org key may lock to a provider its org holds and
// that lists the model, and is then served with the org's credentials.
// Locking to a provider the org does not hold for the model is refused.
func TestOrgKeyProviderLockUsesOrgProvider(t *testing.T) {
	up := newScopeUpstream(t)
	srv := scopeServer(t, up, orgOpenAI("gpt-4o"), []string{"gpt-4o", "gpt-4o-mini"}, nil)

	w := objCall(t, srv, "POST", "/v1/chat/completions", "sk-org", scopeChat, map[string]string{"x-agentcc-provider-lock": "openai"})
	if w.Code != http.StatusOK {
		t.Fatalf("lock to the org's provider: status %d, want 200; body %s", w.Code, w.Body.String())
	}
	if got := up.take(); len(got) != 1 || got[0] != orgUpKey {
		t.Errorf("lock to the org's provider: upstream saw %q, want only the org key", got)
	}

	// The org's openai does not list gpt-4o-mini: locking does not widen it.
	w = objCall(t, srv, "POST", "/v1/chat/completions", "sk-org", `{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}`, map[string]string{"x-agentcc-provider-lock": "openai"})
	up.assertNoPlatformKey(t, "lock, model the org does not list", up.take())
	if w.Code != http.StatusForbidden {
		t.Errorf("lock, model the org does not list: status %d, want 403; body %s", w.Code, w.Body.String())
	}
}

// An internal key's provider lock reaches the global provider.
func TestInternalKeyProviderLockUsesGlobalProvider(t *testing.T) {
	up := newScopeUpstream(t)
	srv := scopeServer(t, up, &tenant.OrgConfig{}, []string{"gpt-4o"}, nil)

	w := objCall(t, srv, "POST", "/v1/chat/completions", "sk-internal", scopeChat, map[string]string{"x-agentcc-provider-lock": "openai"})
	if w.Code != http.StatusOK {
		t.Fatalf("internal key + lock: status %d, want 200; body %s", w.Code, w.Body.String())
	}
	if got := up.take(); len(got) != 1 || got[0] != platformKey {
		t.Errorf("internal key + lock: upstream saw %q, want the platform key", got)
	}
}

// With auth off there are no org keys: a lock reaches the global provider.
func TestAuthOffProviderLockUsesGlobalProvider(t *testing.T) {
	up := newScopeUpstream(t)
	srv := scopeServer(t, up, &tenant.OrgConfig{}, []string{"gpt-4o"}, func(cfg *config.Config) {
		cfg.Auth.Enabled = false
	})

	w := objCall(t, srv, "POST", "/v1/chat/completions", "", scopeChat, map[string]string{"x-agentcc-provider-lock": "openai"})
	if w.Code != http.StatusOK {
		t.Fatalf("auth off + lock: status %d, want 200; body %s", w.Code, w.Body.String())
	}
	if got := up.take(); len(got) != 1 || got[0] != platformKey {
		t.Errorf("auth off + lock: upstream saw %q, want the platform key", got)
	}
}

// The org's own model_fallbacks must not carry its key to a model only a
// global provider serves, in the sync or the streaming path.
func TestOrgModelFallbackCannotReachGlobalProvider(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%v", stream), func(t *testing.T) {
			up := newScopeUpstream(t, orgUpKey) // the org's upstream is down
			org := orgOpenAI("gpt-4o")
			org.Routing = &tenant.RoutingConfig{FallbackEnabled: true, ModelFallbacks: map[string][]string{"gpt-4o": {"gpt-4o-mini"}}}
			srv := scopeServer(t, up, org, []string{"gpt-4o", "gpt-4o-mini"}, nil)

			// The platform-only model asked for directly is refused.
			w := objCall(t, srv, "POST", "/v1/chat/completions", "sk-org", `{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}`, nil)
			if w.Code != http.StatusForbidden {
				t.Fatalf("direct gpt-4o-mini: status %d, want 403; body %s", w.Code, w.Body.String())
			}
			up.take()

			body := scopeChat
			if stream {
				body = `{"model":"gpt-4o","stream":true,"messages":[{"role":"user","content":"hi"}]}`
			}
			w = objCall(t, srv, "POST", "/v1/chat/completions", "sk-org", body, nil)
			got := up.take()
			up.assertNoPlatformKey(t, "gpt-4o via the org's fallback", got)
			if w.Header().Get("x-agentcc-fallback-used") == "true" {
				t.Errorf("fallback reported used; upstream saw %q", got)
			}
		})
	}
}

// The org's fallbacks work when the fallback model is one its own
// provider lists: served by the org's provider on the org's key.
func TestOrgModelFallbackToOrgProviderWorks(t *testing.T) {
	up := newScopeUpstream(t)
	up.mu.Lock()
	up.failModel["gpt-4o"] = true // the primary model fails, on any key
	up.mu.Unlock()
	org := orgOpenAI("gpt-4o", "gpt-4o-mini")
	org.Routing = &tenant.RoutingConfig{FallbackEnabled: true, ModelFallbacks: map[string][]string{"gpt-4o": {"gpt-4o-mini"}}}
	srv := scopeServer(t, up, org, []string{"gpt-4o", "gpt-4o-mini"}, nil)

	w := objCall(t, srv, "POST", "/v1/chat/completions", "sk-org", scopeChat, nil)
	got := up.take()
	if w.Code != http.StatusOK || w.Header().Get("x-agentcc-fallback-used") != "true" {
		t.Fatalf("status %d fallback-used %q, want 200 via the fallback; body %s", w.Code, w.Header().Get("x-agentcc-fallback-used"), w.Body.String())
	}
	if len(got) != 2 || got[0] != orgUpKey || got[1] != orgUpKey {
		t.Errorf("upstream saw %q, want the org key twice (primary, then fallback)", got)
	}
}

// Traffic mirroring sends a copy of the request to a global provider; an org
// key's request is not mirrored. An internal key's is.
func TestOrgKeyRequestNotMirroredToGlobalProvider(t *testing.T) {
	up := newScopeUpstream(t)
	srv := scopeServer(t, up, orgOpenAI("gpt-4o"), []string{"gpt-4o"}, func(cfg *config.Config) {
		cfg.Providers["shadow"] = config.ProviderConfig{BaseURL: up.srv.URL, APIKey: "platform-shadow-key", APIFormat: "openai", Models: []string{"gpt-4o-shadow"}}
		cfg.Routing.Mirror = config.MirrorConfig{Enabled: true, Rules: []config.MirrorRule{{SourceModel: "gpt-4o", TargetProvider: "shadow", TargetModel: "gpt-4o-shadow", SampleRate: 1}}}
	})

	w := objCall(t, srv, "POST", "/v1/chat/completions", "sk-org", scopeChat, nil)
	if w.Code != http.StatusOK {
		t.Fatalf("org key: status %d, want 200; body %s", w.Code, w.Body.String())
	}
	time.Sleep(300 * time.Millisecond)
	up.assertNoPlatformKey(t, "org key, mirrored", up.take())

	w = objCall(t, srv, "POST", "/v1/chat/completions", "sk-internal", scopeChat, nil)
	if w.Code != http.StatusOK {
		t.Fatalf("internal key: status %d, want 200; body %s", w.Code, w.Body.String())
	}
	time.Sleep(300 * time.Millisecond)
	shadowed := false
	for _, c := range up.take() {
		shadowed = shadowed || c == "Bearer platform-shadow-key"
	}
	if !shadowed {
		t.Errorf("internal key: request was not mirrored to the shadow provider")
	}
}

// Provider failover picks among the global providers that serve a model. An
// org key served by its org's provider must not be failed over (or, on the
// first attempt, routed) to the global instance on the gateway's key.
func TestOrgKeyProviderFailoverCannotReachGlobalProvider(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%v", stream), func(t *testing.T) {
			up := newScopeUpstream(t)
			org := orgOpenAI("gpt-4o")
			org.Routing = &tenant.RoutingConfig{FallbackEnabled: true}
			srv := scopeServer(t, up, org, []string{"gpt-4o"}, func(cfg *config.Config) {
				// A second global provider for the model, so the router has
				// targets to fail over across.
				cfg.Providers["openai-b"] = config.ProviderConfig{BaseURL: up.srv.URL, APIKey: "platform-openai-b-key", APIFormat: "openai", Models: []string{"gpt-4o"}}
				cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"gpt-4o": {{Provider: "openai", Weight: 1}, {Provider: "openai-b", Weight: 1}}}
			})

			body := scopeChat
			if stream {
				body = `{"model":"gpt-4o","stream":true,"messages":[{"role":"user","content":"hi"}]}`
			}
			w := objCall(t, srv, "POST", "/v1/chat/completions", "sk-org", body, nil)
			got := up.take()
			up.assertNoPlatformKey(t, "org key with routing targets + failover", got)
			if w.Code != http.StatusOK {
				t.Errorf("status %d, want 200 from the org's provider; body %s", w.Code, w.Body.String())
			}
			if len(got) == 0 || got[0] != orgUpKey {
				t.Errorf("upstream saw %q, want the org key", got)
			}
		})
	}
}

// An async job runs through runNonStream on the provider its request was
// admitted with, so the org-only guards
// re-applied there (no model fallback to a global provider, no provider
// failover across global targets) hold for a job exactly as for its
// synchronous request. An org key's job runs on its org's own provider with
// the org's key and records credential_source "org"; it never reaches a
// global provider on the gateway's key. An internal key's job runs on
// the global provider.
func TestOrgKeyAsyncJobRunsOnItsOwnAdmittedProvider(t *testing.T) {
	submit := func(t *testing.T, srv *Server, key string) map[string]any {
		t.Helper()
		w := objCall(t, srv, "POST", "/v1/chat/completions", key, scopeChat, map[string]string{"x-agentcc-async": "true"})
		if w.Code != http.StatusAccepted {
			t.Fatalf("%s async: status %d, want 202; body %s", key, w.Code, w.Body.String())
		}
		id, _ := mediaDecode(t, w)["job_id"].(string)
		if id == "" {
			t.Fatalf("%s async: no job_id in %s", key, w.Body.String())
		}
		return waitAsyncJob(t, srv, key, id)
	}

	t.Run("org provider", func(t *testing.T) {
		up := newScopeUpstream(t)
		srv := scopeServer(t, up, orgOpenAI("gpt-4o"), []string{"gpt-4o"}, nil)

		job := submit(t, srv, "sk-org")
		got := up.take()
		up.assertNoPlatformKey(t, "org key async job", got)
		if job["status"] != "completed" || job["credential_source"] != "org" {
			t.Errorf("org key job: status %v credential_source %v, want completed on org credentials; job %v", job["status"], job["credential_source"], job)
		}
		if len(got) != 1 || got[0] != orgUpKey {
			t.Errorf("org key job: upstream saw %q, want the org key once", got)
		}
	})

	t.Run("model fallback to a platform-only model", func(t *testing.T) {
		up := newScopeUpstream(t, orgUpKey) // the org's upstream is down
		org := orgOpenAI("gpt-4o")
		org.Routing = &tenant.RoutingConfig{FallbackEnabled: true, ModelFallbacks: map[string][]string{"gpt-4o": {"gpt-4o-mini"}}}
		srv := scopeServer(t, up, org, []string{"gpt-4o", "gpt-4o-mini"}, nil)

		job := submit(t, srv, "sk-org")
		got := up.take()
		up.assertNoPlatformKey(t, "org key async job via the org's fallback", got)
		if job["status"] != "failed" || job["credential_source"] != "org" {
			t.Errorf("org key job: status %v credential_source %v, want failed on org credentials (the platform-only fallback skipped); job %v", job["status"], job["credential_source"], job)
		}
	})

	t.Run("provider failover across global targets", func(t *testing.T) {
		up := newScopeUpstream(t)
		org := orgOpenAI("gpt-4o")
		org.Routing = &tenant.RoutingConfig{FallbackEnabled: true}
		srv := scopeServer(t, up, org, []string{"gpt-4o"}, func(cfg *config.Config) {
			cfg.Providers["openai-b"] = config.ProviderConfig{BaseURL: up.srv.URL, APIKey: "platform-openai-b-key", APIFormat: "openai", Models: []string{"gpt-4o"}}
			cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"gpt-4o": {{Provider: "openai", Weight: 1}, {Provider: "openai-b", Weight: 1}}}
		})

		job := submit(t, srv, "sk-org")
		got := up.take()
		up.assertNoPlatformKey(t, "org key async job with routing targets + failover", got)
		if job["status"] != "completed" || job["credential_source"] != "org" {
			t.Errorf("org key job: status %v credential_source %v, want completed on org credentials; job %v", job["status"], job["credential_source"], job)
		}
		if len(got) == 0 || got[0] != orgUpKey {
			t.Errorf("org key job: upstream saw %q, want the org key", got)
		}
	})

	t.Run("internal key", func(t *testing.T) {
		up := newScopeUpstream(t)
		srv := scopeServer(t, up, orgOpenAI("gpt-4o"), []string{"gpt-4o"}, nil)

		job := submit(t, srv, "sk-internal")
		got := up.take()
		if job["status"] != "completed" || job["credential_source"] != "platform" {
			t.Errorf("internal key job: status %v credential_source %v, want completed on the platform key; job %v", job["status"], job["credential_source"], job)
		}
		if len(got) != 1 || got[0] != platformKey {
			t.Errorf("internal key job: upstream saw %q, want the platform key once", got)
		}
	})
}

// Scheduled jobs resolve their provider from the global registry too; an org
// key is refused at submission.
func TestOrgKeyScheduledJobRefused(t *testing.T) {
	up := newScopeUpstream(t)
	srv := scopeServer(t, up, orgOpenAI("gpt-4o"), []string{"gpt-4o"}, func(cfg *config.Config) {
		cfg.Routing.Scheduled.Enabled = true
	})

	body := `{"delay":"1m","request":` + scopeChat + `}`
	w := objCall(t, srv, "POST", "/v1/scheduled", "sk-org", body, nil)
	if w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "deferred_run_unavailable") {
		t.Errorf("org key scheduled: status %d, want 400 deferred_run_unavailable; body %s", w.Code, w.Body.String())
	}
	w = objCall(t, srv, "POST", "/v1/scheduled", "sk-internal", body, nil)
	if w.Code != http.StatusCreated {
		t.Errorf("internal key scheduled: status %d, want 201; body %s", w.Code, w.Body.String())
	}
}
