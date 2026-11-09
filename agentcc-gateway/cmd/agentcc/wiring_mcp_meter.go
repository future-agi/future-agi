package main

import (
	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/meter"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// initMCPMeter is the MCP wiring step "meter" (mcpWiringOrder; owner M15).
// Once the server is built, and before initMCP hands the deps over, it
// points deps.Events at the log webhook's event sink, so every tool-call
// record travels in the webhook batch (main sets d.events to the flusher
// after the inits run, which is why this waits for the server), and installs
// the process meter: the key's log_content from the key store, and the org's
// redactor, else the global privacy one, for recorded content.
func initMCPMeter(d *wiringDeps, deps *mcp.Deps) error {
	global := globalRedactor(d.cfg)
	d.afterServer(func() error {
		deps.Events = d.events
		var orgs *tenant.Store
		if d.server != nil {
			orgs = d.server.TenantStore
		}
		meter.Install(meter.New(meter.Options{
			KeyLogContent: keyLogContent(d.keyStore),
			Redactor: func(orgID string) *privacy.Redactor {
				if r := orgs.Redactor(orgID); r != nil && r.ShouldRedact() {
					return r
				}
				return global
			},
		}))
		return nil
	})
	return nil
}

// keyLogContent reads a key's log_content metadata from the key store: by
// id when the principal has one, else by the raw key, read-only. Without a
// key store no key has one.
func keyLogContent(keys *auth.KeyStore) func(keyID, rawKey string) string {
	return func(keyID, rawKey string) string {
		if keys == nil {
			return ""
		}
		key := keys.Get(keyID)
		if key == nil && keyID == "" && rawKey != "" {
			key = keys.Lookup(rawKey)
		}
		if key == nil {
			return ""
		}
		return key.Metadata[privacy.KeyLogContentMetadata]
	}
}

// globalRedactor is the gateway-wide privacy redactor main builds for the
// request log, from the same config; nil when privacy is off.
func globalRedactor(cfg *config.Config) *privacy.Redactor {
	if cfg == nil || !cfg.Privacy.Enabled {
		return nil
	}
	patterns := make([]privacy.PatternConfig, len(cfg.Privacy.Patterns))
	for i, p := range cfg.Privacy.Patterns {
		patterns[i] = privacy.PatternConfig{Name: p.Name, Pattern: p.Pattern}
	}
	return privacy.New(cfg.Privacy.Mode, patterns)
}
