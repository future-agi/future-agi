package audit

import (
	"encoding/json"
	"io"
	"log/slog"
	"os"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// Severity levels for audit events.
type Severity int

const (
	SeverityInfo Severity = iota
	SeverityWarn
	SeverityError
	SeverityCritical
)

func (s Severity) String() string {
	switch s {
	case SeverityInfo:
		return "info"
	case SeverityWarn:
		return "warn"
	case SeverityError:
		return "error"
	case SeverityCritical:
		return "critical"
	}
	return "info"
}

// ParseSeverity converts a string to Severity.
func ParseSeverity(s string) Severity {
	switch s {
	case "warn":
		return SeverityWarn
	case "error":
		return SeverityError
	case "critical":
		return SeverityCritical
	default:
		return SeverityInfo
	}
}

// Event represents a single audit log entry.
type Event struct {
	ID        int64             `json:"id"`
	Seq       int64             `json:"seq"`
	Timestamp time.Time         `json:"timestamp"`
	Category  string            `json:"category"`
	Action    string            `json:"action"`
	Severity  string            `json:"severity"`
	Actor     Actor             `json:"actor"`
	Resource  *Resource         `json:"resource,omitempty"`
	Outcome   string            `json:"outcome"`
	Reason    string            `json:"reason,omitempty"`
	RequestID string            `json:"request_id,omitempty"`
	Metadata  map[string]string `json:"metadata,omitempty"`
}

// Actor identifies who performed the action.
type Actor struct {
	Type  string `json:"type"`
	ID    string `json:"id,omitempty"`
	Name  string `json:"name,omitempty"`
	Owner string `json:"owner,omitempty"`
	Team  string `json:"team,omitempty"`
	Role  string `json:"role,omitempty"`
	IP    string `json:"ip,omitempty"`
}

// Resource identifies the target of the action.
type Resource struct {
	Type     string `json:"type"`
	ID       string `json:"id"`
	Provider string `json:"provider,omitempty"`
}

// Sink writes audit events to a destination.
type Sink interface {
	Write(events []*Event) error
	Close() error
}

// WriterSink writes JSON-lines audit events to an io.Writer.
type WriterSink struct {
	w   io.Writer
	enc *json.Encoder
}

// NewWriterSink creates a sink that writes to the given writer.
func NewWriterSink(w io.Writer) *WriterSink {
	return &WriterSink{w: w, enc: json.NewEncoder(w)}
}

// NewStdoutSink creates a sink that writes to os.Stdout.
func NewStdoutSink() *WriterSink {
	return NewWriterSink(os.Stdout)
}

func (s *WriterSink) Write(events []*Event) error {
	for _, e := range events {
		if err := s.enc.Encode(e); err != nil {
			return err
		}
	}
	return nil
}

func (s *WriterSink) Close() error { return nil }

// Logger is the central audit event logger.
// Events are pushed to a buffered channel and drained by a background goroutine.
type Logger struct {
	events     chan *Event
	sinks      []Sink
	seq        atomic.Int64
	categories map[string]bool
	minSev     Severity
	done       chan struct{}
	dropped    atomic.Int64
}

// NewLogger creates an audit logger from config.
func NewLogger(cfg config.AuditConfig) *Logger {
	bufSize := cfg.BufferSize
	if bufSize <= 0 {
		bufSize = 4096
	}

	l := &Logger{
		events:     make(chan *Event, bufSize),
		categories: make(map[string]bool),
		minSev:     ParseSeverity(cfg.MinSeverity),
		done:       make(chan struct{}),
	}

	for _, cat := range cfg.Categories {
		l.categories[cat] = true
	}

	// Create sinks from config.
	for _, sc := range cfg.Sinks {
		switch sc.Type {
		case "stdout", "":
			l.sinks = append(l.sinks, NewStdoutSink())
		// file and webhook sinks can be added later.
		default:
			slog.Warn("audit: unknown sink type", "type", sc.Type)
		}
	}

	// Default to stdout if no sinks configured.
	if len(l.sinks) == 0 {
		l.sinks = append(l.sinks, NewStdoutSink())
	}

	go l.drain()
	return l
}

// NewLoggerWithSinks creates a logger with explicit sinks (for testing).
func NewLoggerWithSinks(sinks []Sink, categories []string, minSeverity Severity, bufSize int) *Logger {
	if bufSize <= 0 {
		bufSize = 4096
	}
	l := &Logger{
		events:     make(chan *Event, bufSize),
		sinks:      sinks,
		categories: make(map[string]bool),
		minSev:     minSeverity,
		done:       make(chan struct{}),
	}
	for _, cat := range categories {
		l.categories[cat] = true
	}
	go l.drain()
	return l
}

// Emit sends an audit event. Non-blocking; drops if buffer full.
func (l *Logger) Emit(e *Event) {
	// Apply category filter.
	if len(l.categories) > 0 && !l.categories[e.Category] {
		return
	}

	// Apply severity filter.
	if ParseSeverity(e.Severity) < l.minSev {
		return
	}

	// Assign sequence and timestamp.
	e.Seq = l.seq.Add(1)
	e.ID = e.Seq
	if e.Timestamp.IsZero() {
		e.Timestamp = time.Now().UTC()
	}

	select {
	case l.events <- e:
	default:
		l.dropped.Add(1)
	}
}

// Dropped returns the number of events dropped due to buffer full.
func (l *Logger) Dropped() int64 {
	return l.dropped.Load()
}

// Close closes the event channel and waits for drain to complete.
func (l *Logger) Close() {
	close(l.events)
	<-l.done
	for _, s := range l.sinks {
		s.Close()
	}
}

func (l *Logger) drain() {
	defer close(l.done)
	batch := make([]*Event, 0, 64)
	for e := range l.events {
		batch = append(batch, e)
		// Drain remaining buffered events.
	drainLoop:
		for {
			select {
			case next, ok := <-l.events:
				if !ok {
					break drainLoop
				}
				batch = append(batch, next)
				if len(batch) >= 64 {
					break drainLoop
				}
			default:
				break drainLoop
			}
		}

		for _, sink := range l.sinks {
			if err := sink.Write(batch); err != nil {
				slog.Error("audit: sink write error", "error", err)
			}
		}
		batch = batch[:0]
	}
}
