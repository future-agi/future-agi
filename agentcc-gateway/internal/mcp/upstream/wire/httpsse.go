package wire

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"sync"
	"sync/atomic"

	"github.com/futureagi/agentcc-gateway/internal/mcp/contentpolicy"
)

var (
	// ErrSessionClosed is a 2024-11-05 session whose stream has ended.
	ErrSessionClosed = errors.New("the upstream's HTTP+SSE stream ended")
	// ErrSessionGone is a 404 from the message endpoint: the upstream no
	// longer knows the session.
	ErrSessionGone = errors.New("the upstream's HTTP+SSE session is gone (HTTP 404)")
)

// Session is one 2024-11-05 HTTP+SSE session: a GET stream whose first
// event names the message endpoint (same origin as the stream's URL), each
// request POSTed there, and every answer read from the stream.
type Session struct {
	client    *http.Client
	endpoint  string
	header    http.Header // the configured credential, sent on every POST
	onMessage func(raw []byte)
	onDead    func(error)

	cancel context.CancelFunc
	done   chan struct{}

	mu      sync.Mutex
	pending map[string]chan []byte
	dead    error
	closed  atomic.Bool
}

// SessionOptions configure OpenSession.
type SessionOptions struct {
	Client *http.Client
	URL    string
	// Header is sent on the GET and on every POST (the server's credential).
	Header http.Header
	// MaxBytes bounds one event on the stream (<= 0: no bound).
	MaxBytes int64
	// OnMessage receives the stream's messages that answer no request.
	OnMessage func(raw []byte)
	// OnDead is told once when the stream ends on its own (not on Close).
	OnDead func(error)
}

// OpenSession opens the stream and waits, within ctx, for its endpoint event.
// The stream itself outlives ctx, until Close.
func OpenSession(ctx context.Context, o SessionOptions) (*Session, error) {
	life, cancel := context.WithCancel(context.Background())
	stop := context.AfterFunc(ctx, cancel)
	req, err := http.NewRequestWithContext(life, http.MethodGet, o.URL, nil)
	if err != nil {
		cancel()
		return nil, err
	}
	req.Header = o.Header.Clone()
	req.Header.Set("Accept", "text/event-stream")
	resp, err := o.Client.Do(req)
	if err != nil {
		cancel()
		return nil, err
	}
	if resp.StatusCode != http.StatusOK || !IsEventStream(resp.Header) {
		resp.Body.Close()
		cancel()
		return nil, fmt.Errorf("HTTP+SSE stream: http %d, Content-Type %q", resp.StatusCode, resp.Header.Get("Content-Type"))
	}
	events := NewReader(resp.Body, contentpolicy.ByteLimit(o.MaxBytes))
	first, err := events.Next()
	if err == nil && first.Name != "endpoint" {
		err = fmt.Errorf("HTTP+SSE stream: first event %q, want endpoint", first.Name)
	}
	var endpoint string
	if err == nil {
		endpoint, err = sameOrigin(o.URL, string(bytes.TrimSpace(first.Data)))
	}
	if !stop() || err != nil {
		resp.Body.Close()
		cancel()
		if err == nil {
			err = ctx.Err()
		}
		return nil, err
	}
	s := &Session{
		client: o.Client, endpoint: endpoint, header: o.Header.Clone(),
		onMessage: o.OnMessage, onDead: o.OnDead,
		cancel: cancel, done: make(chan struct{}), pending: map[string]chan []byte{},
	}
	go s.read(resp.Body, events)
	return s, nil
}

// sameOrigin resolves the endpoint event's URL against the stream's and
// refuses one on another origin.
func sameOrigin(stream, endpoint string) (string, error) {
	base, err := url.Parse(stream)
	if err != nil {
		return "", err
	}
	u, err := base.Parse(endpoint)
	if err != nil {
		return "", fmt.Errorf("HTTP+SSE endpoint %q: %w", endpoint, err)
	}
	if u.Scheme != base.Scheme || u.Host != base.Host {
		return "", fmt.Errorf("HTTP+SSE endpoint on another origin (%s)", u.Host)
	}
	return u.String(), nil
}

func (s *Session) read(body io.ReadCloser, events *Reader) {
	defer close(s.done)
	defer body.Close()
	var cause error
	for {
		ev, err := events.Next()
		if err != nil {
			cause = ErrSessionClosed
			if !errors.Is(err, io.EOF) {
				cause = fmt.Errorf("%w: %w", ErrSessionClosed, err)
			}
			break
		}
		if ev.Name != "" && ev.Name != "message" {
			continue
		}
		if err := checkContent(ev.Data, events.max); err != nil {
			cause = err
			break
		}
		var id struct {
			ID     json.RawMessage `json:"id"`
			Method string          `json:"method"`
		}
		if json.Unmarshal(ev.Data, &id) != nil {
			continue
		}
		if id.Method == "" && len(id.ID) > 0 && s.deliver(string(bytes.TrimSpace(id.ID)), ev.Data) {
			continue
		}
		if s.onMessage != nil {
			s.onMessage(ev.Data)
		}
	}
	s.stop(cause)
}

func (s *Session) deliver(id string, raw []byte) bool {
	s.mu.Lock()
	ch, ok := s.pending[id]
	delete(s.pending, id)
	s.mu.Unlock()
	if ok {
		ch <- raw
	}
	return ok
}

// stop records why the session ended, tells the owner unless Close ended
// it, and fails every pending request.
func (s *Session) stop(cause error) {
	s.mu.Lock()
	if s.dead != nil {
		s.mu.Unlock()
		return
	}
	s.dead = cause
	pending := s.pending
	s.pending = nil
	s.mu.Unlock()
	if !s.closed.Load() && s.onDead != nil {
		s.onDead(cause)
	}
	for _, ch := range pending {
		close(ch)
	}
}

// Send POSTs one message to the endpoint, with header added (and the
// session's credential unless a per-call one replaces it, withCredential
// false), and for a request (id set) waits for its answer on the stream.
func (s *Session) Send(ctx context.Context, id json.RawMessage, body []byte, header http.Header, withCredential bool) ([]byte, error) {
	var ch chan []byte
	key := string(bytes.TrimSpace(id))
	if len(id) > 0 {
		ch = make(chan []byte, 1)
		s.mu.Lock()
		if s.dead != nil {
			err := s.dead
			s.mu.Unlock()
			return nil, err
		}
		s.pending[key] = ch
		s.mu.Unlock()
		defer s.forget(key)
	} else if err := s.Err(); err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, s.endpoint, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header = http.Header{}
	if withCredential {
		req.Header = s.header.Clone()
	}
	for k, vs := range header {
		req.Header[http.CanonicalHeaderKey(k)] = append([]string(nil), vs...)
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := s.client.Do(req)
	if err != nil {
		return nil, err
	}
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, errorBodyLimit))
	resp.Body.Close()
	switch {
	case resp.StatusCode == http.StatusNotFound:
		return nil, ErrSessionGone
	case resp.StatusCode/100 != 2:
		return nil, fmt.Errorf("HTTP+SSE message endpoint: http %d", resp.StatusCode)
	case ch == nil:
		return nil, nil
	}
	select {
	case raw, ok := <-ch:
		if !ok {
			return nil, s.Err()
		}
		return raw, nil
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

func (s *Session) forget(id string) {
	s.mu.Lock()
	delete(s.pending, id)
	s.mu.Unlock()
}

// Err is why the session ended, or nil while it runs.
func (s *Session) Err() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.dead
}

// Close ends the stream and fails every pending request.
func (s *Session) Close() {
	if s.closed.Swap(true) {
		return
	}
	s.cancel()
	<-s.done
}
