package mcp

import (
	"net/http"
)

// handleListen serves subscriptions/listen (2026 era): not yet supported, so
// the unknown-method answer.
//
// Owner: M12x.
func (s *Server) handleListen(w http.ResponseWriter, r *http.Request, msg *Message) {
	writeMethodNotFound(w, msg)
}
