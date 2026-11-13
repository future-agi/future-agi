package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type orgLockRepairOptions struct {
	global         config.ProviderLockConfig
	policy         *tenant.ProviderLockConfig
	failoverScope  string
	targets        []string
	failingPrimary bool
	modelFallback  bool
}

// Only openai serves gpt-4o in the org's own model map. Ordinary routing thus
// selects openai without advancing the gateway router used by either failover.
func newOrgLockRepairServer(t *testing.T, opts orgLockRepairOptions) (*Server, map[string]*atomic.Int32) {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name: "org-a", Key: "sk-agentcc-lock-a", Owner: "user-a", KeyType: "byok",
		Metadata: map[string]string{"org_id": "org-a"},
	}}
	cfg.Routing.ProviderLock = opts.global
	cfg.Routing.DefaultStrategy = "round-robin"
	cfg.Providers = make(map[string]config.ProviderConfig)
	calls := make(map[string]*atomic.Int32)
	remote := false
	for _, id := range []string{"openai", "OpenAI", "anthropic", "backup"} {
		counter := &atomic.Int32{}
		calls[id] = counter
		mock := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path != "/v1/chat/completions" {
				http.NotFound(w, r)
				return
			}
			counter.Add(1)
			if opts.failingPrimary && id == "openai" {
				w.WriteHeader(http.StatusBadGateway)
				fmt.Fprint(w, `{"error":{"message":"primary unavailable","type":"server_error"}}`)
				return
			}
			var req models.ChatCompletionRequest
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				t.Errorf("decode upstream request: %v", err)
				w.WriteHeader(http.StatusBadRequest)
				return
			}
			if req.Stream {
				w.Header().Set("Content-Type", "text/event-stream")
				fmt.Fprintf(w, "data: {\"id\":\"chatcmpl-lock\",\"object\":\"chat.completion.chunk\",\"model\":%q,\"choices\":[{\"index\":0,\"delta\":{\"content\":\"ok\"},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n", req.Model)
				return
			}
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprintf(w, `{"id":"chatcmpl-lock","object":"chat.completion","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}`, req.Model)
		}))
		t.Cleanup(mock.Close)
		cfg.Providers[id] = config.ProviderConfig{
			BaseURL: mock.URL, APIKey: "test-platform-key", APIFormat: "openai",
			Models: []string{"gpt-4o"}, Local: &remote,
		}
	}
	// Prevent automatic multi-provider targets when testing direct requests.
	cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"gpt-4o": {{Provider: "openai"}}}
	if len(opts.targets) > 0 {
		cfg.Routing.Targets["gpt-4o"] = nil
		for _, id := range opts.targets {
			cfg.Routing.Targets["gpt-4o"] = append(cfg.Routing.Targets["gpt-4o"], config.RoutingTargetConfig{Provider: id})
		}
	}
	orgRouting := &tenant.RoutingConfig{ProviderLock: opts.policy}
	if opts.failoverScope == "gateway" {
		cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 2}
	} else if opts.failoverScope == "org" {
		orgRouting.Failover = &tenant.FailoverConfig{Enabled: true, MaxAttempts: 2}
	}
	if opts.modelFallback {
		orgRouting.ModelFallbacks = map[string][]string{"gpt-4o": {"fallback-model"}}
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	t.Cleanup(func() { registry.Close() })
	tenants := tenant.NewStore()
	tenants.Set("org-a", &tenant.OrgConfig{
		Routing: orgRouting,
		Providers: map[string]*tenant.ProviderConfig{
			"openai":    {Enabled: true, APIKey: "test-org-key", Models: []string{"gpt-4o"}},
			"anthropic": {Enabled: true, APIKey: "test-org-key", Models: []string{"fallback-model"}},
		},
	})
	keys := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keys, true)), keys, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	t.Cleanup(func() { srv.Shutdown(context.Background()) })
	return srv, calls
}

