package mcp

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp/meter"
	"github.com/futureagi/agentcc-gateway/internal/mcp/telemetry"
	otel "github.com/futureagi/agentcc-gateway/internal/otel"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

func tracedHarness(t *testing.T, exp otel.SpanExporter) *meterHarness {
	t.Helper()
	h := newMeterHarness(t)
	h.onHook(attribute("none"))
	d := h.s.Deps()
	d.Tracer = NewToolTracer(telemetry.Options{Export: exp.Export, ServiceName: "test", IncludeBodies: true, Propagate: true})
	d.DecorateUpstream = DecorateToolTrace
	h.s.SetDeps(*d)
	return h
}

func spanPair(t *testing.T, e *traceCapture) (*otel.Span, *otel.Span) {
	t.Helper()
	if len(e.spans) != 2 {
		t.Fatalf("spans=%d, want SERVER and CLIENT", len(e.spans))
	}
	var server, client *otel.Span
	for _, s := range e.spans {
		if s.Kind == otel.KindServer {
			server = s
		}
		if s.Kind == otel.KindClient {
			client = s
		}
	}
	if server == nil || client == nil {
		t.Fatal("missing span kinds")
	}
	if server.TraceID != client.TraceID || client.ParentID != server.SpanID {
		t.Fatal("broken span ancestry")
	}
	if server.EndTime.Before(client.EndTime) || server.StartTime.After(client.StartTime) || client.Duration < 0 {
		t.Fatal("invalid final timings")
	}
	return server, client
}

func TestServerAndClientSpans(t *testing.T) {
	for _, tc := range []struct{ tool, outcome string }{{"up_ok", "success"}, {"up_fail", "is_error"}, {"up_broken", "error"}, {"up_slow", "timeout"}} {
		t.Run(tc.outcome, func(t *testing.T) {
			e := &traceCapture{}
			h := tracedHarness(t, e)
			h.call(t, tc.tool, nil)
			s, c := spanPair(t, e)
			if s.Attributes["mcp.outcome"] != tc.outcome || c.Attributes["mcp.outcome"] != tc.outcome {
				t.Fatal("wrong final outcome")
			}
			if h.sink.only(t).Outcome != tc.outcome {
				t.Fatal("record changed")
			}
			if tc.outcome != "success" && s.Status != "ERROR" {
				t.Fatal("missing error status")
			}
		})
	}
}

func TestSpanAttributesGenAI(t *testing.T) {
	e := &traceCapture{}
	h := tracedHarness(t, e)
	h.call(t, "up_ok", nil)
	s, c := spanPair(t, e)
	for _, sp := range []*otel.Span{s, c} {
		for k, v := range map[string]any{"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "ok", "agentcc.mcp.server_id": "up", "agentcc.billing_mode": "byok", "mcp.billed_cost_micros": int64(0), "agent.id": "researcher", "agent.run_id": "run_R1"} {
			if sp.Attributes[k] != v {
				t.Errorf("attribute %s differs", k)
			}
		}
		if sp.Attributes["gen_ai.tool.call.id"] != h.sink.only(t).ToolCallID {
			t.Fatal("missing recorded call identity")
		}
		if sp.Attributes["mcp.session.id"] == h.session || sp.Attributes["mcp.session.id"] == "" {
			t.Fatal("session not hashed")
		}
	}
}

func TestTraceDenialDoesNotInventClientSpan(t *testing.T) {
	e := &traceCapture{}
	h := tracedHarness(t, e)
	h.call(t, "up_fail", nil, "Authorization: Bearer k-deny")
	if len(e.spans) != 1 || e.spans[0].Kind != otel.KindServer || e.spans[0].Attributes["mcp.outcome"] != "denied" {
		t.Fatal("denial invented an upstream span")
	}
}

func TestTraceFinalizesExactlyOnce(t *testing.T) {
	for _, mode := range []string{"cancelled", "panic", "panic-after-attempt"} {
		t.Run(mode, func(t *testing.T) {
			e := &traceCapture{}
			h := tracedHarness(t, e)
			if mode == "panic-after-attempt" {
				d := *h.s.Deps()
				d.DecorateUpstream = func(cc *CallContext, msg *Message) {
					DecorateToolTrace(cc, msg)
					panic("synthetic attempt panic")
				}
				h.s.SetDeps(d)
			}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			h.onHook(func(hook string, cc *CallContext) {
				attribute("none")(hook, cc)
				if hook == hookCallUpstream {
					if mode == "panic" {
						panic("synthetic panic")
					}
					if mode == "cancelled" {
						cancel()
					}
				}
			})
			func() { defer func() { _ = recover() }(); h.post(t, ctx, "up_ok", nil) }()
			want := 2
			if mode == "panic" {
				want = 1
			}
			if len(e.spans) != want {
				t.Fatalf("finalized %d spans, want %d", len(e.spans), want)
			}
			if want == 2 {
				spanPair(t, e)
			}
			s := e.spans[len(e.spans)-1]
			if s.EndTime.IsZero() || s.Status != "ERROR" {
				t.Fatal("unfinished span")
			}
			if mode == "cancelled" && s.Attributes["mcp.outcome"] != "cancelled" {
				t.Fatal("wrong cancellation")
			}
			if len(h.sink.records(t)) != 1 {
				t.Fatal("record count changed")
			}
		})
	}
	// The tracer's own finish is idempotent even if a caller invokes it twice.
	e := &traceCapture{}
	tr := NewToolTracer(telemetry.Options{Export: e.Export})
	finish := tr.Start(&CallContext{Method: MethodToolsCall})
	finish()
	finish()
	if len(e.spans) != 1 {
		t.Fatal("finish emitted twice")
	}
}

