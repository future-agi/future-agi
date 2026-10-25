package main

import (
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

// TestOrgIPACLKeyGatedRoutesWithTheNodeSwitch proves what config.example's
// "Org config sections" block and the Helm values note tell operators: with
// ip_acl.enabled (no node list), an org's ip_acl also covers the key-gated
// routes that make no model call. On each of them, an org whose allow list
// holds the caller gets the route's own answer, and an org whose list does
// not is refused with the IP rule's answer before the route runs: the models
// list, files, videos, scheduled and async jobs, stored responses, MCP and
// A2A. An A2A task's own model call then meets the pipeline's rules by the
// A2A caller's address (a2aTaskCarriesClientIP).
// TestOrgIPACLKeyGatedRoutesWithoutTheNodeSwitch runs the same routes with
// the switch off on a gateway that resolves client IPs.
func TestOrgIPACLKeyGatedRoutesWithTheNodeSwitch(t *testing.T) {
	orgIPACLKeyGatedRoutes(t, "", "ip_acl:\n  enabled: true\n", "ip_acl.enabled")
}

// orgIPACLKeyGatedRoutes runs the key-gated routes above on a node with
// serverExtra under server: and nodeExtra at the top level; label names the
// node in each failure.
func orgIPACLKeyGatedRoutes(t *testing.T, serverExtra, nodeExtra, label string) {
	t.Helper()
	provider := startMediaProvider(t)
	g := startReadinessGateway(t, `server:
  host: 127.0.0.1
  port: %d
`+serverExtra+ipProviderBlock(provider.URL)+`auth:
  enabled: true
  keys:
    - name: ip-in
      key: `+keyIPIn+`
      owner: org-ip-in
      key_type: internal
      metadata: {org_id: org-ip-in}
    - name: ip-out
      key: `+keyIPOut+`
      owner: org-ip-out
      key_type: internal
      metadata: {org_id: org-ip-out}
admin:
  token: `+testAdminToken+`
`+nodeExtra+`mcp:
  enabled: true
  state_keys: ["checks-mcp-state-key-0123456789abcdef"]
a2a:
  enabled: true
`)
	for org, body := range map[string]string{
		"org-ip-in":  `{"ip_acl":{"enabled":true,"allow":["127.0.0.1/32"]}}`,
		"org-ip-out": `{"ip_acl":{"enabled":true,"allow":["10.9.9.9/32"]}}`,
	} {
		if code, resp := adminCall(t, g, http.MethodPut, "/-/orgs/"+org+"/config", body); code != http.StatusOK {
			t.Fatalf("PUT /-/orgs/%s/config -> %d %s", org, code, resp)
		}
	}
	do := func(key, method, path, body string) (int, string) {
		var rd io.Reader
		if body != "" {
			rd = strings.NewReader(body)
		}
		req, _ := http.NewRequest(method, g.url+path, rd)
		req.Header.Set("Authorization", "Bearer "+key)
		if body != "" {
			req.Header.Set("Content-Type", "application/json")
			req.Header.Set("Accept", "application/json, text/event-stream")
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatalf("%s %s: %v", method, path, err)
		}
		defer resp.Body.Close()
		b, _ := io.ReadAll(resp.Body)
		return resp.StatusCode, strings.TrimSpace(string(b))
	}
	for _, rt := range []struct{ method, path, body string }{
		{http.MethodGet, "/v1/models", ""},
		{http.MethodGet, "/v1/files", ""},
		{http.MethodGet, "/v1/videos", ""},
		{http.MethodGet, "/v1/scheduled", ""},
		{http.MethodGet, "/v1/async/job_ipacl_missing", ""},
		{http.MethodGet, "/v1/responses/resp_ipacl_missing", ""},
		{http.MethodPost, "/mcp", `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"ipacl-test","version":"1"}}}`},
	} {
		code, body := do(keyIPIn, rt.method, rt.path, rt.body)
		t.Logf("allowed caller: %s %s -> %d", rt.method, rt.path, code)
		if code == http.StatusForbidden || strings.Contains(body, "IP address") {
			t.Errorf("%s; org allow [127.0.0.1/32], caller 127.0.0.1: %s %s -> %d %.200s; want the route's own answer, not an IP refusal", label, rt.method, rt.path, code, body)
		}
		code, body = do(keyIPOut, rt.method, rt.path, rt.body)
		if code != http.StatusForbidden || !strings.Contains(body, "IP address is not allowed") {
			t.Errorf("%s; org allow [10.9.9.9/32], caller 127.0.0.1: %s %s -> %d %.200s; want 403 \"IP address is not allowed\"", label, rt.method, rt.path, code, body)
		}
	}

	// A2A, with a message that reaches the model call (a text part,
	// metadata.model). The allowed caller passes KeyAuth's check and gets a
	// task, and the task's model call completes under the org's IP rule.
	code, task, raw := a2aSend(t, g, keyIPIn)
	t.Logf("allowed caller: POST /a2a -> %d, task %s %q", code, task.State, task.Message)
	refusal := "Access denied: IP address is not allowed"
	if a2aTaskCarriesClientIP {
		refusal = ""
	}
	checkA2ATask(t, label+"; org allow [127.0.0.1/32], caller 127.0.0.1", code, task, raw, refusal)
	code, _, raw = a2aSend(t, g, keyIPOut)
	if code != http.StatusForbidden || !strings.Contains(raw, "IP address is not allowed") {
		t.Errorf("%s; org allow [10.9.9.9/32], caller 127.0.0.1: POST /a2a -> %d %.200s; want 403 \"IP address is not allowed\" before any task", label, code, raw)
	}
}

// TestOrgIPACLAsyncJob: on a managed gateway (node ip_acl off) that resolves
// client IPs (a trusted proxy is set), an async chat job of an org whose
// ip_acl allows the caller completes. The job runs the pipeline again on a
// worker, after its request has returned; it carries the client IP its
// request resolved, so the org's ip_acl reads it there too.
func TestOrgIPACLAsyncJob(t *testing.T) {
	provider := startMediaProvider(t)
	g := startReadinessGateway(t, `server:
  host: 127.0.0.1
  port: %d
  trusted_proxies: ["192.0.2.255/32"] # resolves client IPs; the peer stays the client
`+ipProviderBlock(provider.URL)+`auth:
  enabled: true
  keys:
    - name: ip-in
      key: `+keyIPIn+`
      owner: org-ip-in
      key_type: internal
      metadata: {org_id: org-ip-in}
admin:
  token: `+testAdminToken+`
`)
	if code, resp := adminCall(t, g, http.MethodPut, "/-/orgs/org-ip-in/config", `{"ip_acl":{"enabled":true,"allow":["127.0.0.1/32"]}}`); code != http.StatusOK {
		t.Fatalf("PUT /-/orgs/org-ip-in/config -> %d %s", code, resp)
	}
	req, _ := http.NewRequest(http.MethodPost, g.url+"/v1/chat/completions",
		strings.NewReader(`{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+keyIPIn)
	req.Header.Set("x-agentcc-async", "true")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	var sub struct {
		JobID string `json:"job_id"`
	}
	b, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusAccepted || json.Unmarshal(b, &sub) != nil || sub.JobID == "" {
		t.Fatalf("async submit -> %d %s; want 202 with a job_id", resp.StatusCode, b)
	}
	for deadline := time.Now().Add(10 * time.Second); ; time.Sleep(50 * time.Millisecond) {
		req, _ := http.NewRequest(http.MethodGet, g.url+"/v1/async/"+sub.JobID, nil)
		req.Header.Set("Authorization", "Bearer "+keyIPIn)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		b, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		var job struct {
			Status string          `json:"status"`
			Error  json.RawMessage `json:"error"`
		}
		_ = json.Unmarshal(b, &job)
		switch job.Status {
		case "completed":
			t.Logf("async job %s: completed", sub.JobID)
			return
		case "failed":
			t.Fatalf("org allow [127.0.0.1/32], caller 127.0.0.1: async job failed: %s; want completed", b)
		}
		if time.Now().After(deadline) {
			t.Fatalf("async job still %q after 10s: %s", job.Status, b)
		}
	}
}