func TestChatCompletion_OrgProviderLockPatterns(t *testing.T) {
	tests := []struct {
		name, target    string
		allowed, denied []string
		global          bool
		wantStatus      int
	}{
		{name: "org wildcard allow", target: "openai", allowed: []string{"open*"}, wantStatus: http.StatusOK},
		{name: "org wildcard allows mixed case target", target: "OpenAI", allowed: []string{"open*"}, wantStatus: http.StatusOK},
		{name: "org mixed case allow", target: "openai", allowed: []string{"OpenAI"}, wantStatus: http.StatusOK},
		{name: "org multiple stars", target: "openai", allowed: []string{"O*E*A*"}, wantStatus: http.StatusOK},
		{name: "org case insensitive deny", target: "anthropic", denied: []string{"ANTHROPIC"}, wantStatus: http.StatusForbidden},
		{name: "org wildcard deny", target: "anthropic", denied: []string{"anth*"}, wantStatus: http.StatusForbidden},
		{name: "org deny overrides allow", target: "anthropic", allowed: []string{"*"}, denied: []string{"ANTH*"}, wantStatus: http.StatusForbidden},
		{name: "org unmatched allow", target: "openai", allowed: []string{"anth*"}, wantStatus: http.StatusForbidden},
		{name: "question mark is literal", target: "openai", allowed: []string{"opena?"}, wantStatus: http.StatusForbidden},
		{name: "brackets are literal", target: "openai", allowed: []string{"open[ai]"}, wantStatus: http.StatusForbidden},
		{name: "global wildcard allow stays exact", target: "openai", global: true, allowed: []string{"open*"}, wantStatus: http.StatusForbidden},
		{name: "global mixed case allow stays exact", target: "openai", global: true, allowed: []string{"OpenAI"}, wantStatus: http.StatusForbidden},
		{name: "global wildcard deny stays exact", target: "anthropic", global: true, denied: []string{"anth*"}, wantStatus: http.StatusOK},
		{name: "global mixed case deny stays exact", target: "anthropic", global: true, denied: []string{"ANTHROPIC"}, wantStatus: http.StatusOK},
	}
	for _, tt := range tests {
		for _, source := range []string{"header", "metadata"} {
			t.Run(tt.name+"/"+source, func(t *testing.T) {
				opts := orgLockRepairOptions{policy: &tenant.ProviderLockConfig{Enabled: true, AllowedProviders: tt.allowed, DenyProviders: tt.denied}}
				if tt.global {
					opts.policy = nil
					opts.global = config.ProviderLockConfig{Enabled: true, AllowedProviders: tt.allowed, DenyProviders: tt.denied}
				}
				srv, calls := newOrgLockRepairServer(t, opts)
				w := postOrgProviderLock(t, srv, "sk-agentcc-lock-a", source, tt.target, false)
				if w.Code != tt.wantStatus {
					t.Errorf("status = %d, want %d: %s", w.Code, tt.wantStatus, w.Body.String())
				}
				for id, counter := range calls {
					want := int32(0)
					if tt.wantStatus == http.StatusOK && id == tt.target {
						want = 1
					}
					if got := counter.Load(); got != want {
						t.Errorf("%s calls = %d, want %d", id, got, want)
					}
				}
			})
		}
	}
}

func TestChatCompletion_OrgProviderLockFirstFailoverHop(t *testing.T) {
	for _, scope := range []string{"gateway", "org"} {
		for _, stream := range []bool{false, true} {
			for _, source := range []string{"header", "metadata", "none"} {
				for _, policy := range []string{"deny", "allow", "unrestricted", "absent", "disabled", "inherited"} {
					t.Run(fmt.Sprintf("%s/stream=%t/%s/%s", scope, stream, source, policy), func(t *testing.T) {
						opts := orgLockRepairOptions{failoverScope: scope, targets: []string{"anthropic", "openai"}}
						switch policy {
						case "deny":
							opts.policy = &tenant.ProviderLockConfig{Enabled: true, DenyProviders: []string{"ANTH*"}}
						case "allow":
							opts.policy = &tenant.ProviderLockConfig{Enabled: true, AllowedProviders: []string{"open*"}}
						case "unrestricted":
							opts.policy = &tenant.ProviderLockConfig{Enabled: true}
						case "disabled":
							opts.policy = &tenant.ProviderLockConfig{Enabled: false, DenyProviders: []string{"anthropic"}}
						case "inherited":
							opts.global = config.ProviderLockConfig{Enabled: true, DenyProviders: []string{"anthropic"}}
						}
						srv, calls := newOrgLockRepairServer(t, opts)
						target := "openai"
						if source == "none" {
							target = ""
						}
						w := postOrgProviderLock(t, srv, "sk-agentcc-lock-a", source, target, stream)
						if w.Code != http.StatusOK {
							t.Fatalf("status = %d, want 200: %s", w.Code, w.Body.String())
						}
						wantProvider := "anthropic"
						if policy == "deny" || policy == "allow" || (policy == "unrestricted" && source != "none") {
							wantProvider = "openai"
						}
						for id, counter := range calls {
							want := int32(0)
							if id == wantProvider {
								want = 1
							}
							if got := counter.Load(); got != want {
								t.Errorf("%s calls = %d, want %d", id, got, want)
							}
						}
					})
				}
			}
		}
	}
}

