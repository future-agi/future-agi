package mcp

import (
	"log/slog"
	"net/http"
)

// HandleDelete handles DELETE on the MCP endpoint: it ends the legacy
// session MCP-Session-Id names (204), its event log and its open streams, on
// every replica, so a later request on it gets 404 and the client
// initializes again. Like every request on a session, it must come from the
// session's principal; any other gets the unknown-session 404.
func (s *Server) HandleDelete(w http.ResponseWriter, r *http.Request) {
	r, ok := s.authenticateHTTP(w, admissionRoute(r))
	if !ok {
		return
	}
	defer releaseAdmissionIngress(r)
	if refuseStatelessStream(w, r) {
		// No MCP-Session-Id: 405 with Allow: POST, as GET.
		return
	}
	if s.hostedAdmissionRequired() {
		s.handleAdmittedDelete(w, r)
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
	if err := s.sessions.end(r.Context(), s.sessionStore(), session); err != nil {
		http.Error(w, "session store unavailable", http.StatusServiceUnavailable)
		return
	}
	slog.Info("mcp session ended", "session_hash", session.Hash())
	w.WriteHeader(http.StatusNoContent)
}
