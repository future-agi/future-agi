package mcpmock

import (
	"context"
	"encoding/json"
	"errors"
	"sync"
)

// maxSessionEvents bounds the GET-stream events a session keeps for
// Last-Event-ID replay.
const maxSessionEvents = 1000

// event is one message on a legacy session's GET stream, with its id.
type event struct {
	id   int64
	data []byte
}

// session is one legacy session, either a Streamable HTTP initialize session
// or a 2024-11-05 /sse stream's: its revision and client capabilities, the
// event log that Last-Event-ID replays, the live stream if one is open, and
// the elicitations waiting for an answer.
type session struct {
	id      string
	httpSSE bool // a 2024-11-05 /sse session, which lives as long as its stream

	mu      sync.Mutex
	version string
	caps    json.RawMessage
	events  []event
	nextID  int64
	live    chan event
	pending map[string]chan *Message
	done    chan struct{}
}

// negotiated returns the session's revision and the client's capabilities.
func (s *session) negotiated() (string, json.RawMessage) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.version, s.caps
}

// setNegotiated records a 2024-11-05 session's initialize, which arrives
// after its stream opened.
func (s *session) setNegotiated(version string, caps json.RawMessage) {
	s.mu.Lock()
	s.version, s.caps = version, caps
	s.mu.Unlock()
}

func (s *session) closed() bool {
	select {
	case <-s.done:
		return true
	default:
		return false
	}
}

// push logs a GET-stream event and hands it to the open stream, if any. A
// stream that cannot keep up misses the event live; it is still replayable.
func (s *session) push(data []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.nextID++
	ev := event{id: s.nextID, data: data}
	s.events = append(s.events, ev)
	if len(s.events) > maxSessionEvents {
		s.events = s.events[len(s.events)-maxSessionEvents:]
	}
	if s.live != nil {
		select {
		case s.live <- ev:
		default:
		}
	}
}

// attach opens the GET stream: the events after lastID (none when lastID is
// negative) and the channel live events arrive on. A second GET replaces the
// first stream. detach closes it.
func (s *session) attach(lastID int64) ([]event, <-chan event, func()) {
	s.mu.Lock()
	defer s.mu.Unlock()
	var backlog []event
	if lastID >= 0 {
		for _, ev := range s.events {
			if ev.id > lastID {
				backlog = append(backlog, ev)
			}
		}
	}
	ch := make(chan event, 256)
	s.live = ch
	return backlog, ch, func() {
		s.mu.Lock()
		if s.live == ch {
			s.live = nil
		}
		s.mu.Unlock()
	}
}

// await registers an elicitation the session's client must answer.
func (s *session) await(id string) (chan *Message, func()) {
	ch := make(chan *Message, 1)
	s.mu.Lock()
	s.pending[id] = ch
	s.mu.Unlock()
	return ch, func() {
		s.mu.Lock()
		delete(s.pending, id)
		s.mu.Unlock()
	}
}

// answer delivers a client's response to a waiting elicitation.
func (s *session) answer(msg *Message) bool {
	s.mu.Lock()
	ch, ok := s.pending[string(msg.ID)]
	delete(s.pending, string(msg.ID))
	s.mu.Unlock()
	if ok {
		ch <- msg
	}
	return ok
}

// waitAnswer waits for the answer to an elicitation sent with send.
func (s *session) waitAnswer(ctx context.Context, req *Message, send func(*Message) error) (*Message, error) {
	ch, cancel := s.await(string(req.ID))
	defer cancel()
	if err := send(req); err != nil {
		return nil, err
	}
	select {
	case resp := <-ch:
		return resp, nil
	case <-s.done:
		return nil, errors.New("session closed")
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

type sessionStore struct {
	mu sync.Mutex
	m  map[string]*session
}

func newSessionStore() *sessionStore { return &sessionStore{m: map[string]*session{}} }

func (st *sessionStore) create(version string, caps json.RawMessage, httpSSE bool) *session {
	s := &session{id: randomID(), version: version, caps: caps, httpSSE: httpSSE,
		pending: map[string]chan *Message{}, done: make(chan struct{})}
	st.mu.Lock()
	st.m[s.id] = s
	st.mu.Unlock()
	return s
}

func (st *sessionStore) get(id string) *session {
	st.mu.Lock()
	defer st.mu.Unlock()
	return st.m[id]
}

// remove ends a session (DELETE or drop_session); its streams close.
func (st *sessionStore) remove(id string) bool {
	st.mu.Lock()
	s, ok := st.m[id]
	delete(st.m, id)
	st.mu.Unlock()
	if ok {
		close(s.done)
	}
	return ok
}

func (st *sessionStore) dropAll() int {
	n := 0
	for _, s := range st.all() {
		if st.remove(s.id) {
			n++
		}
	}
	return n
}

func (st *sessionStore) all() []*session {
	st.mu.Lock()
	defer st.mu.Unlock()
	out := make([]*session, 0, len(st.m))
	for _, s := range st.m {
		out = append(out, s)
	}
	return out
}

func (st *sessionStore) count() int {
	st.mu.Lock()
	defer st.mu.Unlock()
	return len(st.m)
}
