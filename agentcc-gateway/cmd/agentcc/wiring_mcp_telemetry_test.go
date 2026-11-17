package main

import (
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	otel "github.com/futureagi/agentcc-gateway/internal/otel"
	otelplugin "github.com/futureagi/agentcc-gateway/internal/plugins/otel"
	"testing"
)

type mcpSpanSink struct{ count int }

func (s *mcpSpanSink) Export(spans []*otel.Span) error { s.count += len(spans); return nil }
func (*mcpSpanSink) Shutdown() error                   { return nil }
func TestMCPTelemetryWiresExistingEmitterOnlyWhenEnabled(t *testing.T) {
	for _, enabled := range []bool{false, true} {
		d := &wiringDeps{cfg: &config.Config{OTel: config.OTelConfig{Enabled: enabled}}}
		deps := &mcp.Deps{}
		if err := initMCPTelemetry(d, deps); err != nil {
			t.Fatal(err)
		}
		sink := &mcpSpanSink{}
		// main creates the plugin after the init phase and before afterServer.
		d.otelPlugin = otelplugin.NewWithExporter(sink, 1, enabled)
		for _, p := range d.pending {
			if err := p.fn(); err != nil {
				t.Fatal(err)
			}
		}
		if !enabled {
			if deps.Tracer != nil || deps.DecorateUpstream != nil {
				t.Fatal("disabled telemetry wired")
			}
			continue
		}
		if deps.Tracer == nil || deps.DecorateUpstream == nil {
			t.Fatal("missing tracing seams")
		}
		finish := deps.Tracer.Start(&mcp.CallContext{Method: mcp.MethodToolsCall})
		finish()
		if sink.count != 1 {
			t.Fatal("not using the existing plugin exporter")
		}
	}
}
