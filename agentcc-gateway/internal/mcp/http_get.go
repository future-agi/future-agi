package mcp

import (
	"errors"
	"log/slog"
	"net/http"
	"time"
)

// streamKeepAlive is the interval of the keep-alive comment on a GET stream.
const streamKeepAlive = 15 * time.Second

// HandleSSE handles GET on the MCP endpoint: the server-to-client stream of
// a legacy session. Each message carries an event id; a client that
// reconnects with Last-Event-ID first gets what was logged after it. The
// stream writes through SSEWriter, whose per-write deadline replaces the
// server's whole-response WriteTimeout, so a quiet stream lives as long as
// its session.
//
// Owner: M12.
func (s *Server) HandleSSE(w http.ResponseWriter, r *http.Request) {
	r, ok := s.authenticateHTTP(w, r)
	if !ok || refuseStatelessStream(w, r) {
		// No MCP-Session-Id: 405 with Allow: POST (M11 IR-3; only a legacy
		// session has a GET stream, and a 2026-07-28 client uses POST alone).
		return
	}
	sessionID := r.Header.Get("MCP-Session-Id")
	session, err := s.resumeSession(r.Context(), sessionID, r)
	if err != nil {
		http.Error(w, "session store unavailable", http.StatusServiceUnavailable)
		return
	}
	if session == nil {
		http.Error(w, "invalid or expired session", http.StatusNotFound)
		return
	}

	// Register before the headers go out: once the client sees 200, nothing
	// published to the session can miss this stream.
	hub := s.sessions.hub
	stream, ok := hub.register(session.Hash())
	if !ok {
		http.Error(w, "server is shutting down", http.StatusServiceUnavailable)
		return
	}
	defer hub.unregister(session.Hash(), stream)

	sw, err := NewSSEWriter(w, r, streamKeepAlive)
	if err != nil {
		if !errors.Is(err, ErrClientGone) {
			http.Error(w, "streaming not supported", http.StatusInternalServerError)
		}
		return
	}
	defer sw.Close()

	last := r.Header.Get("Last-Event-ID")
	if _, _, valid := parseEventID(last); !valid {
		last = ""
	}
	if last != "" {
		var writeErr error
		err := s.sessionEventsAfter(r.Context(), session, last, func(id string, data []byte) {
			if writeErr == nil {
				writeErr = sw.Event(id, "", data)
				last = id
			}
		})
		if writeErr != nil {
			return
		}
		if err != nil {
			// The live stream still works; the client sees a gap.
			slog.Warn("mcp session replay failed", "session_hash", session.Hash(), "error", err)
		}
	}

	refresh := time.NewTicker(max(s.sessions.refreshEvery(), time.Second))
	defer refresh.Stop()
	for {
		select {
		case ev := <-stream.ch:
			if last != "" && ev.id != "" {
				if newer, ok := eventIDLess(last, ev.id); ok && !newer {
					continue // already sent by the replay
				}
			}
			if sw.Event(ev.id, "", ev.data) != nil {
				return
			}
			if ev.id != "" {
				last = ev.id
			}
		case <-refresh.C:
			// An open stream keeps its session alive, and notices a session
			// ended on another replica.
			// A failed write keeps the stream (it is logged); the record's
			// ttl is long enough to outlast a short store outage.
			session.Touch()
			if alive, err := s.sessions.refresh(r.Context(), s.sessionStore(), session, true); err == nil && !alive {
				return
			}
		case <-stream.done:
			return
		case <-sw.Done():
			return
		}
	}
}
