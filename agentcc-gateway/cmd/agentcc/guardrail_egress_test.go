package main

// The org factory
// main.go wires into the guardrail plugin builds an org's webhook, external
// and FutureAGI eval checks with the guarded egress dialer, built from the
// operator list mcp.egress.private_allow (orgEgressDialer). Every destination
// is an httptest server on 127.0.0.1; FI_BASE_URL is set to a loopback
// listener or cleared in every case, so nothing reaches a public host.

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// egressSink is a loopback server that counts calls and keeps the last body.
type egressSink struct {
	srv  *httptest.Server
	mu   sync.Mutex
	hits int
	body []byte
}

func newEgressSink(t *testing.T) *egressSink {
	t.Helper()
	s := &egressSink{}
	s.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		buf := new(bytes.Buffer)
		_, _ = buf.ReadFrom(r.Body)
		s.mu.Lock()
		s.hits++
		s.body = buf.Bytes()
		s.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		// A passing webhook verdict and a passing FutureAGI evaluation at once.
		_, _ = w.Write([]byte(`{"pass":true,"score":0,"result":[{"evaluations":[{"name":"t","output":"Passed","reason":"ok","evalId":"15"}]}]}`))
	}))
	t.Cleanup(s.srv.Close)
	return s
}

func (s *egressSink) count() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.hits
}

func (s *egressSink) hostPort() string { return strings.TrimPrefix(s.srv.URL, "http://") }

// orgChecks are the org check configs that name a callback, each pointing at
// base (the sink's URL, or a spelling of it).
func orgChecks(base string) map[string]map[string]interface{} {
	return map[string]map[string]interface{}{
		"webhook": {"url": base + "/hook", "headers": map[string]interface{}{"X-Org": "org-a"}},
		"lakera":  {"provider": "lakera", "endpoint": base + "/v2/guard", "api_key": "planted-lakera-key"},
		"futureagi-eval": {"provider": "futureagi", "eval_id": "15", "base_url": base,
			"api_key": "planted-org-api-key", "secret_key": "planted-org-secret-key"},
	}
}

// runOrgCallbackCheck runs one org check through the guardrail plugin with the factory
// main.go builds for dialer.
func runOrgCallbackCheck(t *testing.T, dialer *egress.Dialer, name string, cfg map[string]interface{}) {
	t.Helper()
	store := tenant.NewStore()
	store.Set("org-a", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{
		Checks: map[string]*tenant.GuardrailCheck{name: {Enabled: true, Action: "log", Config: cfg}},
	}})
	plugin := guardrails.NewPlugin(nil, map[string]guardrails.Guardrail{}, newDynamicGuardrailFactory(dialer), nil, store)
	plugin.ProcessRequest(context.Background(), &models.RequestContext{
		RequestID: "req-egress",
		Metadata:  map[string]string{"org_id": "org-a"},
		Timings:   map[string]time.Duration{},
		Request: &models.ChatCompletionRequest{Model: "gpt-4o",
			Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"an org prompt"`)}}},
	})
}

func operatorDialer(t *testing.T, allow ...string) *egress.Dialer {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.MCP.Egress.PrivateAllow = allow
	if err := cfg.Validate(); err != nil {
		t.Fatalf("config with private_allow %v: %v", allow, err)
	}
	d, err := orgEgressDialer(cfg)
	if err != nil {
		t.Fatalf("orgEgressDialer(%v): %v", allow, err)
	}
	return d
}

// An org check at a loopback address the operator did not list is refused:
// the sink is never reached, by IP or by a name that resolves to loopback,
// and neither is a second loopback server the first one redirects to.
func TestOrgGuardrailCallbacks_UnlistedLoopbackRefused(t *testing.T) {
	t.Setenv("FI_BASE_URL", "")
	for name, spell := range map[string]func(string) string{
		"by IP":   func(u string) string { return u },
		"by name": func(u string) string { return strings.Replace(u, "127.0.0.1", "localhost", 1) },
	} {
		for check := range orgChecks("") {
			t.Run(check+" "+name, func(t *testing.T) {
				sink := newEgressSink(t)
				runOrgCallbackCheck(t, operatorDialer(t), check, orgChecks(spell(sink.srv.URL))[check])
				if n := sink.count(); n != 0 {
					t.Fatalf("the org %s check reached an unlisted loopback server %d time(s)", check, n)
				}
			})
		}
	}

	t.Run("a redirect to an unlisted loopback server", func(t *testing.T) {
		target := newEgressSink(t)
		hop := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			http.Redirect(w, r, target.srv.URL+"/after", http.StatusTemporaryRedirect)
		}))
		defer hop.Close()
		runOrgCallbackCheck(t, operatorDialer(t, strings.TrimPrefix(hop.URL, "http://")), "webhook",
			orgChecks(hop.URL)["webhook"])
		if n := target.count(); n != 0 {
			t.Fatalf("the redirect target was reached %d time(s)", n)
		}
	})
}

// The operator list opens exactly what it names: the same checks reach a
// loopback server whose host:port the operator listed, with the request.
func TestOrgGuardrailCallbacks_ListedServerReached(t *testing.T) {
	t.Setenv("FI_BASE_URL", "")
	for check := range orgChecks("") {
		t.Run(check, func(t *testing.T) {
			sink := newEgressSink(t)
			runOrgCallbackCheck(t, operatorDialer(t, sink.hostPort()), check, orgChecks(sink.srv.URL)[check])
			if n := sink.count(); n != 1 {
				t.Fatalf("the org %s check reached the listed server %d time(s), want 1", check, n)
			}
			sink.mu.Lock()
			defer sink.mu.Unlock()
			if !bytes.Contains(sink.body, []byte("an org prompt")) {
				t.Fatalf("the listed server got %s, want the request", sink.body)
			}
		})
	}
}

// The operator's own backend (FI_BASE_URL) is reachable unlisted for the
// platform-filled default base_url.
func TestOrgGuardrailCallbacks_OperatorBackendReachedUnlisted(t *testing.T) {
	sink := newEgressSink(t)
	t.Setenv("FI_BASE_URL", sink.srv.URL)
	runOrgCallbackCheck(t, operatorDialer(t), "futureagi-eval", orgChecks("https://api.futureagi.com")["futureagi-eval"])
	if n := sink.count(); n != 1 {
		t.Fatalf("the operator backend was reached %d time(s), want 1", n)
	}
}

// orgEgressDialer refuses an entry config load would refuse, so a gateway
// never starts with a list it cannot honour.
func TestOrgEgressDialer_RefusesABadEntry(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.MCP.Egress.PrivateAllow = []string{"10.0.0.0/33"}
	if _, err := orgEgressDialer(cfg); err == nil {
		t.Fatal("orgEgressDialer accepted a malformed private_allow entry")
	}
}
