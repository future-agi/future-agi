package main

import (
	"errors"
	"log/slog"

	"github.com/futureagi/agentcc-gateway/catalog/connectors"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credentials"
	"github.com/futureagi/agentcc-gateway/internal/mcp/vendors"
)

// initMCPVendors is the MCP wiring step "vendors" (mcpWiringOrder). Off by
// default: with mcp.vendors.k2_runtime_url empty it sets nothing. Set, the K2
// hosted runtime wiring (G14b, vendors.Hosted) wraps M25's per-user resolver
// once every MCP init has run (the "credentials" step comes later in the
// order): a server added from FutureAGI's own Gmail, Google Calendar, Outlook
// or Teams provider gets a person's token only at <runtime>/<app>/mcp, through
// the W4b admission checks. It needs the per-user channel (mcp.django); a
// runtime URL without it stops the gateway rather than serving nothing.
// Owner: V10 (W4b).
func initMCPVendors(d *wiringDeps, deps *mcp.Deps) error {
	if d.cfg == nil || !d.cfg.MCP.Enabled || !d.cfg.MCP.K2AppsEnabled || !d.cfg.MCP.UpstreamOAuthEnabled || d.cfg.MCP.Vendors.K2RuntimeURL == "" {
		return nil
	}
	runtime := d.cfg.MCP.Vendors.K2RuntimeURL
	cat, err := connectors.Load(d.cfg.Connectors.CatalogDir)
	if err != nil {
		return err
	}
	d.afterServer(func() error {
		resolver, ok := deps.CredentialResolver.(*credentials.Resolver)
		if !ok {
			return errors.New("mcp.vendors.k2_runtime_url is set without the per-user credential channel (mcp.django.base_url)")
		}
		if d.server == nil || d.server.TenantStore == nil {
			return errors.New("mcp.vendors.k2_runtime_url: the gateway has no org config store")
		}
		hosted, err := vendors.NewHosted(vendors.HostedOptions{Resolver: resolver, Catalog: cat.Records,
			Configs: d.server.TenantStore, RuntimeURL: runtime})
		if err != nil {
			return err
		}
		deps.CredentialResolver = hosted
		slog.Info("mcp K2 hosted runtime wiring is on", "catalog", cat.Source)
		return nil
	})
	return nil
}
