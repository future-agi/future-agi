package main

import (
	"encoding/json"
	"net/http"
	"reflect"
	"strings"
	"testing"
)

// An org's ip_acl is enforced, with the node's own ip_acl switch off, only
// on a gateway that resolves its callers' addresses: server.trusted_proxies
// or server.trusted_hops is set. Behind a load balancer with neither, every
// request seems to come from the balancer, so an org allow list would refuse
// every caller. On such a gateway the org rule is never applied, and GET
// /-/config leaves ip_acl out of org_sections_enforced and gives the reason in
// org_sections_not_enforced. The node's own switch keeps its behaviour: with
// ip_acl.enabled the node's and each org's rules apply to the address the
// gateway resolves (the TCP peer without trusted proxies), as before.

const (
	r7iKeyACLOut = "sk-agentcc-r7i-ipacl-out-key-0123456789abcd"
	r7iKeyACLIn  = "sk-agentcc-r7i-ipacl-in-key-0123456789abcde"

	// r7iUnusedProxy is a trusted proxy that is never the TCP peer: the
	// resolved client stays the peer, 127.0.0.1, so a test can tell "the
	// gateway resolves client IPs" apart from the address it resolves.
	r7iUnusedProxy = `  trusted_proxies: ["192.0.2.255/32"]
`
)

func r7iACLGateway(t *testing.T, serverExtra, extra string) *readinessGateway {
	t.Helper()
	provider := startR7MediaProvider(t)
	g := startReadinessGateway(t, `server:
  host: 127.0.0.1
  port: %d
`+serverExtra+r7IPProviderBlock(provider.URL)+`auth:
  enabled: true
  keys:
    - name: acl-out
      key: `+r7iKeyACLOut+`
      owner: org-r7i-acl-out
      key_type: internal
      metadata: {org_id: org-r7i-acl-out}
    - name: acl-in
      key: `+r7iKeyACLIn+`
      owner: org-r7i-acl-in
      key_type: internal
      metadata: {org_id: org-r7i-acl-in}
admin:
  token: `+testAdminToken+`
`+extra)
	for org, body := range map[string]string{
		"org-r7i-acl-out": `{"ip_acl":{"enabled":true,"allow":["10.9.9.9/32"]}}`,
		"org-r7i-acl-in":  `{"ip_acl":{"enabled":true,"allow":["127.0.0.1/32"]}}`,
	} {
		if code, resp := r7Admin(t, g, http.MethodPut, "/-/orgs/"+org+"/config", body); code != http.StatusOK {
			t.Fatalf("PUT /-/orgs/%s/config -> %d %s", org, code, resp)
		}
	}
	return g
}

// r7iConfigEnforcement reads org_sections_enforced and
// org_sections_not_enforced from GET /-/config.
func r7iConfigEnforcement(t *testing.T, g *readinessGateway) ([]string, map[string]string) {
	t.Helper()
	code, body := r7Admin(t, g, http.MethodGet, "/-/config", "")
	if code != http.StatusOK {
		t.Fatalf("GET /-/config -> %d %s", code, body)
	}
	var cfg struct {
		Enforced    []string          `json:"org_sections_enforced"`
		NotEnforced map[string]string `json:"org_sections_not_enforced"`
	}
	if err := json.Unmarshal([]byte(body), &cfg); err != nil {
		t.Fatalf("GET /-/config body: %v", err)
	}
	return cfg.Enforced, cfg.NotEnforced
}

func TestR7iOrgIPACLNotEnforcedWithoutTrustedProxies(t *testing.T) {
	g := r7iACLGateway(t, "", "")
	for _, rt := range r7PipelineRoutes {
		if code, body := r7Post(t, g, r7iKeyACLOut, rt.path, rt.body); code != http.StatusOK {
			t.Errorf("no trusted proxy, org allow list [10.9.9.9/32], caller 127.0.0.1: POST %s -> %d %.200s; want 200 (the org rule is never applied)", rt.path, code, body)
		}
	}
	enforced, notEnforced := r7iConfigEnforcement(t, g)
	for _, s := range enforced {
		if s == "ip_acl" {
			t.Errorf("no trusted proxy: org_sections_enforced = %v lists ip_acl", enforced)
		}
	}
	if got := notEnforced["ip_acl"]; got != "client IP unknown (trusted_proxies not set)" {
		t.Errorf("no trusted proxy: org_sections_not_enforced = %v; want ip_acl: \"client IP unknown (trusted_proxies not set)\"", notEnforced)
	}
}

