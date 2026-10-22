package mcp

import (
	"fmt"
	"net/http"
	"time"
)

// HandleSSE handles GET /mcp — opens an SSE stream for server-to-client notifications.
func (s *Server) HandleSSE(w http.ResponseWriter, r *http.Request) {
	r, ok := s.authenticateHTTP(w, r)
	if !ok {
		return
	}

	// Validate session. Another key's session answers 404, like an unknown
	// one, and its stream is left attached to its owner.
	sessionID := r.Header.Get("MCP-Session-Id")
	if sessionID == "" {
		http.Error(w, "missing MCP-Session-Id", http.StatusBadRequest)
		return
	}
	session := s.sessions.Get(sessionID, sessionOwner(r.Context()))
	if session == nil {
		http.Error(w, "invalid or expired session", http.StatusNotFound)
		return
	}

	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming not supported", http.StatusInternalServerError)
		return
	}

	// Set SSE headers.
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.WriteHeader(http.StatusOK)
	flusher.Flush()

	// Register SSE channel.
	ch := make(chan []byte, 32)
	s.sseMu.Lock()
	s.sseClients[sessionID] = ch
	s.sseMu.Unlock()

	defer func() {
		s.sseMu.Lock()
		delete(s.sseClients, sessionID)
		s.sseMu.Unlock()
	}()

	// Stream events.
	keepalive := time.NewTicker(15 * time.Second)
	defer keepalive.Stop()

	for {
		select {
		case data, ok := <-ch:
			if !ok {
				return
			}
			fmt.Fprintf(w, "data: %s\n\n", data)
			flusher.Flush()

		case <-keepalive.C:
			// SSE comment as keepalive.
			fmt.Fprint(w, ": keepalive\n\n")
			flusher.Flush()
			// Touch session to keep it alive.
			session.Touch()

		case <-r.Context().Done():
			return
		}
	}
}
