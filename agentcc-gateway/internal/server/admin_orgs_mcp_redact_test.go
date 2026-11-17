package server

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/futureagi/agentcc-gateway/internal/toolcatalog"
)

// mcpSecretsConfig is an org config carrying every MCP secret the org config allows.
const mcpSecretsConfig = `{"mcp":{"enabled":true,
	"servers":{
		"legacy":{"url":"https://l.example","auth":{"type":"api_key","header":"X-Key","key":"KEY-legacy-0123456789","token":"TOKEN-legacy-0123456789"}},
		"vaulted":{"url":"https://v.example","credential":{"mode":"shared","credential_id":"cred_1","secret":{"type":"bearer","value":"SECRET-server-0123456789"}}}},
	"managed_tools":{"enabled":true,"credentials":{"web":{"mode":"shared","credential_id":"cred_2","secret":{"type":"api_key","header":"x-api-key","value":"SECRET-managed-0123456789"}}}}}}`

var mcpSecretValues = []string{"KEY-legacy-0123456789", "TOKEN-legacy-0123456789", "SECRET-server-0123456789", "SECRET-managed-0123456789"}

// Every admin GET masks a server's auth.token and auth.key and the
// push-only credential.secret and managed_tools.credentials.*.secret values,
// and never changes the live config.
func TestRedactMCPSecrets(t *testing.T) {
	var cfg tenant.OrgConfig
	if err := json.Unmarshal([]byte(mcpSecretsConfig), &cfg); err != nil {
		t.Fatal(err)
	}
	raw, err := json.Marshal(redactOrgConfig(&cfg))
	if err != nil {
		t.Fatal(err)
	}
	for _, v := range mcpSecretValues {
		if strings.Contains(string(raw), v) {
			t.Fatalf("redacted config carries %s: %s", v, raw)
		}
	}
	for _, kept := range []string{`"credential_id":"cred_1"`, `"credential_id":"cred_2"`, `"header":"X-Key"`, `"value":"****"`, `"token":"****"`, `"key":"****"`} {
		if !strings.Contains(string(raw), kept) {
			t.Errorf("redacted config lost %s: %s", kept, raw)
		}
	}
	if cfg.MCP.Servers["legacy"].Auth.Token != "TOKEN-legacy-0123456789" ||
		cfg.MCP.Servers["vaulted"].Credential.Secret.Value != "SECRET-server-0123456789" ||
		cfg.MCP.ManagedTools.Credentials["web"].Secret.Value != "SECRET-managed-0123456789" {
		t.Fatal("redaction changed the live config")
	}
	if redactOrgConfig(&tenant.OrgConfig{}).MCP != nil {
		t.Fatal("a config without mcp gained one")
	}

	// Through the handlers: the single GET and the list.
	h := NewOrgConfigHandlers(tenant.NewStore(), "adm", nil)
	if w := orgsCall(h.SetOrgConfig, "PUT", "/-/orgs/o1/config?org_id=o1", mcpSecretsConfig); w.Code != http.StatusOK {
		t.Fatalf("PUT = %d %s", w.Code, w.Body.String())
	}
	for _, w := range []*httptest.ResponseRecorder{
		orgsCall(h.GetOrgConfig, "GET", "/-/orgs/o1/config?org_id=o1", ""),
		orgsCall(h.ListOrgConfigs, "GET", "/-/orgs/configs", ""),
	} {
		for _, v := range mcpSecretValues {
			if strings.Contains(w.Body.String(), v) {
				t.Fatalf("admin GET carries %s: %s", v, w.Body.String())
			}
		}
	}
}

func orgsCall(h http.HandlerFunc, method, target, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, target, bytes.NewReader([]byte(body)))
	req.Header.Set("Authorization", "Bearer adm")
	w := httptest.NewRecorder()
	h(w, req)
	return w
}

// With the tool catalog set, a PUT or a bulk load whose
// mcp.managed_tools allows a byok_only vendor's glob is 400
// capability_managed_not_allowed and stores nothing; with no catalog (tools
// off) nothing is checked.
func TestAdminOrgConfigRefusesManagedByokOnly(t *testing.T) {
	cat, err := toolcatalog.SeedCatalog()
	if err != nil {
		t.Fatal(err)
	}
	if c, ok := cat.Card("search", "brave"); !ok || c.ResaleStatus != toolcatalog.ResaleByokOnly {
		t.Fatalf("the seed catalog's search/brave card is not byok_only: %+v", c)
	}
	byok := `{"mcp":{"enabled":true,"managed_tools":{"enabled":true,"allow":["search/brave"]}}}`
	fine := `{"mcp":{"enabled":true,"managed_tools":{"enabled":true,"allow":["search/exa","fagi.web/*"]}}}`

	store := tenant.NewStore()
	h := NewOrgConfigHandlers(store, "adm", nil)
	h.SetToolCatalog(cat)
	w := orgsCall(h.SetOrgConfig, "PUT", "/-/orgs/o1/config?org_id=o1", byok)
	if w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), toolcatalog.CodeManagedNotAllowed) {
		t.Fatalf("PUT byok_only = %d %s", w.Code, w.Body.String())
	}
	if store.Get("o1") != nil {
		t.Fatal("a refused PUT was stored")
	}
	if w := orgsCall(h.SetOrgConfig, "PUT", "/-/orgs/o1/config?org_id=o1", fine); w.Code != http.StatusOK {
		t.Fatalf("PUT allowed globs = %d %s", w.Code, w.Body.String())
	}

	bulk := `{"o2":` + fine + `,"o3":` + byok + `}`
	w = orgsCall(h.BulkLoadOrgConfigs, "POST", "/-/orgs/configs/bulk", bulk)
	if w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), toolcatalog.CodeManagedNotAllowed) {
		t.Fatalf("bulk byok_only = %d %s", w.Code, w.Body.String())
	}
	if store.Get("o2") != nil || store.Get("o3") != nil || store.Get("o1") == nil {
		t.Fatal("a refused bulk load changed the store")
	}

	off := NewOrgConfigHandlers(tenant.NewStore(), "adm", nil)
	if w := orgsCall(off.SetOrgConfig, "PUT", "/-/orgs/o1/config?org_id=o1", byok); w.Code != http.StatusOK {
		t.Fatalf("no catalog: PUT = %d %s, want the check skipped", w.Code, w.Body.String())
	}
}
