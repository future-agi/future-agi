package server

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// INT-3C r4: HOSTED-ONEKEY-ACTIVATION-1 composed with MIXED-HOP-GUARD-1. An
// activated hosted node (AGENTCC_HOSTED_ONEKEY_ACTIVATION=true with a
// qualified managed provider) runs the same runtime mixed-hop guard as any
// node, with mixed billing chains off, and a hosted /-/reload cannot widen
// what it bills: not by a mirror rule that targets the managed provider, not
// by turning mixed billing chains on, not by a fallback profile and not by
// another managed provider. Synthetic values only; nothing is dialled.

// activatedHostedBase is an activated hosted config with one qualified
// managed provider, "paid". activatedHostedEnv supplies the operator's part.
const activatedHostedBase = `auth: {enabled: true}
guardrails: {enabled: true}
server:
  trusted_proxies: ["192.0.2.10/32"]
  max_request_timeout: 300s
redis: {enabled: true, address: "redis:6379", timeout: 1s}
mcp: {enabled: false}
tools: {enabled: false}
providers: {paid: {base_url: 'https://upstream.synthetic.invalid', api_format: openai, managed: true, auto_discover: false}}
`

func activatedHostedSecret(t *testing.T) string {
	t.Helper()
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		t.Fatal(err)
	}
	return hex.EncodeToString(b)
}

// activatedHostedEnv sets the hosted operator environment with activation on
// and returns the admin token.
func activatedHostedEnv(t *testing.T) string {
	t.Helper()
	admin := activatedHostedSecret(t)
	t.Setenv("AGENTCC_ADMIN_TOKEN", admin)
	t.Setenv("AGENTCC_INTERNAL_API_KEY", activatedHostedSecret(t))
	t.Setenv("AGENTCC_WEBHOOK_SECRET", activatedHostedSecret(t))
	t.Setenv("AGENTCC_DEPLOYMENT_PROFILE", "hosted")
	t.Setenv("AGENTCC_HOSTED_ONEKEY_ACTIVATION", "true")
	t.Setenv("AGENTCC_CONTROL_PLANE_URL", "http://backend:8000")
	t.Setenv("AGENTCC_SYNC_ON_STARTUP", "true")
	t.Setenv("AGENTCC_SYNC_INTERVAL", "60s")
	t.Setenv("AGENTCC_REDIS_ADDRESS", "redis:6379")
	return admin
}

func writeActivatedHosted(t *testing.T, path, text string) {
	t.Helper()
	if !strings.Contains(text, "\nmanaged:") {
		text += "managed: {spend_guard: {max_usd_per_minute: 5}}\n"
	}
	if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestHostedOneKeyActivatedNodeKeepsMixedHopGuard(t *testing.T) {
	activatedHostedEnv(t)
	path := filepath.Join(t.TempDir(), "gateway.yaml")
	writeActivatedHosted(t, path, activatedHostedBase)
	cfg, err := config.Load(path)
	if err != nil {
		t.Fatalf("the qualified activated hosted config was refused: %v", err)
	}
	if !cfg.IsHosted() || !cfg.Providers["paid"].Managed {
		t.Fatal("fixture is not an activated hosted node with a managed provider")
	}
	if cfg.Managed.MixedBillingChains {
		t.Fatal("activated hosted config allows mixed billing chains")
	}
	// The registry only stands in: the guard's setting is read from cfg, and a
	// hosted registry would run the TLS preflight against the provider.
	registry, err := providers.NewRegistry(config.DefaultConfig())
	if err != nil {
		t.Fatal(err)
	}
	defer registry.Close()
	srv := New(cfg, path, registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), tenant.NewStore(), nil)
	if srv.handlers.mixedBillingChains {
		t.Fatal("the activated node's handlers allow mixed billing chains")
	}
	for _, tc := range []struct {
		chain, attempt models.CredentialType
		want           string
	}{
		{models.CredentialManaged, models.CredentialBYOK, models.ReasonPolicy},
		{models.CredentialBYOK, models.CredentialManaged, models.ReasonPolicy},
		{models.CredentialManaged, models.CredentialManaged, ""},
		{models.CredentialBYOK, models.CredentialBYOK, ""},
	} {
		rc := &models.RequestContext{}
		rc.Billing.ChainMode = tc.chain
		if got := srv.handlers.mixedBillingRefusal(rc, tc.attempt); got != tc.want {
			t.Errorf("activated node, %s chain, %s attempt: refusal %q, want %q", tc.chain, tc.attempt, got, tc.want)
		}
	}
}

