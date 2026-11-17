package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/server"
)

// Fake keys for the identity wiring test.
const (
	identityWiringKey     = "m8-wiring-test-key"
	identityWiringExpired = "m8-wiring-test-key-expired"
)

// identityWiring builds a gateway with MCP on, request auth as given, and a
// key store holding one live and one expired key, and runs the identity step
// with the given config.
func identityWiring(t *testing.T, authEnabled, anonymousDev bool) (*server.Server, *wiringDeps) {
	t.Helper()
	return identityWiringWith(t, authEnabled, anonymousDev, "")
}

// identityWiringWith is identityWiring with mcp.static.org_id set to
// staticOrg, the org the operator's (YAML) servers are bound to ("" for none).
func identityWiringWith(t *testing.T, authEnabled, anonymousDev bool, staticOrg string) (*server.Server, *wiringDeps) {
	t.Helper()
	t.Setenv("AGENTCC_MCP_STATE_KEYS", mcpTestStateKey)
	cfg := config.DefaultConfig()
	cfg.MCP.Enabled = true
	cfg.MCP.AllowAnonymousDev = anonymousDev
	cfg.MCP.Static.OrgID = staticOrg
	cfg.Auth.Enabled = authEnabled
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "live", Key: identityWiringKey, Metadata: map[string]string{"org_id": "org-wiring"}},
		{Name: "expired", Key: identityWiringExpired, ExpiresAt: time.Now().Add(-time.Hour).UTC().Format(time.RFC3339)},
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("registry: %v", err)
	}
	var mdb atomic.Pointer[modeldb.ModelDB]
	mdb.Store(modeldb.New(modeldb.BundledModels, nil))
	srv := server.New(cfg, "", registry, pipeline.NewEngine(), keyStore, nil, nil, nil, &mdb, nil, nil)
	t.Cleanup(func() { srv.MCP().Close() })
	d := &wiringDeps{cfg: cfg, keyStore: keyStore}
	if err := initMCPIdentity(d, &mcp.Deps{}); err != nil {
		t.Fatalf("initMCPIdentity: %v", err)
	}
	// The vserver step tells the registry mcp.static.org_id, which decides
	// whether a keyless request reaches the operator's servers.
	if err := initMCPVServer(d, &mcp.Deps{}); err != nil {
		t.Fatalf("initMCPVServer: %v", err)
	}
	return srv, d
}

// mcpInitialize posts an initialize straight to the MCP server's handler with
// headers, and returns the HTTP status and the error's data.code, if any.
func mcpInitialize(t *testing.T, srv *server.Server, headers map[string]string) (int, string) {
	t.Helper()
	body, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 1, "method": "initialize",
		"params": map[string]any{"protocolVersion": "2025-06-18", "capabilities": map[string]any{}, "clientInfo": map[string]any{"name": "t", "version": "1"}}})
	req := httptest.NewRequest(http.MethodPost, "/mcp", bytes.NewReader(body))
	// MCP's POST handler takes only JSON (http_post.go answers 415 otherwise).
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	srv.MCP().HandlePost(w, req)
	var msg struct {
		Error *struct {
			Data struct {
				Code string `json:"code"`
			} `json:"data"`
		} `json:"error"`
	}
	_ = json.Unmarshal(w.Body.Bytes(), &msg)
	if msg.Error == nil {
		return w.Code, ""
	}
	return w.Code, msg.Error.Data.Code
}