type failedTraceExporter struct{ calls int }

func (e *failedTraceExporter) Export([]*otel.Span) error {
	e.calls++
	return errors.New("synthetic collector unavailable")
}
func (*failedTraceExporter) Shutdown() error { return nil }
func TestTraceExporterFailurePreservesToolResult(t *testing.T) {
	e := &failedTraceExporter{}
	h := tracedHarness(t, e)
	resp := h.call(t, "up_ok", nil)
	if resp.Error != nil || h.sink.only(t).Outcome != "success" || e.calls != 1 {
		t.Fatal("export failure changed call or multiplied exports")
	}
}

func TestContentModeOnSpans(t *testing.T) {
	for _, tc := range []struct {
		mode      string
		retention TermsRetention
		bodies    bool
		want      bool
	}{
		{"", "", true, false}, {"none", "", true, false}, {"metadata", "", true, false}, {"full", "", false, false},
		{"full", RetentionMetadataOnly, true, false}, {"full", RetentionHashed, true, false}, {"full", "", true, true},
	} {
		t.Run(tc.mode+"/"+string(tc.retention)+"/"+map[bool]string{true: "capture", false: "off"}[tc.bodies], func(t *testing.T) {
			e := &traceCapture{}
			h := tracedHarness(t, e)
			r := privacy.New(privacy.ModePatterns, []privacy.PatternConfig{{Name: "email", Pattern: `[a-z]+@[a-z]+\.example`}})
			t.Cleanup(meter.Install(meter.New(meter.Options{Redactor: func(string) *privacy.Redactor { return r }})))
			d := h.s.Deps()
			d.Tracer = NewToolTracer(telemetry.Options{Export: e.Export, IncludeBodies: tc.bodies})
			h.s.SetDeps(*d)
			h.onHook(func(hook string, cc *CallContext) {
				attribute(tc.mode)(hook, cc)
				if hook == hookEvidenceMeta {
					cc.Terms.Retention = tc.retention
					cc.Result = json.RawMessage(`{"content":[{"type":"text","text":"safe result bob@corp.example Authorization: Bearer sk-synthetic-result-value"}]}`)
				}
			})
			var logs bytes.Buffer
			old := slog.Default()
			slog.SetDefault(slog.New(slog.NewTextHandler(&logs, nil)))
			defer slog.SetDefault(old)
			h.call(t, "up_ok", map[string]any{"password": "synthetic-secret-arg", "email": "bob@corp.example", "safe": "safe argument", "padding": strings.Repeat("x", 20000)}, "Authorization: Bearer synthetic-auth-value")
			s, _ := spanPair(t, e)
			raw, _ := json.Marshal(e.spans)
			for _, canary := range []string{"synthetic-secret-arg", "bob@corp.example", "sk-synthetic-result-value", "synthetic-auth-value"} {
				if bytes.Contains(raw, []byte(canary)) || strings.Contains(logs.String(), canary) {
					t.Fatal("private canary retained")
				}
			}
			for _, key := range []string{"gen_ai.tool.call.arguments", "gen_ai.tool.call.result"} {
				value, ok := s.Attributes[key].(string)
				if ok != tc.want {
					t.Fatalf("content presence for %s=%v", key, ok)
				}
				if len(value) > meter.MaxContentBytes {
					t.Fatal("content exceeded byte limit")
				}
			}
		})
	}
}

func TestTraceUpstreamErrorIsNotExported(t *testing.T) {
	e := &traceCapture{}
	h := tracedHarness(t, e)
	var logs bytes.Buffer
	old := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&logs, nil)))
	defer slog.SetDefault(old)
	h.call(t, "up_pii", nil)
	raw, _ := json.Marshal(e.spans)
	for _, v := range []string{"bob@corp.example", "sk-fake-test-value", "upstream error"} {
		if bytes.Contains(raw, []byte(v)) || strings.Contains(logs.String(), v) {
			t.Fatal("error text exported or logged")
		}
	}
}
