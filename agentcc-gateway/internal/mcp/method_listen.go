package mcp

import (
	"net/http"
)

// handleListen serves subscriptions/listen (2026 era) with the unknown-method
// answer.
func (s *Server) handleListen(w http.ResponseWriter, r *http.Request, msg *Message) {
	writeMethodNotFound(w, msg)
}
