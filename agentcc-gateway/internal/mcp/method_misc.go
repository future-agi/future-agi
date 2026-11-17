package mcp

import (
	"net/http"
)

// handlePing serves ping on legacy requests only: 2026-07-28 removed it, so
// a stateless ping gets the unknown-method answer (HTTP 404 there). The
// frozen method table passes no request, so the era comes from the writer.
func (s *Server) handlePing(w http.ResponseWriter, msg *Message) {
	if pw := asProtocolWriter(w); pw != nil && pw.pr.era.Stateless {
		writeMethodNotFound(w, msg)
		return
	}
	writeJSONRPCResult(w, msg.ID, struct{}{})
}

// handleNotification serves a notification method sent with an id, which
// HandlePost dispatches (a notification without one gets its 202 there):
// initialized and cancelled are accepted with 202, as before; the others get
// the unknown-method answer.
func (s *Server) handleNotification(w http.ResponseWriter, r *http.Request, msg *Message) {
	switch msg.Method {
	case MethodInitialized, MethodCancelled:
		w.WriteHeader(http.StatusAccepted)
	default:
		writeMethodNotFound(w, msg)
	}
}

// handleCompletion serves completion/complete: not yet supported.
func (s *Server) handleCompletion(w http.ResponseWriter, r *http.Request, msg *Message) {
	writeMethodNotFound(w, msg)
}

// handleSetLevel serves logging/setLevel: not yet supported, so legacy
// sessions get no log messages, as before; 2026-07-28 removed it in favour of
// the per-request _meta logLevel.
func (s *Server) handleSetLevel(w http.ResponseWriter, r *http.Request, msg *Message) {
	writeMethodNotFound(w, msg)
}

// handleTasks serves the tasks/* methods: not yet supported; task-shaped
// results pass through untouched.
func (s *Server) handleTasks(w http.ResponseWriter, r *http.Request, msg *Message) {
	writeMethodNotFound(w, msg)
}
