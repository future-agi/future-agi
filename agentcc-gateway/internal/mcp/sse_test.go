package mcp

import (
	"bufio"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

// sseReadFrame reads one SSE frame (lines up to a blank line), failing the
// test if none arrives within two seconds.
func sseReadFrame(t *testing.T, r *bufio.Reader) string {
	t.Helper()
	type frame struct {
		text string
		err  error
	}
	got := make(chan frame, 1)
	go func() {
		var b strings.Builder
		for {
			line, err := r.ReadString('\n')
			if err != nil {
				got <- frame{b.String(), err}
				return
			}
			if line == "\n" {
				got <- frame{b.String(), nil}
				return
			}
			b.WriteString(line)
		}
	}()
	select {
	case f := <-got:
		if f.err != nil {
			t.Fatalf("reading the stream: %v (so far %q)", f.err, f.text)
		}
		return f.text
	case <-time.After(2 * time.Second):
		t.Fatal("no SSE frame within 2s")
		return ""
	}
}

// TestSSEWriterKeepAliveAndDisconnect: the stream carries the SSE headers;
// each event reaches the client as soon as it is written, framed with its id,
// name and one data line per line; keep-alive comments arrive between events;
// and once the client disconnects the writer reports ErrClientGone, closes
// Done and stops writing.
func TestSSEWriterKeepAliveAndDisconnect(t *testing.T) {
	type outcome struct {
		err      error
		doneSeen bool
	}
	events := make(chan [3]string)
	result := make(chan outcome, 1)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s, err := NewSSEWriter(w, r, 20*time.Millisecond)
		if err != nil {
			result <- outcome{err: err}
			return
		}
		defer s.Close()
		for ev := range events {
			if err := s.Event(ev[0], ev[1], []byte(ev[2])); err != nil {
				result <- outcome{err: err}
				return
			}
		}
		// The client hangs up: Done closes without another write, and the
		// next event is refused.
		select {
		case <-s.Done():
		case <-time.After(5 * time.Second):
			result <- outcome{err: errors.New("Done did not close after the client left")}
			return
		}
		result <- outcome{err: s.Event("9", "late", []byte("x")), doneSeen: true}
	}))
	defer srv.Close()

	resp, err := http.Get(srv.URL)
	if err != nil {
		t.Fatal(err)
	}
	// On any early failure, hang up and release the handler before
	// srv.Close, which waits for it.
	defer resp.Body.Close()
	var closeEvents sync.Once
	defer closeEvents.Do(func() { close(events) })
	for k, want := range map[string]string{"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no"} {
		if got := resp.Header.Get(k); got != want {
			t.Errorf("%s = %q, want %q", k, got, want)
		}
	}
	r := bufio.NewReader(resp.Body)

	events <- [3]string{"1", "message", `{"jsonrpc":"2.0","id":1,"result":{}}`}
	if got, want := sseReadFrame(t, r), "id: 1\nevent: message\ndata: {\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{}}\n"; got != want {
		t.Errorf("first event = %q, want %q", got, want)
	}

	// With no event pending, keep-alive comments arrive on the interval.
	deadline := time.Now().Add(2 * time.Second)
	keepAlives := 0
	for keepAlives < 2 && time.Now().Before(deadline) {
		frame := sseReadFrame(t, r)
		if !strings.HasPrefix(frame, ":") {
			t.Fatalf("expected a keep-alive comment, got %q", frame)
		}
		keepAlives++
	}
	if keepAlives < 2 {
		t.Fatalf("got %d keep-alives", keepAlives)
	}

	// A multi-line payload is one data line per line; no id or name.
	events <- [3]string{"", "", "line one\nline two\r\nline three"}
	for {
		frame := sseReadFrame(t, r)
		if strings.HasPrefix(frame, ":") {
			continue
		}
		if want := "data: line one\ndata: line two\ndata: line three\n"; frame != want {
			t.Errorf("multi-line event = %q, want %q", frame, want)
		}
		break
	}

	resp.Body.Close()
	closeEvents.Do(func() { close(events) })
	select {
	case out := <-result:
		if !out.doneSeen || !errors.Is(out.err, ErrClientGone) {
			t.Errorf("after disconnect: Done seen %v, Event = %v; want ErrClientGone", out.doneSeen, out.err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("the handler never saw the disconnect")
	}
}

// TestSSEWriterRefusesFramingInjection: an id or event name holding a line
// break is refused, and nothing is written.
func TestSSEWriterRefusesFramingInjection(t *testing.T) {
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/mcp", nil)
	s, err := NewSSEWriter(rec, req, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	for _, ev := range [][2]string{{"1\ndata: forged", "message"}, {"1", "message\nid: 2"}, {"1\r", ""}} {
		if err := s.Event(ev[0], ev[1], []byte("x")); err == nil {
			t.Errorf("Event(%q, %q) accepted a line break", ev[0], ev[1])
		}
	}
	if body := rec.Body.String(); body != "" {
		t.Errorf("refused events wrote %q", body)
	}
	if err := s.Event("7", "message", []byte("ok")); err != nil {
		t.Fatal(err)
	}
	if body := rec.Body.String(); body != "id: 7\nevent: message\ndata: ok\n\n" {
		t.Errorf("body = %q", body)
	}
	s.Close()
	if err := s.Event("8", "message", []byte("after")); !errors.Is(err, ErrSSEClosed) {
		t.Errorf("Event after Close = %v, want ErrSSEClosed", err)
	}
	select {
	case <-s.Done():
	default:
		t.Error("Done is open after Close")
	}
}

// TestSSEWriterNeedsFlusher: a response writer that cannot flush is refused
// up front instead of buffering a stream that never reaches the client.
func TestSSEWriterNeedsFlusher(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/mcp", nil)
	if _, err := NewSSEWriter(sseNoFlushWriter{httptest.NewRecorder()}, req, 0); err == nil {
		t.Error("NewSSEWriter accepted a writer that cannot flush")
	}
}

type sseNoFlushWriter struct{ rec *httptest.ResponseRecorder }

func (w sseNoFlushWriter) Header() http.Header         { return w.rec.Header() }
func (w sseNoFlushWriter) Write(b []byte) (int, error) { return w.rec.Write(b) }
func (w sseNoFlushWriter) WriteHeader(code int)        { w.rec.WriteHeader(code) }
