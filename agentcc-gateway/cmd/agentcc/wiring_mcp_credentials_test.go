package main

import (
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credentials"
)

// The credentials step installs the per-user resolver only with the Django
// channel configured, and refuses to start on a half-configured channel or a
// secret that is also an admin token, without printing the secret.
func TestMCPCredentialsWiring(t *testing.T) {
	const secret = "m25-test-internal-secret-not-real"
	base := func(mutate func(*config.Config)) *config.Config {
		cfg := config.DefaultConfig()
		cfg.MCP.Enabled = true
		cfg.MCP.UpstreamOAuthEnabled = true
		cfg.MCP.K2AppsEnabled = true
		cfg.MCP.StateKeys = []string{mcpTestStateKey}
		cfg.Admin.Token = "m25-test-admin-token-not-real"
		if mutate != nil {
			mutate(cfg)
		}
		return cfg
	}
	run := func(cfg *config.Config) (*mcp.Deps, error) {
		deps := &mcp.Deps{}
		return deps, initMCPCredentials(&wiringDeps{cfg: cfg}, deps)
	}

	for name, cfg := range map[string]*config.Config{
		"mcp off":    base(func(c *config.Config) { c.MCP.Enabled = false; c.MCP.Django.BaseURL = "http://django.internal:8000" }),
		"no channel": base(nil),
	} {
		deps, err := run(cfg)
		if err != nil || deps.CredentialResolver != nil {
			t.Fatalf("%s: resolver %v, error %v; want none installed", name, deps.CredentialResolver, err)
		}
	}

	deps, err := run(base(func(c *config.Config) {
		c.MCP.Django = config.MCPDjangoConfig{BaseURL: "http://django.internal:8000", InternalTokenSecret: secret}
	}))
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := deps.CredentialResolver.(*credentials.Resolver); !ok {
		t.Fatalf("the resolver installed is %T", deps.CredentialResolver)
	}
	if _, ok := deps.CredentialResolver.(mcp.CredentialPurger); !ok {
		t.Fatal("the installed resolver does not purge")
	}

	for name, mutate := range map[string]func(*config.Config){
		"secret without base url": func(c *config.Config) { c.MCP.Django.InternalTokenSecret = secret },
		"base url without secret": func(c *config.Config) { c.MCP.Django.BaseURL = "http://django.internal:8000" },
		"secret is the admin token": func(c *config.Config) {
			c.MCP.Django = config.MCPDjangoConfig{BaseURL: "http://django.internal:8000", InternalTokenSecret: c.Admin.Token}
		},
		"secret is the control plane's admin token": func(c *config.Config) {
			c.ControlPlane.AdminToken = secret
			c.MCP.Django = config.MCPDjangoConfig{BaseURL: "http://django.internal:8000", InternalTokenSecret: secret}
		},
	} {
		cfg := base(mutate)
		_, err := run(cfg)
		if err == nil {
			t.Fatalf("%s: the gateway would start", name)
		}
		if strings.Contains(err.Error(), secret) || strings.Contains(err.Error(), cfg.Admin.Token) {
			t.Fatalf("%s: the error prints a secret", name)
		}
	}
}
