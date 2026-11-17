package mcp

import (
	"testing"

	otel "github.com/futureagi/agentcc-gateway/internal/otel"
)

// This probe deliberately uses only interfaces present at the pinned base.
// A compile failure is not its red: tools/call must complete, then fail the
// tracing assertion on the base's empty startSpan hook.
type traceProbe struct {
	exporter         otel.SpanExporter
	starts, finishes int
}

func (p *traceProbe) Start(cc *CallContext) func() {
	p.starts++
	s := otel.NewSpan("execute_tool", "test")
	return func() {
		p.finishes++
		s.SetAttribute("settled", cc.Billing.Settled)
		s.End()
		_ = p.exporter.Export([]*otel.Span{s})
	}
}

type traceCapture struct{ spans []*otel.Span }

func (e *traceCapture) Export(spans []*otel.Span) error {
	e.spans = append(e.spans, spans...)
	return nil
}
func (*traceCapture) Shutdown() error { return nil }

func TestToolsCallInvokesTracer(t *testing.T) {
	h := newMeterHarness(t)
	h.onHook(attribute("none"))
	e := &traceCapture{}
	p := &traceProbe{exporter: e}
	d := h.s.Deps()
	d.Tracer = p
	h.s.SetDeps(*d)
	resp := h.call(t, "up_ok", nil)
	if resp.Error != nil || h.sink.only(t).Outcome != "success" {
		t.Fatal("tools/call did not succeed")
	}
	if p.starts != 1 || p.finishes != 1 || len(e.spans) != 1 {
		t.Fatalf("tracing did not run: starts=%d finishes=%d spans=%d", p.starts, p.finishes, len(e.spans))
	}
}
