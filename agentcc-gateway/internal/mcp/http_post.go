package mcp

import (
	"encoding/json"
	"net/http"
)

// HandlePost handles POST /mcp — the main JSON-RPC endpoint.
func (s *Server) HandlePost(w http.ResponseWriter, r *http.Request) {
	req, ok := s.authenticateHTTP(w, r)
	if !ok {
		return
	}

	// Parse the JSON-RPC message.
	var msg Message
	if err := json.NewDecoder(req.Body).Decode(&msg); err != nil {
		writeJSONRPCError(w, nil, ErrCodeParse, "invalid JSON: "+err.Error())
		return
	}

	if msg.JSONRPC != "2.0" {
		writeJSONRPCError(w, msg.ID, ErrCodeInvalidRequest, "expected jsonrpc 2.0")
		return
	}

	s.dispatch(w, req, &msg)
}
