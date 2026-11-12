package main

import (
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/emit"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/restmcp"
)

// TestServerJSONRuleMatchesTheRuntime holds the catalog build's server-json
// rule (emit.RuntimeServesProtectedResourceMetadata, IR-CN14c-6) to what this
// runtime does. A registry MCP client learns how to get the token a server
// needs from its OAuth protected-resource metadata (RFC 9728): at a
// /.well-known/oauth-protected-resource path, or named by resource_metadata
// in the 401's WWW-Authenticate. agentcc-connectors serves neither, so the
// build leaves every rest provider out of emit/server-json, the set OA-DIR
// would publish. A runtime that starts serving it fails here until the
// emit's constant says so; one that stops fails here too.
func TestServerJSONRuleMatchesTheRuntime(t *testing.T) {
	t.Setenv("AGENTCC_MCP_K2_APPS_ENABLED", "true")
	apps, _, err := loadApps(cn4Catalog)
	if err != nil {
		t.Fatal(err)
	}
	srv, err := restmcp.New(apps, restmcp.Options{Logger: slog.New(slog.NewTextHandler(io.Discard, nil))})
	if err != nil {
		t.Fatal(err)
	}
	ts := httptest.NewServer(handler(srv, apps))
	defer ts.Close()

	served := false
	for _, path := range []string{
		"/.well-known/oauth-protected-resource",
		"/.well-known/oauth-protected-resource/gmail/mcp",
		"/gmail/mcp/.well-known/oauth-protected-resource",
		"/gmail/.well-known/oauth-protected-resource",
	} {
		resp, err := http.Get(ts.URL + path)
		if err != nil {
			t.Fatal(err)
		}
		_ = resp.Body.Close()
		if resp.StatusCode == http.StatusOK {
			t.Logf("GET %s: 200 (%s)", path, resp.Header.Get("Content-Type"))
			served = true
		}
	}

	req, _ := http.NewRequest(http.MethodPost, ts.URL+"/gmail/mcp",
		strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"tools/list"}`))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	req.Header.Set("MCP-Protocol-Version", "2025-11-25")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	_ = resp.Body.Close()
	challenge := resp.Header.Get("WWW-Authenticate")
	if resp.StatusCode != http.StatusUnauthorized || challenge == "" {
		t.Fatalf("no bearer: HTTP %d, WWW-Authenticate %q; want 401 with a challenge", resp.StatusCode, challenge)
	}
	if strings.Contains(strings.ToLower(challenge), "resource_metadata") {
		served = true
	}

	if served != emit.RuntimeServesProtectedResourceMetadata {
		t.Errorf("agentcc-connectors serves protected-resource metadata: %v (401 challenge %q); "+
			"emit.RuntimeServesProtectedResourceMetadata is %v: change the constant with the runtime "+
			"(it decides which rest providers emit/server-json publishes)",
			served, challenge, emit.RuntimeServesProtectedResourceMetadata)
	}
}
