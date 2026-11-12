package mcp

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"time"
)

var (
	// ErrClientGone: the client disconnected, or a write to it failed, so
	// the stream is over.
	ErrClientGone = errors.New("mcp: sse client gone")
	// ErrSSEClosed: the stream was closed by its handler.
	ErrSSEClosed = errors.New("mcp: sse stream closed")
)

// sseWriteTimeout bounds each write, so a client that stopped reading cannot
// hold the stream open. It replaces the server's whole-response write
// timeout, which would end a long-lived stream.
const sseWriteTimeout = 30 * time.Second

// SSEWriter writes one Server-Sent Events stream: every event is flushed as
// it is written, a comment line is sent every keep-alive interval, and once
// the client is gone every call returns ErrClientGone. Event and the
// keep-alive loop are serialised; call Close before the handler returns.
type SSEWriter struct {
	mu     sync.Mutex
	w      http.ResponseWriter
	rc     *http.ResponseController
	ctx    context.Context
	err    error         // sticky: ErrClientGone or ErrSSEClosed
	done   chan struct{} // closed when err is set
	exited chan struct{} // closed when the keep-alive loop has returned
}

// NewSSEWriter sends the stream's headers (text/event-stream, no caching, no
// proxy buffering) and starts the keep-alive loop. keepAlive <= 0 sends no
// keep-alives. It fails when the response cannot be flushed.
func NewSSEWriter(w http.ResponseWriter, r *http.Request, keepAlive time.Duration) (*SSEWriter, error) {
	s := &SSEWriter{
		w:      w,
		rc:     http.NewResponseController(w),
		ctx:    r.Context(),
		done:   make(chan struct{}),
		exited: make(chan struct{}),
	}
	h := w.Header()
	h.Set("Content-Type", "text/event-stream")
	h.Set("Cache-Control", "no-cache")
	h.Set("X-Accel-Buffering", "no")
	s.extendWriteDeadline()
	w.WriteHeader(http.StatusOK)
	if err := s.rc.Flush(); err != nil {
		if errors.Is(err, http.ErrNotSupported) {
			return nil, fmt.Errorf("mcp: streaming is not supported by this response writer")
		}
		return nil, ErrClientGone
	}
	go s.loop(keepAlive)
	return s, nil
}

// Done is closed when the stream ends: the client is gone or Close was called.
func (s *SSEWriter) Done() <-chan struct{} { return s.done }

// Event writes one event and flushes it. id and name are optional and must
// be single lines; data may span lines, each sent as its own data line.
func (s *SSEWriter) Event(id, name string, data []byte) error {
	if strings.ContainsAny(id, "\r\n\x00") || strings.ContainsAny(name, "\r\n") {
		return fmt.Errorf("mcp: sse event id and name must be single lines")
	}
	var b strings.Builder
	if id != "" {
		b.WriteString("id: " + id + "\n")
	}
	if name != "" {
		b.WriteString("event: " + name + "\n")
	}
	text := strings.ReplaceAll(strings.ReplaceAll(string(data), "\r\n", "\n"), "\r", "\n")
	for _, line := range strings.Split(text, "\n") {
		b.WriteString("data: " + line + "\n")
	}
	b.WriteString("\n")
	return s.write(b.String())
}

// Close ends the stream from the handler's side and waits for the keep-alive
// loop to stop, so nothing writes to the response after the handler returns.
// It clears the per-write deadline, which would otherwise outlive the stream
// on a kept-alive connection.
func (s *SSEWriter) Close() {
	s.mu.Lock()
	s.fail(ErrSSEClosed)
	s.mu.Unlock()
	<-s.exited
	_ = s.rc.SetWriteDeadline(time.Time{})
}

// write sends one framed chunk and flushes it.
func (s *SSEWriter) write(chunk string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.err != nil {
		return s.err
	}
	if s.ctx.Err() != nil {
		return s.fail(ErrClientGone)
	}
	s.extendWriteDeadline()
	if _, err := s.w.Write([]byte(chunk)); err != nil {
		return s.fail(ErrClientGone)
	}
	if err := s.rc.Flush(); err != nil {
		return s.fail(ErrClientGone)
	}
	return nil
}

// fail records the stream's end once and wakes everything waiting on it.
// The caller holds mu.
func (s *SSEWriter) fail(err error) error {
	if s.err == nil {
		s.err = err
		close(s.done)
	}
	return s.err
}

func (s *SSEWriter) extendWriteDeadline() {
	// A writer that cannot set deadlines keeps the server's own timeout.
	_ = s.rc.SetWriteDeadline(time.Now().Add(sseWriteTimeout))
}

// loop sends keep-alives and notices a client that went away between events.
func (s *SSEWriter) loop(keepAlive time.Duration) {
	defer close(s.exited)
	var tick <-chan time.Time
	if keepAlive > 0 {
		t := time.NewTicker(keepAlive)
		defer t.Stop()
		tick = t.C
	}
	for {
		select {
		case <-s.done:
			return
		case <-s.ctx.Done():
			s.mu.Lock()
			s.fail(ErrClientGone)
			s.mu.Unlock()
			return
		case <-tick:
			if s.write(": keep-alive\n\n") != nil {
				return
			}
		}
	}
}
