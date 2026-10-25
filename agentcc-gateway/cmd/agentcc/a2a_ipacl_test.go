package main

import (
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"
)

// An A2A task's model call and the IP rules. The A2A executor replays each
// task as a chat request it builds itself, carrying the A2A caller's client
// IP (a2aTaskCarriesClientIP). These tests run the real binary and call it
// from 127.0.0.1, with an A2A message that reaches the model call (a text
// part, metadata.model).

const (
	keyA2APlain = "sk-agentcc-check-a2a-plain-key-0123456789ab"
	keyA2AAllow = "sk-agentcc-check-a2a-allow-key-0123456789ab"
	keyA2ADeny  = "sk-agentcc-check-a2a-deny-key-0123456789abc"
	keyA2AKeyIP = "sk-agentcc-check-a2a-keyip-key-0123456789ab"

	a2aSendBody = `{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":{"role":"user","messageId":"m1","parts":[{"type":"text","text":"hi"}]},"metadata":{"model":"gpt-4o"}}}`
)

// a2aTask is the part of an A2A message/send answer these tests read.
type a2aTask struct {
	State   string // the task's status.state
	Message string // the text of its status message
	Answer  string // the text of its artifacts (the model's answer)
}

// a2aSend sends a2aSendBody to POST /a2a as key and returns the HTTP
// status, the task (zero when the answer carries none) and the raw body.
func a2aSend(t *testing.T, g *readinessGateway, key string) (int, a2aTask, string) {
	t.Helper()
	req, _ := http.NewRequest(http.MethodPost, g.url+"/a2a", strings.NewReader(a2aSendBody))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	req.Header.Set("Authorization", "Bearer "+key)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("POST /a2a: %v", err)
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	var rpc struct {
		Result *struct {
			Status struct {
				State   string `json:"state"`
				Message []struct {
					Text string `json:"text"`
				} `json:"message"`
			} `json:"status"`
			Artifacts []struct {
				Parts []struct {
					Text string `json:"text"`
				} `json:"parts"`
			} `json:"artifacts"`
		} `json:"result"`
	}
	var task a2aTask
	if json.Unmarshal(b, &rpc) == nil && rpc.Result != nil {
		task.State = rpc.Result.Status.State
		for _, m := range rpc.Result.Status.Message {
			task.Message += m.Text
		}
		for _, a := range rpc.Result.Artifacts {
			for _, p := range a.Parts {
				task.Answer += p.Text
			}
		}
	}
	return resp.StatusCode, task, strings.TrimSpace(string(b))
}

// checkA2ATask fails t unless the A2A answer is 200 with a task that
// completed with the upstream's answer ("hi", startMediaProvider's) when
// refusal is "", or that failed with refusal in its status message.
func checkA2ATask(t *testing.T, who string, code int, task a2aTask, raw, refusal string) {
	t.Helper()
	if code != http.StatusOK {
		t.Errorf("%s: POST /a2a -> %d %.300s; want 200 with a task", who, code, raw)
		return
	}
	if refusal == "" {
		if task.State != "completed" || task.Answer != "hi" {
			t.Errorf("%s: A2A task %q %q (answer %q); want completed with the upstream's answer \"hi\" (%.300s)", who, task.State, task.Message, task.Answer, raw)
		}
		return
	}
	if task.State != "failed" || !strings.Contains(task.Message, refusal) {
		t.Errorf("%s: A2A task %q %q; want failed with %q (%.300s)", who, task.State, task.Message, refusal, raw)
	}
}

