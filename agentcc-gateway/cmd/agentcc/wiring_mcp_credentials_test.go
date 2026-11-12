package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credentials"
	"github.com/futureagi/agentcc-gateway/internal/mcp/tenancy"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestMCPCredentialsSharedStaticLifetime(t *testing.T) {
	for _, useRedis := range []bool{false, true} {
		for _, mode := range []string{"oauth-off-no-channel", "oauth-off-with-channel", "oauth-on-no-channel"} {
			t.Run(fmt.Sprintf("%s/redis=%t", mode, useRedis), func(t *testing.T) {
				ctx := context.Background()
				org := fmt.Sprintf("org-static-%d", time.Now().UnixNano())
				var client *redisstate.Client
				if useRedis {
					client = r7AgentTestRedis(t, "mtok:{"+org+"}:")
				}
				cfg := config.DefaultConfig()
				cfg.MCP.Enabled = true
				cfg.MCP.UpstreamOAuthEnabled = mode == "oauth-on-no-channel"
				if mode == "oauth-off-with-channel" {
					cfg.MCP.Django = config.MCPDjangoConfig{
						BaseURL: "https://backend.example.test", InternalTokenSecret: "synthetic-static-wiring-secret-for-test",
					}
				}
				// Static grants must not require OAuth state keys or a token channel.
				cfg.MCP.StateKeys = nil
				cfg.MCP.DevInsecureState = false
				install := func() (*mcp.Deps, mcp.PushedCredentials, mcp.PushedGrantRevoker) {
					t.Helper()
					deps := &mcp.Deps{}
					if err := initMCPCredentials(&wiringDeps{cfg: cfg, redis: client}, deps); err != nil {
						t.Fatal(err)
					}
					// The pool admits static grants through SharedCredentials, but
					// the purge route revokes them through CredentialResolver.
					pushed, ok := deps.SharedCredentials.(mcp.PushedCredentials)
					if !ok {
						t.Fatalf("shared credentials %T cannot admit pushed grants", deps.SharedCredentials)
					}
					revoker, ok := deps.CredentialResolver.(mcp.PushedGrantRevoker)
					if !ok {
						t.Fatalf("resolver %T cannot revoke pushed grants", deps.CredentialResolver)
					}
					if _, ok := deps.CredentialResolver.(mcp.CredentialPurger); !ok {
						t.Fatal("resolver lost revision-less purging")
					}
					if headers, err := deps.SharedCredentials.SharedAuth(org, "issues").Header(ctx); !errors.Is(err, mcp.ErrSharedCredentialsOff) || len(headers) != 0 {
						t.Fatalf("shared OAuth became available: %v", err)
					}
					return deps, pushed, revoker
				}
				_, pushed, revoker := install()
				defer revoker.RevokePushedGrants(ctx, org, "issues", 3)
				old, err := pushed.PushedCredential(ctx, org, "issues", mcp.PushedGrant{Identity: "synthetic-old", Revision: 1})
				if err != nil || old == nil || old.Check(ctx) != nil {
					t.Fatalf("initial static grant refused: %v", err)
				}
				if err := revoker.RevokePushedGrants(ctx, org, "issues", 2); err != nil {
					t.Fatal(err)
				}
				if old.Check(ctx) == nil || old.Lifetime.Err() == nil {
					t.Fatal("revoked grant remained usable")
				}
				if err := revoker.RevokePushedGrants(ctx, org, "issues", 1); err != nil {
					t.Fatal(err)
				}
				candidates := []mcp.PushedCredentials{pushed}
				if useRedis {
					_, cold, coldRevoker := install()
					defer coldRevoker.RevokePushedGrants(ctx, org, "issues", 3)
					candidates = append(candidates, cold)
				}
				for _, candidate := range candidates {
					for _, revision := range []int64{1, 2} {
						grant := mcp.PushedGrant{Identity: "synthetic-old", Revision: revision}
						if stale, err := candidate.PushedCredential(ctx, org, "issues", grant); err == nil || stale != nil {
							t.Fatalf("revoked revision %d was admitted again", revision)
						}
					}
					fresh, err := candidate.PushedCredential(ctx, org, "issues", mcp.PushedGrant{Identity: "synthetic-new", Revision: 3})
					if err != nil || fresh == nil || fresh.Check(ctx) != nil {
						t.Fatalf("successor refused: %v", err)
					}
					if err := revoker.RevokePushedGrants(ctx, org, "issues", 1); err != nil {
						t.Fatal(err)
					}
					if fresh.Check(ctx) != nil || fresh.Lifetime.Err() != nil {
						t.Fatal("delayed predecessor revocation cancelled the successor")
					}
				}
			})
		}
	}
}

func TestMCPCredentialsStaticResolverLeavesPerUserOff(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.MCP.Enabled = true
	cfg.MCP.UpstreamOAuthEnabled = false
	deps := &mcp.Deps{}
	if err := initMCPCredentials(&wiringDeps{cfg: cfg}, deps); err != nil {
		t.Fatal(err)
	}
	if deps.CredentialResolver == nil {
		t.Fatal("no static credential resolver installed")
	}
	auth, denial := deps.CredentialResolver.Resolve(&mcp.CallContext{Principal: mcp.Principal{OrgID: "org-static", UserID: "u1"}})
	if auth != nil || denial != nil {
		t.Fatal("static resolution must leave the per-user credential unavailable")
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
	defer manager.Close(context.Background())
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
		"mcp off": base(func(c *config.Config) { c.MCP.Enabled = false; c.MCP.Django.BaseURL = "http://django.internal:8000" }),
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
