package main

import (
	"net/http"
	"strings"
	"testing"
)

// Through the real binary: an org's Model Database override holds that
// org's chat calls to its token limit and capability switches. The
// validation plugin main.go builds reads the org's overlay from the tenant
// store; an org without the override, and the same request on that org's
// other models, are served normally.

const (
	keyModelDB      = "sk-agentcc-chk-modeldb-org-key-0123456789ab"
	keyModelDBPlain = "sk-agentcc-chk-modeldb-plain-key-0123456789"
)

func TestOrgModelDatabaseLimitsOnChat(t *testing.T) {
	provider := startMediaProvider(t)
	g := startReadinessGateway(t, `server:
  host: 127.0.0.1
  port: %d
`+ipProviderBlock(provider.URL)+`auth:
  enabled: true
  keys:
    - name: modeldb
      key: `+keyModelDB+`
      owner: org-modeldb
      key_type: internal
      metadata: {org_id: org-modeldb}
    - name: plain
      key: `+keyModelDBPlain+`
      owner: org-plain
      key_type: internal
      metadata: {org_id: org-plain}
admin:
  token: `+testAdminToken+`
`)
	override := `{"model_database":{"overrides":{"gpt-4o":{"max_input_tokens":1000,"max_output_tokens":5,` +
		`"capabilities":{"function_calling":false}}}}}`
	if code, resp := adminCall(t, g, http.MethodPut, "/-/orgs/org-modeldb/config", override); code != http.StatusOK {
		t.Fatalf("PUT org config -> %d %s", code, resp)
	}

	overLimit := `{"model":"gpt-4o","max_tokens":500,"messages":[{"role":"user","content":"hi"}]}`
	withTools := `{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}],` +
		`"tools":[{"type":"function","function":{"name":"f","parameters":{"type":"object"}}}]}`
	inLimit := `{"model":"gpt-4o","max_tokens":5,"messages":[{"role":"user","content":"hi"}]}`

	code, body := pipelinePost(t, g, keyModelDB, "/v1/chat/completions", overLimit)
	if code != http.StatusBadRequest || !strings.Contains(body, "max_tokens_exceeds_limit") || !strings.Contains(body, "max_output_tokens 5") {
		t.Errorf("org max_output_tokens 5, max_tokens 500 -> %d %.300s; want 400 max_tokens_exceeds_limit naming max_output_tokens 5", code, body)
	}
	code, body = pipelinePost(t, g, keyModelDB, "/v1/chat/completions", withTools)
	if code != http.StatusBadRequest || !strings.Contains(body, "model_capability_error") || !strings.Contains(body, "function_calling") {
		t.Errorf("org function_calling off, request with tools -> %d %.300s; want 400 model_capability_error naming function_calling", code, body)
	}
	if code, body = pipelinePost(t, g, keyModelDB, "/v1/chat/completions", inLimit); code != http.StatusOK {
		t.Errorf("org override, max_tokens 5 (within the limit) -> %d %.300s; want 200", code, body)
	}
	// Another org on the same node is not held to this org's override.
	for _, req := range []string{overLimit, withTools} {
		if code, body = pipelinePost(t, g, keyModelDBPlain, "/v1/chat/completions", req); code != http.StatusOK {
			t.Errorf("org without an override: %s -> %d %.300s; want 200", req, code, body)
		}
	}
}
