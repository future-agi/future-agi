package mcp

import (
	"bytes"
	"context"
	"log/slog"
	"slices"
	"sync"
	"time"
)

// Server-initiated messages on legacy sessions (tools/list_changed today,
// elicitation from M28) are published per session: each is appended to the
// session's event log, which gives it its event id, and handed to exactly one
// of the GET streams this process holds for the session (the spec forbids
// sending one message on several streams). A stream resumed with
// Last-Event-ID, here or on another replica, replays the log after that id.

// streamBuffer is how many events a stream may lag before the next is left
// to replay (it stays in the log).
const streamBuffer = 32

// sessionTapKey is the sseClients entry the session tap holds. It can never
// be a session id (sealed ids are base64url).
const sessionTapKey = "\x00mcp-session-tap"

// sessionEventTimeout bounds one event-log write or read.
const sessionEventTimeout = 2 * time.Second

// streamEvent is one message on a session's GET stream.
type streamEvent struct {
	id   string // "" when the event log could not record it
	data []byte
}

// sessionStream is one open GET stream: its own registration, so a second
// stream on the session never replaces or removes the first's (bug 21).
type sessionStream struct {
	ch   chan streamEvent
	done chan struct{} // closed when the session ends or the server closes
	once sync.Once
}

func (st *sessionStream) end() { st.once.Do(func() { close(st.done) }) }

// streamHub is this process's registry of open GET streams, by session hash,
// oldest first.
type streamHub struct {
	mu      sync.Mutex
	streams map[string][]*sessionStream
	closed  bool

	tapOnce sync.Once
	tapStop chan struct{}
}

func newStreamHub() *streamHub {
	return &streamHub{streams: map[string][]*sessionStream{}, tapStop: make(chan struct{})}
}

// register adds a stream for the session; false once the hub is closed.
func (h *streamHub) register(hash string) (*sessionStream, bool) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.closed {
		return nil, false
	}
	st := &sessionStream{ch: make(chan streamEvent, streamBuffer), done: make(chan struct{})}
	h.streams[hash] = append(h.streams[hash], st)
	return st, true
}

// unregister removes exactly st, leaving the session's other streams.
func (h *streamHub) unregister(hash string, st *sessionStream) {
	h.mu.Lock()
	defer h.mu.Unlock()
	list := h.streams[hash]
	for i, other := range list {
		if other == st {
			list = append(list[:i:i], list[i+1:]...)
			break
		}
	}
	if len(list) == 0 {
		delete(h.streams, hash)
	} else {
		h.streams[hash] = list
	}
}

// deliver hands ev to one open stream of the session, newest first (an
// older one may be a connection the client has given up on). It reports
// false when no stream took it; the event log still has it.
func (h *streamHub) deliver(hash string, ev streamEvent) bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	list := h.streams[hash]
	for i := len(list) - 1; i >= 0; i-- {
		select {
		case list[i].ch <- ev:
			return true
		default:
		}
	}
	return false
}

func (h *streamHub) hasStreams(hash string) bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	return len(h.streams[hash]) > 0
}

func (h *streamHub) streamCount(hash string) int {
	h.mu.Lock()
	defer h.mu.Unlock()
	return len(h.streams[hash])
}

// endSession ends the session's streams here (DELETE, or found gone).
func (h *streamHub) endSession(hash string) {
	h.mu.Lock()
	list := h.streams[hash]
	delete(h.streams, hash)
	h.mu.Unlock()
	for _, st := range list {
		st.end()
	}
}

// close ends every stream and stops the tap; nothing registers after it.
func (h *streamHub) close() {
	h.mu.Lock()
	h.closed = true
	all := h.streams
	h.streams = map[string][]*sessionStream{}
	h.mu.Unlock()
	close(h.tapStop)
	for _, list := range all {
		for _, st := range list {
			st.end()
		}
	}
}

// ensureSessionTap registers, once, the channel notifyToolsChanged
// broadcasts to (sseClients), and starts the goroutine that publishes each
// broadcast to every live session this process holds, open stream or not, so
// a client that reconnects with Last-Event-ID gets what it missed. Close
// (server.go) closes the channel after it ends the sessions.
func (s *Server) ensureSessionTap() {
	hub := s.sessions.hub
	hub.tapOnce.Do(func() {
		ch := make(chan []byte, streamBuffer)
		hub.mu.Lock()
		closed := hub.closed
		hub.mu.Unlock()
		if closed {
			return
		}
		s.sseMu.Lock()
		s.sseClients[sessionTapKey] = ch
		s.sseMu.Unlock()
		go s.runSessionTap(ch, hub.tapStop)
	})
}

func (s *Server) runSessionTap(ch <-chan []byte, stop <-chan struct{}) {
	for {
		select {
		case <-stop:
			return
		case data, ok := <-ch:
			if !ok {
				return
			}
			for _, msg := range coalesce(data, ch) {
				s.sessions.sessions.Range(func(_, v interface{}) bool {
					if sess := v.(*Session); sess.IdleSince() <= s.sessions.ttl {
						s.publishSessionEvent(sess, msg)
					}
					return true
				})
			}
		}
	}
}

// coalesce returns first and the broadcasts already queued behind it, each
// distinct message once: a burst of identical tools/list_changed costs one
// pass over the sessions, not one per broadcast.
func coalesce(first []byte, ch <-chan []byte) [][]byte {
	msgs := [][]byte{first}
	for {
		select {
		case data, ok := <-ch:
			if !ok {
				return msgs
			}
			if !slices.ContainsFunc(msgs, func(m []byte) bool { return bytes.Equal(m, data) }) {
				msgs = append(msgs, data)
			}
		default:
			return msgs
		}
	}
}

// publishSessionEvent logs data in the session's event log and hands it to
// one of its open streams here. A session the store no longer holds is
// forgotten instead.
func (s *Server) publishSessionEvent(sess *Session, data []byte) {
	ctx, cancel := context.WithTimeout(context.Background(), sessionEventTimeout)
	defer cancel()
	var id string
	if log, ok := s.sessionStore().(SessionEventLog); ok {
		eventID, alive, err := log.AppendSessionEvent(ctx, sess.key(), sess.eventsKey(), data, sessionEventLogMax, s.sessions.ttl)
		switch {
		case err != nil:
			// Delivered live without an id: it cannot be replayed.
			slog.Warn("mcp session event not logged", "session_hash", sess.Hash(), "error", err)
		case !alive:
			s.sessions.forget(sess.ID)
			return
		default:
			id = eventID
		}
	} else {
		id = s.sessions.events.append(sess.eventsKey(), data)
	}
	s.sessions.hub.deliver(sess.Hash(), streamEvent{id: id, data: data})
}

// sessionEventsAfter calls each for the session's logged events after
// afterID, oldest first.
func (s *Server) sessionEventsAfter(ctx context.Context, sess *Session, afterID string, each func(id string, data []byte)) error {
	if log, ok := s.sessionStore().(SessionEventLog); ok {
		ctx, cancel := context.WithTimeout(ctx, sessionEventTimeout)
		defer cancel()
		return log.SessionEventsAfter(ctx, sess.eventsKey(), afterID, sessionEventLogMax, each)
	}
	s.sessions.events.after(sess.eventsKey(), afterID, sessionEventLogMax, each)
	return nil
}
