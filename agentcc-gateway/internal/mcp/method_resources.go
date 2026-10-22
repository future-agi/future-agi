package mcp

import (
	"encoding/json"
	"net/http"
)

// handleResourcesList serves resources/list through its chain (chain.go).
func (s *Server) handleResourcesList(w http.ResponseWriter, r *http.Request, msg *Message) {
	s.runListOrRead(w, r, msg, resourcesListChain, nil, s.writeResourcesList)
}

// handleResourcesRead serves resources/read through its chain (chain.go).
func (s *Server) handleResourcesRead(w http.ResponseWriter, r *http.Request, msg *Message) {
	s.runListOrRead(w, r, msg, resourcesReadChain, prepareResourceRead, s.writeReadResource)
}

// prepareResourceRead reads the resources/read params into the call.
func prepareResourceRead(w http.ResponseWriter, cc *CallContext) bool {
	var params ReadResourceParams
	if err := json.Unmarshal(cc.msg.Params, &params); err != nil {
		writeJSONRPCError(w, cc.msg.ID, ErrCodeInvalidParams, "invalid resources/read params")
		return false
	}
	cc.legacy.resourceURI = params.URI
	return true
}

// handleResourceTemplatesList serves resources/templates/list with the
// unknown-method answer.
func (s *Server) handleResourceTemplatesList(w http.ResponseWriter, r *http.Request, msg *Message) {
	writeMethodNotFound(w, msg)
}

// handleResourcesSubscribe serves resources/subscribe and
// resources/unsubscribe with the unknown-method answer.
func (s *Server) handleResourcesSubscribe(w http.ResponseWriter, r *http.Request, msg *Message) {
	writeMethodNotFound(w, msg)
}
