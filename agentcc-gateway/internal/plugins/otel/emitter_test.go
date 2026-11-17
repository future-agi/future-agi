package otel

import (
	otelpkg "github.com/futureagi/agentcc-gateway/internal/otel"
	"testing"
)

type emitterCapture struct{ batches, closed int }

func (e *emitterCapture) Export([]*otelpkg.Span) error { e.batches++; return nil }
func (e *emitterCapture) Shutdown() error              { e.closed++; return nil }

func TestMCPEmitterSharesExporterAndShutdownOwner(t *testing.T) {
	e := &emitterCapture{}
	p := NewWithExporter(e, 1, true)
	m := p.MCPEmitter()
	if m == nil {
		t.Fatal("no emitter")
	}
	if !m.Sample("request") {
		t.Fatal("sampling config not shared")
	}
	if err := m.Export([]*otelpkg.Span{otelpkg.NewSpan("execute_tool", "test")}); err != nil {
		t.Fatal(err)
	}
	if e.batches != 1 || e.closed != 0 {
		t.Fatal("emitter has separate lifecycle")
	}
	p.Close()
	if e.closed != 1 {
		t.Fatal("plugin not sole shutdown owner")
	}
	if NewWithExporter(e, 1, false).MCPEmitter() != nil {
		t.Fatal("disabled exporter activated MCP")
	}
}