func TestHostedOneKeyReloadCannotWidenBilling(t *testing.T) {
	admin := activatedHostedEnv(t)
	path := filepath.Join(t.TempDir(), "gateway.yaml")
	writeActivatedHosted(t, path, activatedHostedBase)
	start, err := config.Load(path)
	if err != nil {
		t.Fatalf("the qualified activated hosted config was refused: %v", err)
	}
	registry, err := providers.NewRegistry(config.DefaultConfig())
	if err != nil {
		t.Fatal(err)
	}
	defer registry.Close()
	h := &Handlers{}
	// A running mirror to an operator provider: a refused reload must keep it.
	running := routing.NewMirror(config.MirrorConfig{Enabled: true, Rules: []config.MirrorRule{{SourceModel: "sentinel", TargetProvider: "operator"}}}, nil)
	if running == nil {
		t.Fatal("fixture mirror not built")
	}
	h.mirror.Store(running)
	s := &Server{cfg: start, configPath: path, hosted: true, registry: registry, handlers: h}

	reload := func(t *testing.T, text string) (int, string) {
		t.Helper()
		writeActivatedHosted(t, path, text)
		r := httptest.NewRequest(http.MethodPost, "/-/reload", nil)
		r.Header.Set("Authorization", "Bearer "+admin)
		w := httptest.NewRecorder()
		s.reloadHandler(w, r)
		return w.Code, w.Body.String()
	}
	mirrorToPaid := "routing:\n  mirror:\n    enabled: true\n    rules:\n      - {source_model: \"*\", target_provider: paid, sample_rate: 1}\n"
	for _, tc := range []struct{ name, extra, want string }{
		{"mirror targets the managed provider", mirrorToPaid, `target_provider \"paid\" is a managed provider`},
		{"disabled mirror rule targets the managed provider", strings.Replace(mirrorToPaid, "enabled: true", "enabled: false", 1), `target_provider \"paid\" is a managed provider`},
		{"mixed billing chains on", "managed: {mixed_billing_chains: true, spend_guard: {max_usd_per_minute: 5}}\n", "hosted one-key refuses managed.mixed_billing_chains=true"},
		{"model fallback profile", "routing: {model_fallbacks: {catalogue/model: [token/model]}}\n", "hosted profile refuses model fallback chains, failover and retries"},
		{"failover profile", "routing: {failover: {enabled: true}}\n", "hosted profile refuses model fallback chains, failover and retries"},
		{"retry profile", "routing: {retry: {enabled: true}}\n", "hosted profile refuses model fallback chains, failover and retries"},
		{"another managed provider", "", "hosted infrastructure changes require restart"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			text := activatedHostedBase + tc.extra
			if tc.name == "another managed provider" {
				text = strings.Replace(activatedHostedBase, "providers: {paid:", "providers: {more: {base_url: 'https://other.synthetic.invalid', api_format: openai, managed: true, auto_discover: false}, paid:", 1)
			}
			code, body := reload(t, text)
			if code != http.StatusBadRequest || !strings.Contains(body, `"validation_failed"`) || !strings.Contains(body, tc.want) {
				t.Fatalf("reload answered %d %s; want 400 validation_failed naming %q", code, body, tc.want)
			}
			if h.mirror.Load() != running {
				t.Fatal("a refused reload replaced the running mirror")
			}
			if h.mixedBillingChains || s.cfg != start {
				t.Fatal("a refused reload changed the running configuration")
			}
		})
	}
	// Control: the unchanged file reloads, so each refusal above is the change's.
	code, body := reload(t, activatedHostedBase)
	var answer map[string]any
	if code != http.StatusOK || json.Unmarshal([]byte(body), &answer) != nil || answer["status"] != "reloaded" {
		t.Fatalf("unchanged activated hosted config reload answered %d %s; want 200 reloaded", code, body)
	}
	if h.mirror.Load() != nil {
		t.Fatal("the reloaded config has no mirror, but the running mirror was kept")
	}
}
