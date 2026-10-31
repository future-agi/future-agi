package common

import (
	"context"
	"log/slog"
	"sync"
	"sync/atomic"
	"time"
)

// ShapeKind names the part of an upstream response an adapter did not
// understand. The set is closed on purpose: it is a metric label, and the
// upstream value that triggered a report never is.
type ShapeKind string

const (
	// ShapeUnknownEvent is a streamed event whose type the adapter does not
	// handle, such as an SSE event name added after the adapter was written.
	ShapeUnknownEvent ShapeKind = "unknown_event"
	// ShapeUnknownBlock is a content block, part or item type the adapter does
	// not translate, so its content never reaches the canonical response.
	ShapeUnknownBlock ShapeKind = "unknown_block"
	// ShapeUnknownDelta is a streamed delta type the adapter does not
	// translate, so the increment it carries is lost.
	ShapeUnknownDelta ShapeKind = "unknown_delta"
	// ShapeUnmappedFinishReason is a stop or finish reason with no canonical
	// mapping; the adapter fell back to its default.
	ShapeUnmappedFinishReason ShapeKind = "unmapped_finish_reason"
	// ShapeMalformed is a payload that did not decode into the type the
	// adapter expected, and was skipped.
	ShapeMalformed ShapeKind = "malformed_payload"
)

// UnknownShape is one report of a response shape an adapter did not
// understand.
type UnknownShape struct {
	// Provider is the adapter, e.g. "anthropic" — not the configured provider
	// ID, which per-org providers make unbounded.
	Provider string
	Kind     ShapeKind
	// Value is the upstream value that was not understood, e.g.
	// "redacted_thinking". It is logged and recorded, never used as a label.
	Value string
}

// maxShapeValueLen caps Value: it comes from the upstream body, and a
// malformed one should not turn a log line into a payload dump.
const maxShapeValueLen = 128

// ShapeReporter is how an adapter says it met a response shape it does not
// understand. Before this, adapters skipped such content silently: an unknown
// SSE event, a new content block type, or an unmapped stop reason vanished
// from the canonical response with nothing to show it had been there. A report
// does not fail the request. It logs a warning, feeds the metric hook, and is
// visible to a ShapeRecorder on the request context, which is how the contract
// tests prove a fixture translated without loss.
//
// Create one per upstream response, with NewShapeReporter. Each distinct
// (kind, value) is reported once per reporter, so a stream that repeats an
// unknown delta on every event counts once, not once per event. The zero of
// *ShapeReporter is nil, and Report on nil does nothing, so translation
// helpers can take one as a parameter and unit tests can pass nil.
type ShapeReporter struct {
	provider   string
	providerID string
	recorder   *ShapeRecorder

	mu   sync.Mutex
	seen map[shapeKey]struct{}
}

type shapeKey struct {
	kind  ShapeKind
	value string
}

// NewShapeReporter returns a reporter for one upstream response. provider is
// the adapter name and becomes the metric label; providerID is the configured
// provider and appears only in the log line.
func NewShapeReporter(ctx context.Context, provider, providerID string) *ShapeReporter {
	r := &ShapeReporter{provider: provider, providerID: providerID}
	if rec, ok := ctx.Value(shapeRecorderKey{}).(*ShapeRecorder); ok {
		r.recorder = rec
	}
	return r
}

// Report records that the response carried something the adapter did not
// translate. Safe to call on a nil reporter.
func (r *ShapeReporter) Report(kind ShapeKind, value string) {
	if r == nil {
		return
	}
	if len(value) > maxShapeValueLen {
		value = value[:maxShapeValueLen]
	}
	key := shapeKey{kind: kind, value: value}

	r.mu.Lock()
	if _, dup := r.seen[key]; dup {
		r.mu.Unlock()
		return
	}
	if r.seen == nil {
		r.seen = make(map[shapeKey]struct{})
	}
	r.seen[key] = struct{}{}
	r.mu.Unlock()

	s := UnknownShape{Provider: r.provider, Kind: kind, Value: value}
	if r.recorder != nil {
		r.recorder.add(s)
	}
	if hook := shapeHook.Load(); hook != nil {
		(*hook)(s)
	}
	logUnknownShape(s, r.providerID)
}

var shapeHook atomic.Pointer[func(UnknownShape)]

// SetUnknownShapeHook installs fn to run on every report, after per-response
// de-duplication. main wires it to a counter; fn must be cheap and safe for
// concurrent use. nil removes the hook.
func SetUnknownShapeHook(fn func(UnknownShape)) {
	if fn == nil {
		shapeHook.Store(nil)
		return
	}
	shapeHook.Store(&fn)
}

// A provider that starts sending a new block type sends it on every response,
// so logging each report would put a warning on every request. One line per
// distinct (adapter, kind, value) per interval says the same thing; the
// metric carries the rate.
const shapeLogEvery = time.Minute

// maxLoggedShapes bounds the limiter's memory against an upstream that sends
// unbounded distinct values. Past it, new values are counted but not logged.
const maxLoggedShapes = 1024

var (
	shapeLogMu   sync.Mutex
	shapeLogLast = make(map[UnknownShape]time.Time)
)

func logUnknownShape(s UnknownShape, providerID string) {
	now := time.Now()
	shapeLogMu.Lock()
	last, ok := shapeLogLast[s]
	if (ok && now.Sub(last) < shapeLogEvery) || (!ok && len(shapeLogLast) >= maxLoggedShapes) {
		shapeLogMu.Unlock()
		return
	}
	shapeLogLast[s] = now
	shapeLogMu.Unlock()

	slog.Warn("provider response has a shape the adapter does not translate; that content was dropped or defaulted",
		"provider", s.Provider,
		"provider_id", providerID,
		"kind", string(s.Kind),
		"value", s.Value,
	)
}

// ShapeRecorder collects the reports made by every ShapeReporter created from
// a context carrying it. Tests use it to observe reports without sharing
// state across parallel tests; it is safe for concurrent use.
type ShapeRecorder struct {
	mu     sync.Mutex
	shapes []UnknownShape
}

type shapeRecorderKey struct{}

// WithShapeRecorder returns a context whose reporters also record into the
// returned recorder.
func WithShapeRecorder(ctx context.Context) (context.Context, *ShapeRecorder) {
	rec := &ShapeRecorder{}
	return context.WithValue(ctx, shapeRecorderKey{}, rec), rec
}

func (r *ShapeRecorder) add(s UnknownShape) {
	r.mu.Lock()
	r.shapes = append(r.shapes, s)
	r.mu.Unlock()
}

// Shapes returns a copy of the reports recorded so far, in report order.
func (r *ShapeRecorder) Shapes() []UnknownShape {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]UnknownShape(nil), r.shapes...)
}
