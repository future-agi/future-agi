package mcp

import (
	"net/http"
)

// handleDiscover serves server/discover (2026 era) with the unknown-method
// answer.
func (s *Server) handleDiscover(w http.ResponseWriter, r *http.Request, msg *Message) {
	writeMethodNotFound(w, msg)
}
