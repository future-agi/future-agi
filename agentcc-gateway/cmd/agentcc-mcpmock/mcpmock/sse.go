package mcpmock

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"sync"
)

// sseWriter writes one Server-Sent Events stream, unbuffered.
type sseWriter struct {
	mu sync.Mutex
	w  http.ResponseWriter
	f  http.Flusher
}

func newSSEWriter(w http.ResponseWriter) *sseWriter {
	h := w.Header()
	h.Set("Content-Type", "text/event-stream")
	h.Set("Cache-Control", "no-cache")
	h.Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	f, _ := w.(http.Flusher)
	s := &sseWriter{w: w, f: f}
	s.flush()
	return s
}

func (s *sseWriter) flush() {
	if s.f != nil {
		s.f.Flush()
	}
}

func (s *sseWriter) write(frame string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, err := fmt.Fprint(s.w, frame); err != nil {
		return err
	}
	s.flush()
	return nil
}

// send writes a JSON-RPC message as a "message" event with no id.
func (s *sseWriter) send(msg *Message) error {
	data, err := json.Marshal(msg)
	if err != nil {
		return err
	}
	return s.write("event: message\ndata: " + string(data) + "\n\n")
}

// event writes a GET-stream event with its id, for Last-Event-ID replay.
func (s *sseWriter) event(id int64, data []byte) error {
	return s.write(fmt.Sprintf("id: %d\nevent: message\ndata: %s\n\n", id, data))
}

// named writes an event with the given name and raw data (2024-11-05's
// endpoint event).
func (s *sseWriter) named(name, data string) error {
	return s.write("event: " + name + "\ndata: " + data + "\n\n")
}

func (s *sseWriter) comment(text string) error { return s.write(": " + text + "\n\n") }

func acceptsSSE(r *http.Request) bool {
	return strings.Contains(r.Header.Get("Accept"), "text/event-stream")
}

// postStream is a Streamable HTTP POST answered as SSE: notifications go on
// it, and a legacy session's elicitation is sent on it and answered by a
// later POST on the same session.
type postStream struct {
	sw   *sseWriter
	sess *session
}

func (p *postStream) send(msg *Message) error { return p.sw.send(msg) }

func (p *postStream) ask(ctx context.Context, req *Message) (*Message, error) {
	if p.sess == nil {
		return nil, errors.New("no session to answer on")
	}
	return p.sess.waitAnswer(ctx, req, p.sw.send)
}

// sessionStream sends on a session's own stream (2024-11-05 HTTP+SSE, where
// every answer goes on the /sse stream).
type sessionStream struct{ sess *session }

func (p sessionStream) send(msg *Message) error {
	if p.sess.closed() {
		return errors.New("session closed")
	}
	data, err := json.Marshal(msg)
	if err != nil {
		return err
	}
	p.sess.push(data)
	return nil
}

func (p sessionStream) ask(ctx context.Context, req *Message) (*Message, error) {
	return p.sess.waitAnswer(ctx, req, p.send)
}
