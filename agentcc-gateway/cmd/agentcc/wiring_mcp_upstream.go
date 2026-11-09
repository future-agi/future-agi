package main

import (
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/upstream"
)

// initMCPUpstream is the MCP wiring step "upstream" (mcpWiringOrder), owner
// M13. It installs the upstream client factory as deps.ClientFactory. The
// factory reads its dialer from the server's deps when it builds a client,
// never here: M17's SSRF guard is set by the later "probe" step, and the
// pools build only after SetDeps. The static YAML clients get their call
// settings (timeout_ms, forward_headers, mcp.max_result_bytes) and era when
// they are built (mcp.StaticPoolClientConfig, M13 IR-1), so nothing is applied
// after connect.
func initMCPUpstream(d *wiringDeps, deps *mcp.Deps) error {
	var maxResult int64
	if d.cfg != nil {
		maxResult = d.cfg.MCP.MaxResultBytes
	}
	factory := upstream.NewFactory(upstream.Options{MaxResultBytes: maxResult})
	deps.ClientFactory = factory.Build
	d.afterServer(func() error {
		if d.server == nil {
			return nil
		}
		srv := d.server.MCP()
		if srv == nil {
			return nil
		}
		factory.SetDialerSource(func() mcp.Dialer { return srv.Deps().Dialer })
		return nil
	})
	return nil
}
