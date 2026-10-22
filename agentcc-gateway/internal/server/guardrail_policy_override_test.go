package server

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"net/http/httputil"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// X-Guardrail-Policy lets a request pick its guardrail policy (log-only,
// disabled, strict) only when its key has allow_policy_override. These tests
// run a gateway whose only static guardrail is on the OUTPUT (post stage) and
// blocks every response, with streaming guardrails on: a key without the
// permission never switches the output check off.

const (
	overrideKeyWithout = "sk-guardrail-no-override"
	overrideKeyWith    = "sk-guardrail-may-override"
	mockAnswer         = "How can I help" // in both of startMockOpenAI's answers
)

type outputBlocker struct{}

func (outputBlocker) Name() string            { return "output-blocker" }
func (outputBlocker) Stage() guardrails.Stage { return guardrails.StagePost }
func (outputBlocker) Check(context.Context, *guardrails.CheckInput) *guardrails.CheckResult {
	return &guardrails.CheckResult{Pass: false, Score: 0.99, Message: "output blocked by policy"}
}

// countingUpstream forwards to startMockOpenAI and counts the chat calls.
func countingUpstream(t *testing.T) (string, *atomic.Int64) {
	t.Helper()
	mock := startMockOpenAI(t)
	t.Cleanup(mock.Close)
	target, _ := url.Parse(mock.URL)
	proxy := httputil.NewSingleHostReverseProxy(target)
	proxy.FlushInterval = -1
	var calls atomic.Int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/chat/completions" {
			calls.Add(1)
		}
		proxy.ServeHTTP(w, r)
	}))
	t.Cleanup(srv.Close)
	return srv.URL, &calls
}

func outputGuardrailServer(t *testing.T) (*Server, *atomic.Int64) {
	t.Helper()
	upstream, calls := countingUpstream(t)
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "no-override", Key: overrideKeyWithout, Owner: "o", KeyType: "byok",
			Metadata: map[string]string{"org_id": "org-guard"}},
		{Name: "may-override", Key: overrideKeyWith, Owner: "o", KeyType: "byok",
			Metadata: map[string]string{"org_id": "org-guard", "allow_policy_override": "true"}},
	}
	cfg.Guardrails = config.GuardrailsConfig{
		Enabled:        true,
		DefaultTimeout: 2 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "output-blocker", Stage: "post", Mode: "sync", Action: "block", Threshold: 0.5},
		},
		Streaming: config.StreamingGuardrailConfig{Enabled: true, CheckInterval: 5, FailureAction: "stop"},
	}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstream, APIFormat: "openai", Models: []string{"gpt-4o"}}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	grRegistry := map[string]guardrails.Guardrail{"output-blocker": outputBlocker{}}
	grEngine := guardrails.NewEngine(cfg.Guardrails, grRegistry)
	if grEngine.PreCount() != 0 || grEngine.PostCount() != 1 {
		t.Fatalf("set-up: want one post-stage rule and none before the provider, got pre %d post %d", grEngine.PreCount(), grEngine.PostCount())
	}
	ks := auth.NewKeyStore(cfg.Auth)
	tenants := tenant.NewStore()
	tenants.Set("org-guard", orgOpenAI("gpt-4o")) // the org's own provider, on the same upstream
	policies := policy.NewStore()
	eng := pipeline.NewEngine(
		authplugin.New(ks, true),
		guardrails.NewPlugin(grEngine, grRegistry, nil, policies, tenants),
	)
	srv := New(cfg, "", registry, eng, ks, grEngine, policies, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	return srv, calls
}

func overrideChat(t *testing.T, srv *Server, key string, stream bool, policyHeader string) (int, string) {
	t.Helper()
	body := `{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`
	if stream {
		body = `{"model":"gpt-4o","stream":true,"messages":[{"role":"user","content":"hi"}]}`
	}
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", bytes.NewReader([]byte(body)))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+key)
	if policyHeader != "" {
		req.Header.Set("X-Guardrail-Policy", policyHeader)
	}
	ctx, cancel := context.WithTimeout(req.Context(), 5*time.Second)
	defer cancel()
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req.WithContext(ctx))
	b, _ := io.ReadAll(w.Result().Body)
	return w.Code, string(b)
}

func streamLabel(stream bool) string {
	if stream {
		return "stream"
	}
	return "non-stream"
}

// A key without allow_policy_override that sends X-Guardrail-Policy is
// refused with 403 before the provider is called, whatever the value, on a
// gateway whose static guardrails are all on the output.
func TestGuardrailPolicyOverride_KeyWithoutPermissionIsRefusedBeforeTheProvider(t *testing.T) {
	for _, stream := range []bool{false, true} {
		for _, value := range []string{"disabled", "log-only", "strict", "not-a-policy"} {
			t.Run(streamLabel(stream)+"/"+value, func(t *testing.T) {
				srv, calls := outputGuardrailServer(t)

				code, body := overrideChat(t, srv, overrideKeyWithout, stream, value)

				if strings.Contains(body, mockAnswer) {
					t.Errorf("X-Guardrail-Policy: %s from a key without allow_policy_override switched the output guardrail off: status %d, body %q", value, code, body)
				}
				if code != http.StatusForbidden {
					t.Errorf("status %d, want 403; body %q", code, body)
				}
				if n := calls.Load(); n != 0 {
					t.Errorf("the provider was called %d times for a refused override, want 0", n)
				}
			})
		}
	}
}

// Without the header, the output guardrail blocks for both kinds of request
// (the set-up works).
func TestGuardrailPolicyOverride_OutputGuardrailBlocksWithoutTheHeader(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(streamLabel(stream), func(t *testing.T) {
			srv, _ := outputGuardrailServer(t)

			code, body := overrideChat(t, srv, overrideKeyWithout, stream, "")

			if strings.Contains(body, mockAnswer) {
				t.Errorf("the output guardrail let the blocked answer through: status %d, body %q", code, body)
			}
		})
	}
}

// A key that has allow_policy_override applies its per-request override.
func TestGuardrailPolicyOverride_KeyWithPermissionOverrides(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(streamLabel(stream), func(t *testing.T) {
			srv, _ := outputGuardrailServer(t)

			code, body := overrideChat(t, srv, overrideKeyWith, stream, "disabled")

			if code != http.StatusOK || !strings.Contains(body, mockAnswer) {
				t.Errorf("X-Guardrail-Policy: disabled from a key with allow_policy_override: status %d, body %q; want 200 with the answer", code, body)
			}
		})
	}
}
