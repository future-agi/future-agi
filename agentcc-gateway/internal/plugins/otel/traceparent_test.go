package otel

import (
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	otelpkg "github.com/futureagi/agentcc-gateway/internal/otel"
)

const (
	validTrace  = "0af7651916cd43dd8448eb211c80319c"
	validParent = "b7ad6b7169203331"
)

func TestParseTraceparent(t *testing.T) {
	tests := []struct {
		name   string
		header string
		wantOK bool
		trace  string
		parent string
	}{
		{
			name:   "valid sampled",
			header: "00-" + validTrace + "-" + validParent + "-01",
			wantOK: true, trace: validTrace, parent: validParent,
		},
		{
			name:   "valid, other flag bits set alongside sampled",
			header: "00-" + validTrace + "-" + validParent + "-03",
			wantOK: true, trace: validTrace, parent: validParent,
		},
		{
			name:   "surrounding whitespace tolerated",
			header: "  00-" + validTrace + "-" + validParent + "-01  ",
			wantOK: true, trace: validTrace, parent: validParent,
		},

		// Not sampled: the caller will never export the span we would point at,
		// so parenting to it would dangle forever.
		{name: "not sampled", header: "00-" + validTrace + "-" + validParent + "-00"},

		// Version handling. Only 00 is defined; ff is explicitly invalid.
		{name: "future version", header: "01-" + validTrace + "-" + validParent + "-01"},
		{name: "version ff", header: "ff-" + validTrace + "-" + validParent + "-01"},

		// All-zero ids are invalid per spec.
		{name: "zero trace id", header: "00-" + "00000000000000000000000000000000" + "-" + validParent + "-01"},
		{name: "zero parent id", header: "00-" + validTrace + "-0000000000000000-01"},

		// Malformed shapes.
		{name: "empty", header: ""},
		{name: "too few fields", header: "00-" + validTrace + "-" + validParent},
		{name: "too many fields", header: "00-" + validTrace + "-" + validParent + "-01-extra"},
		{name: "trace id too short", header: "00-abc-" + validParent + "-01"},
		{name: "parent id too long", header: "00-" + validTrace + "-" + validParent + "ff-01"},
		{name: "non-hex trace id", header: "00-" + "ZZf7651916cd43dd8448eb211c80319c" + "-" + validParent + "-01"},
		{name: "uppercase hex rejected", header: "00-" + "0AF7651916CD43DD8448EB211C80319C" + "-" + validParent + "-01"},
		{name: "flags not hex", header: "00-" + validTrace + "-" + validParent + "-zz"},
		{name: "flags wrong width", header: "00-" + validTrace + "-" + validParent + "-1"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			trace, parent, ok := parseTraceparent(tt.header)
			if ok != tt.wantOK {
				t.Fatalf("ok = %v, want %v (header %q)", ok, tt.wantOK, tt.header)
			}
			if !tt.wantOK {
				return
			}
			if trace != tt.trace {
				t.Errorf("trace = %q, want %q", trace, tt.trace)
			}
			if parent != tt.parent {
				t.Errorf("parent = %q, want %q", parent, tt.parent)
			}
		})
	}
}

// newRC builds a request context carrying the given traceparent header.
func newRC(traceparent string) *models.RequestContext {
	rc := &models.RequestContext{
		RequestID:      "req-1",
		Model:          "gpt-4o",
		Metadata:       map[string]string{},
		RequestHeaders: http.Header{},
	}
	if traceparent != "" {
		rc.RequestHeaders.Set("traceparent", traceparent)
	}
	return rc
}

// spanFor runs ProcessRequest and returns the span the plugin stored.
func spanFor(t *testing.T, p *Plugin, rc *models.RequestContext) *otelpkg.Span {
	t.Helper()
	p.ProcessRequest(nil, rc)
	v, ok := p.spans.Load(rc.RequestID)
	if !ok {
		t.Fatal("no span was stored for the request")
	}
	return v.(*otelpkg.Span)
}

func TestProcessRequest_TracePropagationDisabled(t *testing.T) {
	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)
	p.tracePropagation = false

	rc := newRC("00-" + validTrace + "-" + validParent + "-01")
	span := spanFor(t, p, rc)

	if span.ParentID != "" {
		t.Errorf("ParentID = %q, want empty when propagation is off", span.ParentID)
	}
	if span.TraceID == validTrace {
		t.Error("trace id was adopted from traceparent while propagation is off")
	}
}

