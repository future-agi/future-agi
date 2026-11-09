package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// NG-03 (REL-R7 R7-OPS): an org's ip_acl, budgets, tool_policy and alerting
// sections are enforced by the gateway that receives them, with the node's own
// switches (ip_acl.enabled, budgets.enabled, tool_policy.enabled,
// alerting.enabled) left off, as DefaultConfig, config.example.yaml and the
// Helm values ship them. These tests run the real binary (main) in a child
// process, as readiness_test.go does, push the org sections through the admin
// API exactly as the backend does, and call the gateway from 127.0.0.1.

const (
	r7KeyACL     = "sk-agentcc-r7ops-org-acl-key-0123456789abcdef"
	r7KeyBudget  = "sk-agentcc-r7ops-org-budget-key-0123456789ab"
	r7KeyTools   = "sk-agentcc-r7ops-org-tools-key-0123456789abc"
	r7KeyAlert   = "sk-agentcc-r7ops-org-alert-key-0123456789abc"
	r7KeyGuarded = "sk-agentcc-r7ops-org-guarded-key-0123456789"
	r7KeyNothing = "sk-agentcc-r7ops-org-nothing-key-0123456789"
)

// r7Hook is an org alert channel's webhook receiver that counts deliveries.
type r7Hook struct {
	*httptest.Server
	posts atomic.Int32
}

func startR7Hook(t *testing.T) *r7Hook {
	t.Helper()
	h := &r7Hook{}
	h.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		if r.Method == http.MethodPost {
			h.posts.Add(1)
		}
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(h.Close)
	return h
}

// r7OrgKeys are config keys, each bound to its own org, that may use the
// gateway's own provider (the readiness tests' provider on loopback).
func r7OrgKeys() string {
	var b strings.Builder
	for _, k := range []struct{ name, key, org string }{
		{"acl", r7KeyACL, "org-acl"},
		{"budget", r7KeyBudget, "org-budget"},
		{"tools", r7KeyTools, "org-tools"},
		{"alert", r7KeyAlert, "org-alert"},
		{"guarded", r7KeyGuarded, "org-guarded"},
		{"nothing", r7KeyNothing, "org-nothing"},
	} {
		fmt.Fprintf(&b, "    - name: %s\n      key: %s\n      owner: %s\n      key_type: internal\n      metadata: {org_id: %s}\n", k.name, k.key, k.org, k.org)
	}
	return b.String()
}

