package main

import "github.com/futureagi/agentcc-gateway/internal/mcp"

// main builds REL's plugin after the init phase. Borrow it just before the
// MCP dependency snapshot is installed; no extra exporter or lifecycle.
func initMCPTelemetry(d *wiringDeps, deps *mcp.Deps) error {
	if d.cfg == nil || !d.cfg.OTel.Enabled {
		return nil
	}
	d.afterServer(func() error {
		opts := d.otelPlugin.MCPEmitter()
		if opts == nil {
			return nil
		}
		deps.Tracer = mcp.NewToolTracer(*opts)
		previous := deps.DecorateUpstream
		deps.DecorateUpstream = func(cc *mcp.CallContext, msg *mcp.Message) {
			if previous != nil {
				previous(cc, msg)
			}
			mcp.DecorateToolTrace(cc, msg)
		}
		return nil
	})
	return nil
}