// TestA2ATaskModelCallIPRules: on a managed gateway (admin token set, node
// ip_acl off, as the shipped configs run) that resolves client IPs (a trusted
// proxy is set) with a2a.enabled, an A2A task's model call meets the same IP
// rules as the caller's chat call: an org's allow list holding the caller,
// and a key's allowed_ips holding it, let the task complete. An org's deny
// list holding the caller is refused at the A2A request itself, 403 before
// any task (KeyAuth applies an org's ip_acl on the key-gated routes). The
// caller's chat call shows the rules themselves judge 127.0.0.1 as intended.
func TestA2ATaskModelCallIPRules(t *testing.T) {
	provider := startMediaProvider(t)
	g := startReadinessGateway(t, `server:
  host: 127.0.0.1
  port: %d
  trusted_proxies: ["192.0.2.255/32"] # resolves client IPs; the peer stays the client
`+ipProviderBlock(provider.URL)+`auth:
  enabled: true
  keys:
    - name: a2a-plain
      key: `+keyA2APlain+`
      owner: org-a2a-plain
      key_type: internal
      metadata: {org_id: org-a2a-plain}
    - name: a2a-allow
      key: `+keyA2AAllow+`
      owner: org-a2a-allow
      key_type: internal
      metadata: {org_id: org-a2a-allow}
    - name: a2a-deny
      key: `+keyA2ADeny+`
      owner: org-a2a-deny
      key_type: internal
      metadata: {org_id: org-a2a-deny}
    - name: a2a-keyip
      key: `+keyA2AKeyIP+`
      owner: org-a2a-keyip
      key_type: internal
      allowed_ips: ["127.0.0.1/32"]
      metadata: {org_id: org-a2a-keyip}
admin:
  token: `+testAdminToken+`
a2a:
  enabled: true
`)
	for org, body := range map[string]string{
		"org-a2a-allow": `{"ip_acl":{"enabled":true,"allow":["127.0.0.1/32"]}}`,
		"org-a2a-deny":  `{"ip_acl":{"enabled":true,"deny":["127.0.0.1/32"]}}`,
	} {
		if code, resp := adminCall(t, g, http.MethodPut, "/-/orgs/"+org+"/config", body); code != http.StatusOK {
			t.Fatalf("PUT /-/orgs/%s/config -> %d %s", org, code, resp)
		}
	}
	const (
		orgRefusal = "Access denied: IP address is not allowed"
		keyRefusal = "API key is not authorized from this IP address"
	)
	chat := pipelineRoutes[0]
	for _, c := range []struct {
		who, key               string
		chatCode               int
		taskRefusal, switchOff string // taskRefusal: the task's refusal, "" when it completes; switchOff: what the same rule answers a model call that carries no client IP
		refusedAtRequest       bool   // KeyAuth refuses the A2A request
	}{
		{"org with no IP rule", keyA2APlain, http.StatusOK, "", "", false},
		{"org ip_acl allow [127.0.0.1/32]", keyA2AAllow, http.StatusOK, "", orgRefusal, false},
		{"org ip_acl deny [127.0.0.1/32]", keyA2ADeny, http.StatusForbidden, "", "", true},
		{"key allowed_ips [127.0.0.1/32]", keyA2AKeyIP, http.StatusOK, "", keyRefusal, false},
	} {
		who := c.who + ", caller 127.0.0.1"
		if code, body := pipelinePost(t, g, c.key, chat.path, chat.body); code != c.chatCode {
			t.Errorf("%s: POST %s -> %d %.200s; want %d", who, chat.path, code, body, c.chatCode)
		}
		refusal := c.switchOff
		if a2aTaskCarriesClientIP {
			refusal = c.taskRefusal
		}
		code, task, raw := a2aSend(t, g, c.key)
		t.Logf("%s: POST /a2a -> %d, task %s %q", who, code, task.State, task.Message)
		if c.refusedAtRequest {
			if code != http.StatusForbidden || !strings.Contains(raw, orgRefusal) || task.State != "" {
				t.Errorf("%s: POST /a2a -> %d, task %q %.200s; want 403 %q before any task", who, code, task.State, raw, orgRefusal)
			}
			continue
		}
		checkA2ATask(t, who, code, task, raw, refusal)
	}
}

// TestA2ATaskModelCallNodeIPACL: with ip_acl.enabled and a node allow list
// holding the caller, the A2A request passes KeyAuth's check and the task's
// model call meets the node's list and completes (a2aTaskCarriesClientIP). An
// org whose deny list holds the caller is refused at the A2A request itself
// (403, no task): with the switch on, KeyAuth judges the caller's address.
func TestA2ATaskModelCallNodeIPACL(t *testing.T) {
	provider := startMediaProvider(t)
	g := startReadinessGateway(t, `server:
  host: 127.0.0.1
  port: %d
`+ipProviderBlock(provider.URL)+`auth:
  enabled: true
  keys:
    - name: a2a-plain
      key: `+keyA2APlain+`
      owner: org-a2a-plain
      key_type: internal
      metadata: {org_id: org-a2a-plain}
    - name: a2a-deny
      key: `+keyA2ADeny+`
      owner: org-a2a-deny
      key_type: internal
      metadata: {org_id: org-a2a-deny}
admin:
  token: `+testAdminToken+`
ip_acl:
  enabled: true
  allow: ["127.0.0.1/32"]
a2a:
  enabled: true
`)
	if code, resp := adminCall(t, g, http.MethodPut, "/-/orgs/org-a2a-deny/config", `{"ip_acl":{"enabled":true,"deny":["127.0.0.1/32"]}}`); code != http.StatusOK {
		t.Fatalf("PUT /-/orgs/org-a2a-deny/config -> %d %s", code, resp)
	}
	chat := pipelineRoutes[0]
	if code, body := pipelinePost(t, g, keyA2APlain, chat.path, chat.body); code != http.StatusOK {
		t.Errorf("ip_acl allow [127.0.0.1/32], caller 127.0.0.1: POST %s -> %d %.200s; want 200", chat.path, code, body)
	}
	refusal := "Access denied: IP address is not allowed"
	if a2aTaskCarriesClientIP {
		refusal = ""
	}
	code, task, raw := a2aSend(t, g, keyA2APlain)
	t.Logf("ip_acl allow [127.0.0.1/32], caller 127.0.0.1: POST /a2a -> %d, task %s %q", code, task.State, task.Message)
	checkA2ATask(t, "ip_acl allow [127.0.0.1/32], caller 127.0.0.1", code, task, raw, refusal)
	code, _, raw = a2aSend(t, g, keyA2ADeny)
	t.Logf("ip_acl.enabled; org deny [127.0.0.1/32], caller 127.0.0.1: POST /a2a -> %d %.120s", code, raw)
	if code != http.StatusForbidden || !strings.Contains(raw, "IP address is not allowed") || strings.Contains(raw, `"result"`) {
		t.Errorf("ip_acl.enabled; org deny [127.0.0.1/32], caller 127.0.0.1: POST /a2a -> %d %.200s; want 403 \"IP address is not allowed\" and no task", code, raw)
	}
}