func TestR7iOrgIPACLEnforcedWithTrustedProxies(t *testing.T) {
	for name, server := range map[string]string{
		"trusted_proxies": r7iUnusedProxy,
		"trusted_hops":    "  trusted_hops: 2\n",
	} {
		t.Run(name, func(t *testing.T) {
			g := r7iACLGateway(t, server, "")
			for _, rt := range r7PipelineRoutes {
				code, body := r7Post(t, g, r7iKeyACLOut, rt.path, rt.body)
				if code != http.StatusForbidden || !strings.Contains(body, "IP address is not allowed") {
					t.Errorf("%s set, org allow list [10.9.9.9/32], caller 127.0.0.1: POST %s -> %d %.200s; want 403 \"Access denied: IP address is not allowed\"", name, rt.path, code, body)
				}
				if code, body := r7Post(t, g, r7iKeyACLIn, rt.path, rt.body); code != http.StatusOK {
					t.Errorf("%s set, org allow list [127.0.0.1/32]: POST %s -> %d %.200s; want 200", name, rt.path, code, body)
				}
			}
			enforced, notEnforced := r7iConfigEnforcement(t, g)
			if want := []string{"alerting", "budgets", "ip_acl", "rate_limiting", "tool_policy"}; !reflect.DeepEqual(enforced, want) {
				t.Errorf("%s set: org_sections_enforced = %v, want %v", name, enforced, want)
			}
			if _, ok := notEnforced["ip_acl"]; ok {
				t.Errorf("%s set: org_sections_not_enforced = %v names ip_acl", name, notEnforced)
			}
		})
	}
}

// The node's own switch, unchanged: with ip_acl.enabled and no trusted proxy
// the org's rules apply to the TCP peer, as before this release.
func TestR7iNodeIPACLSwitchKeepsOrgRulesWithoutTrustedProxies(t *testing.T) {
	g := r7iACLGateway(t, "", "ip_acl:\n  enabled: true\n")
	code, body := r7Post(t, g, r7iKeyACLOut, "/v1/chat/completions", r7PipelineRoutes[0].body)
	if code != http.StatusForbidden || !strings.Contains(body, "IP address is not allowed") {
		t.Errorf("ip_acl.enabled, no trusted proxy, org allow list [10.9.9.9/32]: chat -> %d %.200s; want 403", code, body)
	}
	if code, body := r7Post(t, g, r7iKeyACLIn, "/v1/chat/completions", r7PipelineRoutes[0].body); code != http.StatusOK {
		t.Errorf("ip_acl.enabled, org allow list [127.0.0.1/32]: chat -> %d %.200s; want 200", code, body)
	}
	enforced, notEnforced := r7iConfigEnforcement(t, g)
	found := false
	for _, s := range enforced {
		found = found || s == "ip_acl"
	}
	if !found || notEnforced["ip_acl"] != "" {
		t.Errorf("ip_acl.enabled: org_sections_enforced = %v, not_enforced = %v; want ip_acl enforced", enforced, notEnforced)
	}
}

// Round 7 integrator line I4: on a gateway org configs reach that resolves
// client IPs, KeyAuth applies an org's ip_acl on the key-gated routes that
// make no model call (the models list, files, videos, scheduled and async
// jobs, stored responses, MCP, the A2A request) with the node's switch off,
// as the pipeline does on model calls.
func TestR7iOrgIPACLKeyGatedRoutesWithoutTheNodeSwitch(t *testing.T) {
	r7OrgIPACLKeyGatedRoutes(t, r7iUnusedProxy, "", "node ip_acl off, trusted proxy set")
}

// Without a trusted proxy the org rule is never applied there either.
func TestR7iOrgIPACLKeyGatedRoutesNotEnforcedWithoutTrustedProxies(t *testing.T) {
	g := r7iACLGateway(t, "", "")
	for _, path := range []string{"/v1/models", "/v1/files"} {
		req, _ := http.NewRequest(http.MethodGet, g.url+path, nil)
		req.Header.Set("Authorization", "Bearer "+r7iKeyACLOut)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatalf("GET %s: %v", path, err)
		}
		resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Errorf("no trusted proxy, org allow list [10.9.9.9/32]: GET %s -> %d; want 200 (the org rule is never applied)", path, resp.StatusCode)
		}
	}
}
