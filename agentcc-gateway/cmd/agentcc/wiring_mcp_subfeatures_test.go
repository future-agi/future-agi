package main

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credentials"
	"github.com/futureagi/agentcc-gateway/internal/mcp/tenancy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestDeploymentClientOAuthOffOverridesLegacyConfig(t *testing.T) {
	t.Setenv("AGENTCC_MCP_CLIENT_OAUTH_ENABLED", "false")
	path := filepath.Join(t.TempDir(), "gateway.yaml")
	if err := os.WriteFile(path, []byte("mcp:\n  enabled: true\n  dev_insecure_state: true\n  public_url: https://gw.example.test\n  oauth:\n    enabled: true\n    issuer: https://app.example.test/agentcc/mcp-oauth\n"), 0600); err != nil {
		t.Fatal(err)
	}
	cfg, err := config.Load(path)
	if err != nil {
		t.Fatal(err)
	}
	deps := &mcp.Deps{}
	if err := initMCPOAuth(&wiringDeps{cfg: cfg}, deps); err != nil {
		t.Fatal(err)
	}
	if deps.BearerVerifier != nil || deps.ResourceMetadataURL != "" {
		t.Fatal("OFF deployment advertises or accepts OAuth")
	}
}

func TestDeploymentUpstreamOAuthDefaultOff(t *testing.T) {
	cfg := oauthWiringConfig(nil)
	cfg.MCP.Django.BaseURL = "https://backend.example.test"
	cfg.MCP.Django.InternalTokenSecret = "synthetic-subfeature-secret-for-test"
	cfg.MCP.DevInsecureState = true
	deps := &mcp.Deps{}
	if err := initMCPCredentials(&wiringDeps{cfg: cfg}, deps); err != nil {
		t.Fatal(err)
	}
	resolver, resolverOK := deps.CredentialResolver.(*credentials.PushedResolver)
	shared, sharedOK := deps.SharedCredentials.(*credentials.PushedResolver)
	if !resolverOK || resolver == nil || !sharedOK || shared == nil {
		t.Fatal("OFF deployment installs upstream OAuth")
	}
	if _, ok := deps.CredentialResolver.(mcp.PushedGrantRevoker); !ok {
		t.Fatal("static credential resolver cannot revoke pushed grants")
	}

	ctx := context.Background()
	auth, denial := deps.CredentialResolver.Resolve(&mcp.CallContext{Principal: mcp.Principal{OrgID: "org-static", UserID: "u1"}})
	if auth != nil || denial != nil {
		t.Fatal("static resolution must leave the per-user credential unavailable")
	}
	if headers, err := deps.SharedCredentials.SharedAuth("org-static", "issues").Header(ctx); !errors.Is(err, mcp.ErrSharedCredentialsOff) || len(headers) != 0 {
		t.Fatalf("shared OAuth became available: %v", err)
	}

	srv := &mcp.Server{}
	srv.SetDeps(*deps)
	store := tenant.NewStore()
	var orgConfig tenant.OrgConfig
	if err := json.Unmarshal([]byte(`{"mcp":{"enabled":true,"servers":{"issues":{"url":"https://issues.example.test/mcp","credential":{"mode":"per_user"}}}}}`), &orgConfig); err != nil {
		t.Fatal(err)
	}
	store.Set("org-static", &orgConfig)
	manager := tenancy.New(tenancy.Options{Store: store, Deps: srv.Deps})
	defer manager.Close(ctx)
	_, pool, err := manager.Resolve("org-static")
	if err != nil {
		t.Fatal(err)
	}
	if ps, ok := pool.Server("issues"); !ok || !ps.PerUser {
		t.Fatal("fixture did not install the per-user server")
	}
	for _, user := range []string{"", "u1"} {
		client, err := pool.Client("issues", user, mcp.AuthOverride{})
		var refusal *mcp.Denial
		if client != nil || !errors.As(err, &refusal) || refusal.Code != mcp.CodeAuthRequired {
			t.Fatalf("per-user call without a token was not refused: %v", err)
		}
	}
}

func TestDeploymentK2OnDoesNotRequireUpstreamOAuthToStartCore(t *testing.T) {
	_, err := k2Wired(t, func(c *config.Config) {
		c.MCP.K2AppsEnabled = true
		c.MCP.UpstreamOAuthEnabled = false
		c.MCP.Vendors.K2RuntimeURL = "http://127.0.0.1:9"
	})
	if err != nil {
		t.Fatalf("independent switches prevented core MCP startup: %v", err)
	}
}
