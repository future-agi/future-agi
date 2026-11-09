package main

import (
	"context"
	"log/slog"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/tenancy"
)

// initMCPTenancy is the MCP wiring step "tenancy" (mcpWiringOrder, M9). Once
// the server exists it installs a tenancy.Manager on the MCP server: one pool
// per org from the pushed configs in the server's tenant store, and the
// static YAML pool, which adopts the clients the route setup connected. The
// manager reads the server's dependencies each time it builds a client, so
// the factory (M13) and dialer (M17) installed by later steps, and handed
// over by SetDeps after this callback, are the ones used. It sets no Deps
// member.
func initMCPTenancy(d *wiringDeps, _ *mcp.Deps) error {
	d.afterServer(func() error {
		if d.server == nil || d.server.MCP() == nil {
			return nil
		}
		srv := d.server.MCP()
		var opts tenancy.Options
		if d.cfg != nil {
			opts.MCP = d.cfg.MCP
			opts.Redactor = mcp.OperatorRedactor(d.cfg.Privacy)
		}
		// The connector catalog a catalog_ref server's credential is placed
		// by (IR-CN7-3); nil only when the connectors step did not run.
		opts.Catalog = d.connectorCatalog
		opts.Store = d.server.TenantStore
		opts.Deps = srv.Deps
		opts.Adopt = srv.StaticClients
		opts.Publish = srv.SetStaticClient
		mgr := tenancy.New(opts)
		srv.SetTenancy(mgr)
		// The org pools are the virtual servers' member source (M10 IR-4, at
		// the W3b merge): an org's virtual servers compose its own pool's
		// lists, the static org's view included; an org whose policy cannot
		// be evaluated has none (the registry's fail-closed default).
		sep := srv.Registry().Separator()
		srv.Registry().SetOrgSource(func(orgID string) mcp.MemberSource {
			_, pool, err := mgr.Resolve(orgID)
			if err != nil {
				return nil
			}
			return mcp.PoolMembers(pool, sep)
		})
		if d.ctx != nil {
			go closeTenancyOnShutdown(d.ctx, mgr, shutdownBound(d.cfg))
		}
		return nil
	})
	return nil
}

// closeTenancyOnShutdown closes every pool's clients once shutdown begins
// (ctx is cancelled first thing in the shutdown), bounded by limit. The
// static clients of today's kind are also the server's own, which the
// server's Shutdown closes and waits for; main does not wait for this.
func closeTenancyOnShutdown(ctx context.Context, mgr *tenancy.Manager, limit time.Duration) {
	<-ctx.Done()
	closeCtx, cancel := context.WithTimeout(context.Background(), limit)
	defer cancel()
	if err := mgr.Close(closeCtx); err != nil {
		slog.Warn("mcp org pools not all closed before the shutdown deadline", "error", err)
	}
}

// shutdownBound is server.shutdown_timeout, 30 s when unset.
func shutdownBound(cfg *config.Config) time.Duration {
	if cfg != nil && cfg.Server.ShutdownTimeout > 0 {
		return cfg.Server.ShutdownTimeout
	}
	return 30 * time.Second
}
