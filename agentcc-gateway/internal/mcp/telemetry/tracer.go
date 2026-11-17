package telemetry

import (
	"log/slog"
	"sync"
	"time"

	otel "github.com/futureagi/agentcc-gateway/internal/otel"
)

// Options borrows the existing REL plugin's exporter and sampling decision.
// Content capture needs both IncludeBodies and the call's resolved full mode.
type Options struct {
	Export        func([]*otel.Span) error
	Sample        func(string) bool
	ServiceName   string
	IncludeBodies bool
	Propagate     bool
}

// Trace belongs to one request goroutine. Finish transfers immutable spans
// to the exporter; nothing in the call context is referenced after that.
type Trace struct {
	opts           Options
	server, client *otel.Span
	once           sync.Once
}

func Start(opts Options, parent string, started time.Time) *Trace {
	opts.ServiceName = SanitizeText(opts.ServiceName, nil)
	s := otel.NewSpan("execute_tool", opts.ServiceName)
	s.Kind = otel.KindServer
	if !started.IsZero() {
		s.StartTime = started
	}
	if opts.Propagate && validParent(parent) {
		s.TraceID = parent[3:35]
		s.ParentID = parent[36:52]
	}
	return &Trace{opts: opts, server: s}
}

// Upstream is called only when the gateway is about to attempt tools/call,
// never for authorization denials or gateway-only metatools.
func (t *Trace) Upstream() string {
	if t.client == nil {
		t.client = otel.NewChildSpan("execute_tool", t.server.TraceID, t.server.SpanID)
		t.client.ServiceName = t.opts.ServiceName
		t.client.Kind = otel.KindClient
	}
	if !t.opts.Propagate {
		return ""
	}
	return "00-" + t.client.TraceID + "-" + t.client.SpanID + "-01"
}

func (t *Trace) Finish(attrs map[string]any, failed bool, upstreamStarted, upstreamEnded time.Time) {
	t.once.Do(func() {
		// Exporter errors or panics must not change the tool result. In particular,
		// never print an exporter error: it may contain a collector URL or payload.
		defer func() {
			if recover() != nil {
				slog.Warn("mcp trace export failed")
			}
		}()
		t.server.End()
		spans := []*otel.Span{}
		if t.client != nil {
			if !upstreamStarted.IsZero() {
				t.client.StartTime = upstreamStarted
			}
			if upstreamEnded.IsZero() {
				upstreamEnded = t.server.EndTime
			}
			t.client.EndTime = upstreamEnded
			t.client.Duration = max(upstreamEnded.Sub(t.client.StartTime), 0)
			spans = append(spans, t.client)
		}
		spans = append(spans, t.server)
		for _, s := range spans {
			for k, v := range attrs {
				// Only these two already-processed bodies are content. Every other
				// string, including method/outcome/error dimensions, is shape-bound.
				if text, ok := v.(string); ok && k != "gen_ai.tool.call.arguments" && k != "gen_ai.tool.call.result" {
					v = SanitizeText(text, nil)
				}
				s.SetAttribute(k, v)
			}
			if failed {
				s.Status = otel.StatusError.String()
			}
		}
		if t.opts.Export != nil {
			if err := t.opts.Export(spans); err != nil {
				slog.Warn("mcp trace export failed")
			}
		}
	})
}
