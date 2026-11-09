package main

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/oauthrs"
	"github.com/futureagi/agentcc-gateway/internal/mcp/oauthrs/oauthrstest"
)

// The OAuth wiring test's deployment. Every value is a placeholder.
const (
	oauthWiringPublicURL = "https://gw.example.test"
	oauthWiringIssuer    = "https://app.example.test/agentcc/mcp-oauth"
)

func oauthWiringConfig(mutate func(*config.MCPConfig)) *config.Config {
	cfg := config.DefaultConfig()
	cfg.MCP.Enabled = true
	cfg.MCP.PublicURL = oauthWiringPublicURL
	cfg.MCP.ClientOAuthEnabled = true
	cfg.MCP.OAuth.Enabled = true
	cfg.MCP.OAuth.Issuer = oauthWiringIssuer
	if mutate != nil {
		mutate(&cfg.MCP)
	}
	return cfg
}

// With mcp.oauth off (the release default) the step changes nothing: JWTs
// stay refused by the default verifier and the 401 stays the plain challenge.
func TestInitMCPOAuthOffChangesNothing(t *testing.T) {
	for name, cfg := range map[string]*config.Config{
		"oauth off": oauthWiringConfig(func(m *config.MCPConfig) { m.OAuth.Enabled = false }),
		"mcp off":   oauthWiringConfig(func(m *config.MCPConfig) { m.Enabled = false }),
		"no config": nil,
	} {
		deps := &mcp.Deps{}
		if err := initMCPOAuth(&wiringDeps{cfg: cfg}, deps); err != nil {
			t.Fatalf("%s: initMCPOAuth: %v", name, err)
		}
		if deps.BearerVerifier != nil || deps.ResourceMetadataURL != "" {
			t.Fatalf("%s: the step set the verifier %T or the metadata URL %q", name, deps.BearerVerifier, deps.ResourceMetadataURL)
		}
	}
}

// With mcp.oauth on, the step installs the resource server built from the
// configured public base and issuer: the 401's metadata URL is the
// endpoint's, and the verifier accepts the issuer's tokens, once its keys are
// pushed, on exactly the virtual server they name.
func TestInitMCPOAuthInstallsResourceServer(t *testing.T) {
	deps := &mcp.Deps{}
	if err := initMCPOAuth(&wiringDeps{cfg: oauthWiringConfig(nil)}, deps); err != nil {
		t.Fatalf("initMCPOAuth: %v", err)
	}
	if got, want := deps.ResourceMetadataURL, "https://gw.example.test/.well-known/oauth-protected-resource/mcp"; got != want {
		t.Fatalf("ResourceMetadataURL = %q, want %q", got, want)
	}
	v, ok := deps.BearerVerifier.(*oauthrs.Verifier)
	if !ok {
		t.Fatalf("BearerVerifier is %T, want the resource server's verifier", deps.BearerVerifier)
	}
	signer := oauthrstest.NewSigner(t, "as-wiring")
	if res, err := v.PushKeys(context.Background(), oauthrstest.PushBody(t, oauthWiringIssuer, 1, signer)); err != nil || !res.Applied {
		t.Fatalf("PushKeys = %+v, %v", res, err)
	}
	token := signer.Sign(t, oauthrstest.AccessClaims(oauthWiringIssuer, "https://gw.example.test/mcp/v/vs_A", time.Now(), 5*time.Minute))
	if p, err := v.Verify(context.Background(), token, "/mcp/v/vs_A"); err != nil || p == nil || p.Kind != mcp.PrincipalOAuth {
		t.Fatalf("Verify on vs_A = %+v, %v, want an OAuth principal", p, err)
	}
	if p, err := v.Verify(context.Background(), token, "/mcp/v/vs_B"); err == nil || p != nil {
		t.Fatalf("Verify on vs_B = %+v, %v, want refused", p, err)
	}
}

// A gateway with OAuth on and no usable public base or issuer refuses to
// start, through the MCP wiring, rather than advertise a wrong address.
func TestInitMCPOAuthRefusesUnusableConfig(t *testing.T) {
	for name, mutate := range map[string]func(*config.MCPConfig){
		"no public url":   func(m *config.MCPConfig) { m.PublicURL = "" },
		"no issuer":       func(m *config.MCPConfig) { m.OAuth.Issuer = "" },
		"relative issuer": func(m *config.MCPConfig) { m.OAuth.Issuer = "/agentcc/mcp-oauth" },
	} {
		cfg := oauthWiringConfig(mutate)
		err := runMCPWiring(&wiringDeps{cfg: cfg}, []mcpWiringStep{{"oauth", initMCPOAuth}})
		if err == nil || !strings.HasPrefix(err.Error(), "mcp oauth: ") {
			t.Fatalf("%s: wiring error %v, want the oauth step to refuse", name, err)
		}
	}
}