func TestProcessRequest_TracePropagationEnabled(t *testing.T) {
	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)
	p.tracePropagation = true

	rc := newRC("00-" + validTrace + "-" + validParent + "-01")
	span := spanFor(t, p, rc)

	if span.TraceID != validTrace {
		t.Errorf("TraceID = %q, want %q", span.TraceID, validTrace)
	}
	if span.ParentID != validParent {
		t.Errorf("ParentID = %q, want %q", span.ParentID, validParent)
	}
}

// An unsampled caller must not become a parent — see parseTraceparent.
func TestProcessRequest_UnsampledCallerIsNotParented(t *testing.T) {
	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)
	p.tracePropagation = true

	rc := newRC("00-" + validTrace + "-" + validParent + "-00")
	span := spanFor(t, p, rc)

	if span.ParentID != "" {
		t.Errorf("ParentID = %q, want empty for an unsampled caller", span.ParentID)
	}
}

func TestProcessRequest_MalformedHeaderLeavesSpanAsRoot(t *testing.T) {
	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)
	p.tracePropagation = true

	rc := newRC("not-a-traceparent")
	span := spanFor(t, p, rc)

	if span.ParentID != "" {
		t.Errorf("ParentID = %q, want empty for a malformed header", span.ParentID)
	}
	if span.TraceID == "" {
		t.Error("span lost its generated trace id on a malformed header")
	}
}

// x-agentcc-trace-id still sets the trace id; traceparent wins when both are
// present, because only it can also supply a parent.
func TestProcessRequest_TraceparentOverridesRequestTraceID(t *testing.T) {
	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)
	p.tracePropagation = true

	rc := newRC("00-" + validTrace + "-" + validParent + "-01")
	rc.TraceID = "11111111111111111111111111111111"

	span := spanFor(t, p, rc)

	if span.TraceID != validTrace {
		t.Errorf("TraceID = %q, want traceparent's %q", span.TraceID, validTrace)
	}
	if span.ParentID != validParent {
		t.Errorf("ParentID = %q, want %q", span.ParentID, validParent)
	}
}

// No traceparent at all: the existing x-agentcc-trace-id behaviour is untouched.
func TestProcessRequest_NoTraceparentKeepsRequestTraceID(t *testing.T) {
	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)
	p.tracePropagation = true

	rc := newRC("")
	rc.TraceID = "11111111111111111111111111111111"

	span := spanFor(t, p, rc)

	if span.TraceID != rc.TraceID {
		t.Errorf("TraceID = %q, want %q", span.TraceID, rc.TraceID)
	}
	if span.ParentID != "" {
		t.Errorf("ParentID = %q, want empty", span.ParentID)
	}
}

// A nil header map must not panic.
func TestProcessRequest_NilRequestHeaders(t *testing.T) {
	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)
	p.tracePropagation = true

	rc := newRC("")
	rc.RequestHeaders = nil

	span := spanFor(t, p, rc)
	if span.ParentID != "" {
		t.Errorf("ParentID = %q, want empty", span.ParentID)
	}
}

// With propagation on, span.TraceID becomes the caller's id — so the gateway's
// own id must be pinned as an attribute, or the documented span-to-log-row join
// silently breaks.
func TestProcessRequest_PreservesGatewayTraceIDForLogJoin(t *testing.T) {
	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)
	p.tracePropagation = true

	rc := newRC("00-" + validTrace + "-" + validParent + "-01")
	rc.TraceID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"

	span := spanFor(t, p, rc)

	if span.TraceID != validTrace {
		t.Errorf("TraceID = %q, want the caller's %q", span.TraceID, validTrace)
	}
	got, ok := span.Attributes["agentcc.trace_id"]
	if !ok {
		t.Fatal("agentcc.trace_id attribute missing; the log join is broken")
	}
	if got != rc.TraceID {
		t.Errorf("agentcc.trace_id = %v, want the gateway id %q", got, rc.TraceID)
	}
}

// Without propagation the exporter supplies the attribute itself, so the plugin
// must not set it — two sources would emit a duplicate OTLP key.
func TestProcessRequest_NoDuplicateTraceIDAttributeWhenDisabled(t *testing.T) {
	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)
	p.tracePropagation = false

	rc := newRC("00-" + validTrace + "-" + validParent + "-01")
	rc.TraceID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"

	span := spanFor(t, p, rc)

	if _, ok := span.Attributes["agentcc.trace_id"]; ok {
		t.Error("plugin set agentcc.trace_id while propagation is off; the exporter already does")
	}
}
