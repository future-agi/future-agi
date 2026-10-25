package mcp

import (
	"net/http"
)

// handleToolsList serves tools/list through its chain (chain.go).
func (s *Server) handleToolsList(w http.ResponseWriter, r *http.Request, msg *Message) {
	s.runListOrRead(w, r, msg, toolsListChain, nil, s.writeToolsList)
}

// handleToolsCall serves tools/call through its chain (chain.go).
func (s *Server) handleToolsCall(w http.ResponseWriter, r *http.Request, msg *Message) {
	s.runToolsCall(w, r, msg)
}
