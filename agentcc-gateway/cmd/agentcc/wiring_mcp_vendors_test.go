package main

import (
	"fmt"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/server"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// k2ChannelSecret is a synthetic per-user channel secret (32+ bytes).
const k2ChannelSecret = "k2-wiring-synthetic-channel-secret-not-real"

// k2Wired runs the whole MCP wiring over cfg's changes and the server
// callbacks, returning the MCP credential step's type or the wiring error.
func k2Wired(t *testing.T, change func(*config.Config)) (string, error) {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.MCP.Enabled = true
	cfg.MCP.K2AppsEnabled = true
	cfg.MCP.UpstreamOAuthEnabled = true
	cfg.MCP.StateKeys = []string{mcpTestStateKey}
	change(cfg)
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	var mdb atomic.Pointer[modeldb.ModelDB]
	mdb.Store(modeldb.New(modeldb.BundledModels, nil))
	srv := server.New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, &mdb, tenant.NewStore(), nil)
	t.Cleanup(func() { srv.MCP().Close() })
	d := &wiringDeps{cfg: cfg, server: srv}
	if err := runMCPWiring(d, mcpWiringOrder); err != nil {
		return "", err
	}
	if err := d.startServerWiring(); err != nil {
		return "", err
	}
	return fmt.Sprintf("%T", srv.MCP().Deps().CredentialResolver), nil
}

func k2Channel(cfg *config.Config) {
	cfg.MCP.Django.BaseURL = "http://127.0.0.1:9"
	cfg.MCP.Django.InternalTokenSecret = k2ChannelSecret
}

// TestK2VendorsWiring: K2 is guarded even without a runtime. Naming the
// runtime installs hosted admission over the vendors client; without the credential channel
// a named runtime still stops the gateway rather than silently degrading.
func TestK2VendorsWiring(t *testing.T) {
	t.Run("no runtime with channel: K2 guard wraps the vendors client", func(t *testing.T) {
		got, err := k2Wired(t, k2Channel)
		if err != nil {
			t.Fatal(err)
		}
		if got != "*vendors.UnavailableRuntime" {
			t.Fatalf("credential step = %s, want *vendors.UnavailableRuntime", got)
		}
	})
	t.Run("no runtime without channel: K2 guard remains installed", func(t *testing.T) {
		got, err := k2Wired(t, func(*config.Config) {})
		if err != nil {
			t.Fatal(err)
		}
		if got != "*vendors.UnavailableRuntime" {
			t.Fatalf("credential step = %s, want *vendors.UnavailableRuntime", got)
		}
	})
	t.Run("runtime named with the channel: the hosted wiring", func(t *testing.T) {
		got, err := k2Wired(t, func(c *config.Config) {
			k2Channel(c)
			c.MCP.Vendors.K2RuntimeURL = "http://127.0.0.1:9"
		})
		if err != nil {
			t.Fatal(err)
		}
		if got != "*vendors.Hosted" {
			t.Fatalf("credential step = %s, want *vendors.Hosted", got)
		}
	})
	t.Run("runtime named without the channel: refused", func(t *testing.T) {
		_, err := k2Wired(t, func(c *config.Config) { c.MCP.Vendors.K2RuntimeURL = "http://127.0.0.1:9" })
		if err == nil || !strings.Contains(err.Error(), "mcp.vendors.k2_runtime_url") {
			t.Fatalf("wiring error = %v, want a refusal naming mcp.vendors.k2_runtime_url", err)
		}
	})
	t.Run("a runtime that is not an absolute URL fails validation", func(t *testing.T) {
		cfg := config.DefaultConfig()
		cfg.MCP.Vendors.K2RuntimeURL = "agentcc-connectors:8090"
		if err := cfg.Validate(); err == nil || !strings.Contains(err.Error(), "mcp.vendors.k2_runtime_url") {
			t.Fatalf("Validate = %v", err)
		}
	})
}