// TestMCPIdentityInstalledWithRequestAuthOff: with request auth off, the
// server's routes attach no key authenticator, so before this step every MCP
// request was served and no key was read. After it, a credential that is
// sent is checked, and a keyless request is served as on the release line
// (TestNoKeyAuthServesWithoutAKey) while the
// operator's servers are bound to no org (mcp.static.org_id unset); once
// they are bound to one, a keyless request is 401 unless
// mcp.allow_anonymous_dev is on. With request auth on, the identity
// replaces the routes' authenticator.
func TestMCPIdentityInstalledWithRequestAuthOff(t *testing.T) {
	t.Run("request auth off", func(t *testing.T) {
		srv, d := identityWiring(t, false, false)
		if code, _ := mcpInitialize(t, srv, map[string]string{"x-api-key": identityWiringExpired}); code != http.StatusOK {
			t.Fatalf("before the identity step an expired key got %d; the test no longer shows the gap it closes", code)
		}
		d.server = srv
		if err := d.startServerWiring(); err != nil {
			t.Fatalf("startServerWiring: %v", err)
		}
		if code, reason := mcpInitialize(t, srv, nil); code != http.StatusOK {
			t.Fatalf("keyless after the identity step: %d %q, want 200 as on the release line", code, reason)
		}
		if code, _ := mcpInitialize(t, srv, map[string]string{"Authorization": "Bearer " + identityWiringKey}); code != http.StatusOK {
			t.Fatalf("a valid key: %d", code)
		}
		if code, reason := mcpInitialize(t, srv, map[string]string{"x-api-key": identityWiringExpired}); code != http.StatusUnauthorized || reason != mcp.AuthKeyExpired {
			t.Fatalf("an expired key: %d %q, want 401 key_expired", code, reason)
		}
		if code, reason := mcpInitialize(t, srv, map[string]string{"Authorization": "Bearer nope"}); code != http.StatusUnauthorized || reason != mcp.AuthInvalidAPIKey {
			t.Fatalf("a bad key: %d %q, want 401 invalid_api_key", code, reason)
		}
	})

	t.Run("request auth off, the operator's servers bound to an org", func(t *testing.T) {
		srv, d := identityWiringWith(t, false, false, "org-wiring")
		d.server = srv
		if err := d.startServerWiring(); err != nil {
			t.Fatalf("startServerWiring: %v", err)
		}
		if code, reason := mcpInitialize(t, srv, nil); code != http.StatusUnauthorized || reason != mcp.AuthMissingCredentials {
			t.Fatalf("keyless with mcp.static.org_id set: %d %q, want 401 missing_credentials", code, reason)
		}
		if code, _ := mcpInitialize(t, srv, map[string]string{"Authorization": "Bearer " + identityWiringKey}); code != http.StatusOK {
			t.Fatalf("a valid key of the static org: %d", code)
		}
	})

	t.Run("request auth on: a keyless request is 401", func(t *testing.T) {
		srv, d := identityWiring(t, true, false)
		d.server = srv
		if err := d.startServerWiring(); err != nil {
			t.Fatalf("startServerWiring: %v", err)
		}
		if code, reason := mcpInitialize(t, srv, nil); code != http.StatusUnauthorized || reason != mcp.AuthMissingCredentials {
			t.Fatalf("keyless with request auth on: %d %q, want 401 missing_credentials", code, reason)
		}
	})

	t.Run("mcp.allow_anonymous_dev", func(t *testing.T) {
		srv, d := identityWiring(t, false, true)
		d.server = srv
		if err := d.startServerWiring(); err != nil {
			t.Fatalf("startServerWiring: %v", err)
		}
		if code, _ := mcpInitialize(t, srv, nil); code != http.StatusOK {
			t.Fatalf("keyless under anonymous dev: %d", code)
		}
		if code, reason := mcpInitialize(t, srv, map[string]string{"Authorization": "Bearer nope"}); code != http.StatusUnauthorized || reason != mcp.AuthInvalidAPIKey {
			t.Fatalf("a bad key under anonymous dev: %d %q", code, reason)
		}
	})

	t.Run("request auth on: the identity replaces the routes' authenticator", func(t *testing.T) {
		srv, d := identityWiring(t, true, false)
		// The routes' authenticator knows only active or not.
		if code, reason := mcpInitialize(t, srv, map[string]string{"x-api-key": identityWiringExpired}); code != http.StatusUnauthorized || reason != mcp.AuthInvalidAPIKey {
			t.Fatalf("expired key before the identity step: %d %q", code, reason)
		}
		d.server = srv
		if err := d.startServerWiring(); err != nil {
			t.Fatalf("startServerWiring: %v", err)
		}
		if code, reason := mcpInitialize(t, srv, map[string]string{"x-api-key": identityWiringExpired}); code != http.StatusUnauthorized || reason != mcp.AuthKeyExpired {
			t.Fatalf("expired key after the identity step: %d %q, want key_expired from the identity", code, reason)
		}
	})

	t.Run("MCP disabled: nothing to install", func(t *testing.T) {
		d := &wiringDeps{server: newWiringServer(t, false)}
		if err := initMCPIdentity(d, &mcp.Deps{}); err != nil {
			t.Fatalf("initMCPIdentity: %v", err)
		}
		if err := d.startServerWiring(); err != nil {
			t.Fatalf("startServerWiring: %v", err)
		}
	})
}
