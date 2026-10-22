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
// org_sections_not_enforced. With the node's own switch, ip_acl.enabled, the
// node's and each org's rules apply to the address the gateway resolves (the
// TCP peer without trusted proxies).

const (
	keyACLOut = "sk-agentcc-chk-ipacl-out-key-0123456789abcd"
	keyACLIn  = "sk-agentcc-chk-ipacl-in-key-0123456789abcde"

	// unusedTrustedProxy is a trusted proxy that is never the TCP peer: the
	// resolved client stays the peer, 127.0.0.1, so a test can tell "the
	// gateway resolves client IPs" apart from the address it resolves.
	unusedTrustedProxy = `  trusted_proxies: ["192.0.2.255/32"]
`
)

func aclGateway(t *testing.T, serverExtra, extra string) *readinessGateway {
	t.Helper()
	provider := startMediaProvider(t)
	g := startReadinessGateway(t, `server:
  host: 127.0.0.1
  port: %d
`+serverExtra+ipProviderBlock(provider.URL)+`auth:
  enabled: true
  keys:
    - name: acl-out
      key: `+keyACLOut+`
      owner: org-acl-out
      key_type: internal
      metadata: {org_id: org-acl-out}
    - name: acl-in
      key: `+keyACLIn+`
      owner: org-acl-in
      key_type: internal
      metadata: {org_id: org-acl-in}
admin:
  token: `+testAdminToken+`
`+extra)
	for org, body := range map[string]string{
		"org-acl-out": `{"ip_acl":{"enabled":true,"allow":["10.9.9.9/32"]}}`,
		"org-acl-in":  `{"ip_acl":{"enabled":true,"allow":["127.0.0.1/32"]}}`,
	} {
		if code, resp := adminCall(t, g, http.MethodPut, "/-/orgs/"+org+"/config", body); code != http.StatusOK {
			t.Fatalf("PUT /-/orgs/%s/config -> %d %s", org, code, resp)
		}
	}
	return g
}

// configEnforcement reads org_sections_enforced and
// org_sections_not_enforced from GET /-/config.
func configEnforcement(t *testing.T, g *readinessGateway) ([]string, map[string]string) {
	t.Helper()
	code, body := adminCall(t, g, http.MethodGet, "/-/config", "")
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

func TestOrgIPACLNotEnforcedWithoutTrustedProxies(t *testing.T) {
	g := aclGateway(t, "", "")
	for _, rt := range pipelineRoutes {
		if code, body := pipelinePost(t, g, keyACLOut, rt.path, rt.body); code != http.StatusOK {
			t.Errorf("no trusted proxy, org allow list [10.9.9.9/32], caller 127.0.0.1: POST %s -> %d %.200s; want 200 (the org rule is never applied)", rt.path, code, body)
		}
	}
	enforced, notEnforced := configEnforcement(t, g)
	for _, s := range enforced {
		if s == "ip_acl" {
			t.Errorf("no trusted proxy: org_sections_enforced = %v lists ip_acl", enforced)
		}
	}
	if got := notEnforced["ip_acl"]; got != "client IP unknown (trusted_proxies not set)" {
		t.Errorf("no trusted proxy: org_sections_not_enforced = %v; want ip_acl: \"client IP unknown (trusted_proxies not set)\"", notEnforced)
	}
}

func TestOrgIPACLEnforcedWithTrustedProxies(t *testing.T) {
	for name, server := range map[string]string{
		"trusted_proxies": unusedTrustedProxy,
		"trusted_hops":    "  trusted_hops: 2\n",
	} {
		t.Run(name, func(t *testing.T) {
			g := aclGateway(t, server, "")
			for _, rt := range pipelineRoutes {
				code, body := pipelinePost(t, g, keyACLOut, rt.path, rt.body)
				if code != http.StatusForbidden || !strings.Contains(body, "IP address is not allowed") {
					t.Errorf("%s set, org allow list [10.9.9.9/32], caller 127.0.0.1: POST %s -> %d %.200s; want 403 \"Access denied: IP address is not allowed\"", name, rt.path, code, body)
				}
				if code, body := pipelinePost(t, g, keyACLIn, rt.path, rt.body); code != http.StatusOK {
					t.Errorf("%s set, org allow list [127.0.0.1/32]: POST %s -> %d %.200s; want 200", name, rt.path, code, body)
				}
			}
			enforced, notEnforced := configEnforcement(t, g)
			if want := []string{"alerting", "budgets", "ip_acl", "rate_limiting", "tool_policy"}; !reflect.DeepEqual(enforced, want) {
				t.Errorf("%s set: org_sections_enforced = %v, want %v", name, enforced, want)
			}
			if _, ok := notEnforced["ip_acl"]; ok {
				t.Errorf("%s set: org_sections_not_enforced = %v names ip_acl", name, notEnforced)
			}
		})
	}
}

// The node's own switch: with ip_acl.enabled and no trusted proxy the org's
// rules apply to the TCP peer.
func TestNodeIPACLSwitchKeepsOrgRulesWithoutTrustedProxies(t *testing.T) {
	g := aclGateway(t, "", "ip_acl:\n  enabled: true\n")
	code, body := pipelinePost(t, g, keyACLOut, "/v1/chat/completions", pipelineRoutes[0].body)
	if code != http.StatusForbidden || !strings.Contains(body, "IP address is not allowed") {
		t.Errorf("ip_acl.enabled, no trusted proxy, org allow list [10.9.9.9/32]: chat -> %d %.200s; want 403", code, body)
	}
	if code, body := pipelinePost(t, g, keyACLIn, "/v1/chat/completions", pipelineRoutes[0].body); code != http.StatusOK {
		t.Errorf("ip_acl.enabled, org allow list [127.0.0.1/32]: chat -> %d %.200s; want 200", code, body)
	}
	enforced, notEnforced := configEnforcement(t, g)
	found := false
	for _, s := range enforced {
		found = found || s == "ip_acl"
	}
	if !found || notEnforced["ip_acl"] != "" {
		t.Errorf("ip_acl.enabled: org_sections_enforced = %v, not_enforced = %v; want ip_acl enforced", enforced, notEnforced)
	}
}

// On a gateway org configs reach that resolves client IPs, KeyAuth applies
// an org's ip_acl on the key-gated routes that make no model call (the
// models list, files, videos, scheduled and async jobs, stored responses,
// MCP, the A2A request) with the node's switch off, as the pipeline does on
// model calls.
func TestOrgIPACLKeyGatedRoutesWithoutTheNodeSwitch(t *testing.T) {
	orgIPACLKeyGatedRoutes(t, unusedTrustedProxy, "", "node ip_acl off, trusted proxy set")
}

// Without a trusted proxy the org rule is never applied there either.
func TestOrgIPACLKeyGatedRoutesNotEnforcedWithoutTrustedProxies(t *testing.T) {
	g := aclGateway(t, "", "")
	for _, path := range []string{"/v1/models", "/v1/files"} {
		req, _ := http.NewRequest(http.MethodGet, g.url+path, nil)
		req.Header.Set("Authorization", "Bearer "+keyACLOut)
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