func TestChatCompletion_OrgProviderLockLaterFailoverHops(t *testing.T) {
	for _, scope := range []string{"gateway", "org"} {
		for _, stream := range []bool{false, true} {
			for _, source := range []string{"header", "metadata", "none"} {
				t.Run(fmt.Sprintf("%s/stream=%t/%s", scope, stream, source), func(t *testing.T) {
					srv, calls := newOrgLockRepairServer(t, orgLockRepairOptions{
						failoverScope: scope, targets: []string{"openai", "anthropic", "backup"}, failingPrimary: true,
						policy: &tenant.ProviderLockConfig{Enabled: true, DenyProviders: []string{"ANTH*"}},
					})
					target, wantStatus, wantBackup := "openai", http.StatusBadGateway, int32(0)
					if source == "none" {
						target, wantStatus, wantBackup = "", http.StatusOK, 1
					}
					w := postOrgProviderLock(t, srv, "sk-agentcc-lock-a", source, target, stream)
					if w.Code != wantStatus {
						t.Errorf("status = %d, want %d: %s", w.Code, wantStatus, w.Body.String())
					}
					if calls["openai"].Load() != 1 || calls["anthropic"].Load() != 0 || calls["backup"].Load() != wantBackup {
						t.Errorf("calls = openai:%d anthropic:%d backup:%d; want 1/0/%d", calls["openai"].Load(), calls["anthropic"].Load(), calls["backup"].Load(), wantBackup)
					}
				})
			}
		}
	}
}

func TestChatCompletion_OrgProviderLockNoAdmittedFailoverHop(t *testing.T) {
	for _, scope := range []string{"gateway", "org"} {
		for _, stream := range []bool{false, true} {
			for _, source := range []string{"header", "metadata", "none"} {
				t.Run(fmt.Sprintf("%s/stream=%t/%s", scope, stream, source), func(t *testing.T) {
					opts := orgLockRepairOptions{failoverScope: scope, targets: []string{"anthropic", "backup"}, policy: &tenant.ProviderLockConfig{Enabled: true}}
					target := "openai"
					if source == "none" {
						target = ""
						opts.policy.DenyProviders = []string{"*"}
					}
					srv, calls := newOrgLockRepairServer(t, opts)
					w := postOrgProviderLock(t, srv, "sk-agentcc-lock-a", source, target, stream)
					if w.Code != http.StatusForbidden {
						t.Errorf("status = %d, want 403: %s", w.Code, w.Body.String())
					}
					var response models.ErrorResponse
					if err := json.Unmarshal(w.Body.Bytes(), &response); err != nil {
						t.Fatalf("decode refusal: %v", err)
					}
					if response.Error.Message != "provider is not permitted by the routing policy" {
						t.Errorf("refusal must be value-free: %q", response.Error.Message)
					}
					for id, counter := range calls {
						if got := counter.Load(); got != 0 {
							t.Errorf("%s calls = %d, want 0", id, got)
						}
					}
					if strings.Contains(w.Header().Get("Content-Type"), "text/event-stream") {
						t.Error("refusal committed SSE headers")
					}
				})
			}
		}
	}
}

func TestChatCompletion_OrgProviderLockModelFallback(t *testing.T) {
	for _, stream := range []bool{false, true} {
		for _, withTargets := range []bool{false, true} {
			for _, source := range []string{"header", "metadata", "none"} {
				t.Run(fmt.Sprintf("stream=%t/targets=%t/%s", stream, withTargets, source), func(t *testing.T) {
					opts := orgLockRepairOptions{failoverScope: "org", failingPrimary: true, modelFallback: true,
						policy: &tenant.ProviderLockConfig{Enabled: true, DenyProviders: []string{"ANTH*"}}}
					if withTargets {
						opts.targets = []string{"openai", "anthropic"}
					}
					srv, calls := newOrgLockRepairServer(t, opts)
					target := "openai"
					if source == "none" {
						target = ""
					}
					w := postOrgProviderLock(t, srv, "sk-agentcc-lock-a", source, target, stream)
					if w.Code != http.StatusBadGateway {
						t.Errorf("status = %d, want 502: %s", w.Code, w.Body.String())
					}
					if calls["openai"].Load() != 1 || calls["anthropic"].Load() != 0 {
						t.Errorf("calls = openai:%d anthropic:%d, want 1/0", calls["openai"].Load(), calls["anthropic"].Load())
					}
				})
			}
		}
	}
}
