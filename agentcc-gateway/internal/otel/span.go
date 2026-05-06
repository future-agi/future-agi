package otel

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"io"
	"sync"
	"sync/atomic"
	"time"
)

// SpanStatus indicates the outcome of a span.
type SpanStatus int

const (
	StatusOK    SpanStatus = iota
	StatusError
)

func (s SpanStatus) String() string {
	switch s {
	case StatusError:
		return "ERROR"
	default:
		return "OK"
	}
}

// Span represents a single trace span with OTel-compatible attributes.
type Span struct {
	TraceID     string                 `json:"trace_id"`
	SpanID      string                 `json:"span_id"`
	ParentID    string                 `json:"parent_id,omitempty"`
	Name        string                 `json:"name"`
	ServiceName string                 `json:"service_name"`
	StartTime   time.Time              `json:"start_time"`
	EndTime     time.Time              `json:"end_time,omitempty"`
	Duration    time.Duration          `json:"duration_ms,omitempty"`
	Status      string                 `json:"status"`
	Attributes  map[string]interface{} `json:"attributes,omitempty"`
}

// NewSpan creates a new root span with random trace and span IDs.
func NewSpan(name, serviceName string) *Span {
	return &Span{
		TraceID:     generateID(16), // 32 hex chars
		SpanID:      generateID(8),  // 16 hex chars
		Name:        name,
		ServiceName: serviceName,
		StartTime:   time.Now(),
		Status:      StatusOK.String(),
		Attributes:  make(map[string]interface{}, 16),
	}
}

// NewChildSpan creates a child span under an existing trace.
func NewChildSpan(name, traceID, parentSpanID string) *Span {
	return &Span{
		TraceID:  traceID,
		SpanID:   generateID(8),
		ParentID: parentSpanID,
		Name:     name,
		StartTime: time.Now(),
		Status:    StatusOK.String(),
		Attributes: make(map[string]interface{}, 8),
	}
}

// End marks the span as complete.
func (s *Span) End() {
	s.EndTime = time.Now()
	s.Duration = s.EndTime.Sub(s.StartTime)
}

// SetAttribute sets a key-value attribute on the span.
func (s *Span) SetAttribute(key string, value interface{}) {
	s.Attributes[key] = value
}

// SetError marks the span as an error with a message.
func (s *Span) SetError(msg string) {
	s.Status = StatusError.String()
	s.Attributes["error.message"] = msg
}

// generateID produces a random hex-encoded string from n random bytes.
func generateID(byteLen int) string {
	b := make([]byte, byteLen)
	if _, err := rand.Read(b); err != nil {
		// Fallback: use timestamp-based ID (should never happen).
		return hex.EncodeToString([]byte(time.Now().String())[:byteLen])
	}
	return hex.EncodeToString(b)
}

// SpanExporter exports completed spans.
type SpanExporter interface {
	Export(spans []*Span) error
	Shutdown() error
}

// StdoutExporter writes spans as JSON lines to an io.Writer.
type StdoutExporter struct {
	w   io.Writer
	enc *json.Encoder
	mu  sync.Mutex
}

// NewStdoutExporter creates an exporter that writes JSON to w.
func NewStdoutExporter(w io.Writer) *StdoutExporter {
	return &StdoutExporter{
		w:   w,
		enc: json.NewEncoder(w),
	}
}

// Export writes each span as a JSON line.
func (e *StdoutExporter) Export(spans []*Span) error {
	e.mu.Lock()
	defer e.mu.Unlock()
	for _, s := range spans {
		if err := e.enc.Encode(s); err != nil {
			return err
		}
	}
	return nil
}

// Shutdown is a no-op for the stdout exporter.
func (e *StdoutExporter) Shutdown() error {
	return nil
}

// NoopExporter discards all spans.
type NoopExporter struct{}

func (NoopExporter) Export([]*Span) error { return nil }
func (NoopExporter) Shutdown() error      { return nil }

// Metrics holds atomic counters for gateway-level telemetry.
type Metrics struct {
	RequestCount atomic.Int64
	ErrorCount   atomic.Int64
	InputTokens  atomic.Int64
	OutputTokens atomic.Int64
	CacheHits    atomic.Int64
	CacheMisses  atomic.Int64
}

// Snapshot returns a point-in-time copy of all metric values.
type MetricsSnapshot struct {
	RequestCount int64 `json:"request_count"`
	ErrorCount   int64 `json:"error_count"`
	InputTokens  int64 `json:"input_tokens"`
	OutputTokens int64 `json:"output_tokens"`
	CacheHits    int64 `json:"cache_hits"`
	CacheMisses  int64 `json:"cache_misses"`
}

// Snapshot returns a point-in-time copy of metric values.
func (m *Metrics) Snapshot() MetricsSnapshot {
	return MetricsSnapshot{
		RequestCount: m.RequestCount.Load(),
		ErrorCount:   m.ErrorCount.Load(),
		InputTokens:  m.InputTokens.Load(),
		OutputTokens: m.OutputTokens.Load(),
		CacheHits:    m.CacheHits.Load(),
		CacheMisses:  m.CacheMisses.Load(),
	}
}