// r7Admin sends an admin request and returns its status and body.
func r7Admin(t *testing.T, g *readinessGateway, method, path, body string) (int, string) {
	t.Helper()
	req, _ := http.NewRequest(method, g.url+path, strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+testAdminToken)
	if body != "" {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("%s %s: %v", method, path, err)
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, string(b)
}

// r7Chat sends a chat request as key with the given extra body fields.
func r7Chat(t *testing.T, g *readinessGateway, key, extra string) (int, string) {
	t.Helper()
	body := `{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]` + extra + `}`
	req, _ := http.NewRequest(http.MethodPost, g.url+"/v1/chat/completions", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+key)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("chat: %v", err)
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, string(b)
}

// r7Sections reads org_sections_enforced from GET /-/config: nil when the
// field is absent.
func r7Sections(t *testing.T, g *readinessGateway) []string {
	t.Helper()
	code, body := r7Admin(t, g, http.MethodGet, "/-/config", "")
	if code != http.StatusOK {
		t.Fatalf("GET /-/config -> %d %s", code, body)
	}
	var cfg struct {
		Sections *[]string `json:"org_sections_enforced"`
	}
	if err := json.Unmarshal([]byte(body), &cfg); err != nil {
		t.Fatalf("GET /-/config body %q: %v", body, err)
	}
	if cfg.Sections == nil {
		return nil
	}
	return *cfg.Sections
}

func hostPort(t *testing.T, raw string) string {
	t.Helper()
	u, err := url.Parse(raw)
	if err != nil {
		t.Fatal(err)
	}
	return u.Host
}

// TestR7OrgPluginsInstalled: every per-org section the console saves takes
// effect on a gateway whose node switches are all off (the shipped configs).
func TestR7OrgPluginsInstalled(t *testing.T) {
	provider := startChatProvider(t)
	allowed, refused := startR7Hook(t), startR7Hook(t)
	// The operator lists only the allowed receiver as an exception to the
	// guarded egress; the other loopback receiver must stay refused.
	g := startReadinessGateway(t, `server:
  host: 127.0.0.1
  port: %d
  trusted_proxies: ["192.0.2.255/32"] # resolves client IPs; the peer stays the client
providers:
  openai:
    base_url: `+provider.URL+`
    api_key: fake-operator-key
    api_format: openai
    models: [gpt-4o]
auth:
  enabled: true
  keys:
`+r7OrgKeys()+`admin:
  token: `+testAdminToken+`
mcp:
  egress:
    private_allow: ["`+hostPort(t, allowed.URL)+`"]
`)

	put := func(org, body string) {
		t.Helper()
		if code, resp := r7Admin(t, g, http.MethodPut, "/-/orgs/"+org+"/config", body); code != http.StatusOK {
			t.Fatalf("PUT /-/orgs/%s/config -> %d %s", org, code, resp)
		}
	}
	put("org-acl", `{"ip_acl":{"enabled":true,"allow":["10.9.9.9/32"]}}`)
	put("org-budget", `{"budgets":{"enabled":true,"org_limit":0.000001,"hard_limit":true}}`)
	put("org-tools", `{"tool_policy":{"enabled":true,"default_action":"reject","deny":["shell_exec"]}}`)
	alerting := func(hook string) string {
		return `{"alerting":{"enabled":true,` +
			`"rules":[{"name":"every-request","metric":"request_count","condition":">=","threshold":1,"window":"1m","cooldown":"1h","channels":["hook"]}],` +
			`"channels":[{"name":"hook","type":"webhook","url":"` + hook + `"}]}}`
	}
	put("org-alert", alerting(allowed.URL))
	put("org-guarded", alerting(refused.URL))
	put("org-nothing", `{"providers":{}}`)

	t.Run("ip_acl", func(t *testing.T) {
		// allow 10.9.9.9/32; this request comes from 127.0.0.1.
		code, body := r7Chat(t, g, r7KeyACL, "")
		if code != http.StatusForbidden {
			t.Errorf("org ip_acl allow [10.9.9.9/32], caller 127.0.0.1, node ip_acl off -> %d %s; want 403", code, body)
		}
	})
	t.Run("budgets", func(t *testing.T) {
		first, _ := r7Chat(t, g, r7KeyBudget, "")
		second, body := r7Chat(t, g, r7KeyBudget, "")
		if first != http.StatusOK || second != http.StatusTooManyRequests || !strings.Contains(body, "org_budget_exceeded") {
			t.Errorf("org budget $0.000001 hard, node budgets off: %d then %d %s; want 200 then 429 org_budget_exceeded", first, second, body)
		}
	})
	t.Run("tool_policy", func(t *testing.T) {
		code, body := r7Chat(t, g, r7KeyTools, `,"tools":[{"type":"function","function":{"name":"shell_exec","parameters":{"type":"object"}}}]`)
		if code != http.StatusForbidden || !strings.Contains(body, "shell_exec") {
			t.Errorf("org tool_policy reject [shell_exec], node tool_policy off -> %d %s; want 403 naming shell_exec", code, body)
		}
	})
	t.Run("alerting through the guarded egress", func(t *testing.T) {
		if code, body := r7Chat(t, g, r7KeyGuarded, ""); code != http.StatusOK {
			t.Fatalf("org-guarded chat -> %d %s", code, body)
		}
		if code, body := r7Chat(t, g, r7KeyAlert, ""); code != http.StatusOK {
			t.Fatalf("org-alert chat -> %d %s", code, body)
		}
		deadline := time.Now().Add(15 * time.Second)
		for allowed.posts.Load() == 0 && time.Now().Before(deadline) {
			time.Sleep(20 * time.Millisecond)
		}
		if n := allowed.posts.Load(); n == 0 {
			t.Fatalf("org alert rule request_count >= 1 on a webhook in mcp.egress.private_allow: %d deliveries in 15 s, want 1", n)
		}
		time.Sleep(time.Second) // the refused org's alert was evaluated before the allowed one
		if n := refused.posts.Load(); n != 0 {
			t.Errorf("an org webhook on a loopback address the operator did not list received %d alert(s); the guarded egress must refuse it", n)
		}
	})
	t.Run("an org that set nothing", func(t *testing.T) {
		code, body := r7Chat(t, g, r7KeyNothing, `,"tools":[{"type":"function","function":{"name":"shell_exec","parameters":{"type":"object"}}}]`)
		if code != http.StatusOK {
			t.Errorf("org with no ip_acl/budgets/tool_policy/alerting -> %d %s; want 200", code, body)
		}
	})
	t.Run("org_sections_enforced", func(t *testing.T) {
		got := r7Sections(t, g)
		want := []string{"alerting", "budgets", "ip_acl", "rate_limiting", "tool_policy"}
		if !reflect.DeepEqual(got, want) {
			t.Errorf("GET /-/config org_sections_enforced = %v, want %v (no node cache: cache is not enforced)", got, want)
		}
	})
}

// TestR7OrgSectionsEnforcedFollowsTheNode: cache is listed only on a node with
// a cache, and rate_limiting only with the node's rate_limiting switch on
// (contract K2).
func TestR7OrgSectionsEnforcedFollowsTheNode(t *testing.T) {
	provider := startChatProvider(t)
	g := startReadinessGateway(t, `server:
  host: 127.0.0.1
  port: %d
  trusted_proxies: ["192.0.2.255/32"] # resolves client IPs; the peer stays the client
providers:
  openai:
    base_url: `+provider.URL+`
    api_key: fake-operator-key
    api_format: openai
    models: [gpt-4o]
admin:
  token: `+testAdminToken+`
cache:
  enabled: true
rate_limiting:
  enabled: false
`)
	got := r7Sections(t, g)
	want := []string{"alerting", "budgets", "cache", "ip_acl", "tool_policy"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("node cache on, rate_limiting off: org_sections_enforced = %v, want %v", got, want)
	}
}
