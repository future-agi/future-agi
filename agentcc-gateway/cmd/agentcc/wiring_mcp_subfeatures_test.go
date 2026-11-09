package main

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
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
	if deps.CredentialResolver != nil || deps.SharedCredentials != nil {
		t.Fatal("OFF deployment installs upstream OAuth")
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
