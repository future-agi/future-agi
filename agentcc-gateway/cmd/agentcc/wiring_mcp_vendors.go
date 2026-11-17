package main

import (
	"errors"
	"log/slog"

	"github.com/futureagi/agentcc-gateway/catalog/connectors"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/vendors"
)

// initMCPVendors installs the one K2 admission policy. Tenancy evaluates it at
// entry construction, at every credential lookup and before every send;
// compatibility credential adapters only ask the pool's gate, which evaluates
// it again. Shared startup and personal token lookup are downstream of it.
func initMCPVendors(d *wiringDeps, deps *mcp.Deps) error {
	if d.cfg == nil || !d.cfg.MCP.Enabled {
		return nil
	}
	runtime := d.cfg.MCP.Vendors.K2RuntimeURL
	if !d.cfg.MCP.K2AppsEnabled || !d.cfg.MCP.UpstreamOAuthEnabled {
		runtime = ""
	}
	// One policy for both shared and per-user entries, read from the embedded
	// catalog as it is at each evaluation.
	cat, err := connectors.Embedded()
	if err != nil {
		return err
	}
	admission, err := vendors.NewClientAdmission(cat.Records, runtime)
	if err != nil {
		return err
	}
	deps.ClientAdmission = admission
	if runtime == "" {
		d.afterServer(func() error {
			// Never leave the plain resolver as a K2 fallback, including when
			// no per-user channel was installed. Do not broaden this refusal
			// to the legacy visibility list: non-K2 servers are unchanged.
			deps.CredentialResolver = vendors.NewUnavailableRuntime(deps.CredentialResolver)
			return nil
		})
		return nil
	}
	// K2 is served by this build's embedded bundle, not an operator-directory
	// override that may describe a different runtime's providers or sources.
	d.afterServer(func() error {
		// The credentials step always installs a resolver when MCP is on (the
		// pushed one for shared static grants, initMCPCredentials), so a
		// missing per-user channel shows as an unset mcp.django.base_url,
		// not as a nil resolver.
		resolver := deps.CredentialResolver
		if resolver == nil || d.cfg.MCP.Django.BaseURL == "" {
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
