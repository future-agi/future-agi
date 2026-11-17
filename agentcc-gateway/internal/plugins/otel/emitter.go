package otel

import "github.com/futureagi/agentcc-gateway/internal/mcp/telemetry"

// MCPEmitter borrows REL's exporter, sampling and capture switches. There is
// no second queue, ticker or shutdown owner. MCP applies its own existing
// per-org meter privacy policy before calling Export.
func (p *Plugin) MCPEmitter() *telemetry.Options {
	if p == nil || !p.enabled || p.exporter == nil {
		return nil
	}
	return &telemetry.Options{Export: p.exporter.Export, Sample: p.shouldSample, ServiceName: p.serviceName, IncludeBodies: p.includeBodies, Propagate: p.tracePropagation}
}
