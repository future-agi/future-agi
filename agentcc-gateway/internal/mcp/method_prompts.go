package mcp

import (
	"encoding/json"
	"net/http"
)

// handlePromptsList serves prompts/list through its chain (chain.go).
func (s *Server) handlePromptsList(w http.ResponseWriter, r *http.Request, msg *Message) {
	s.runListOrRead(w, r, msg, promptsListChain, nil, s.writePromptsList)
}

// handlePromptsGet serves prompts/get through its chain (chain.go).
func (s *Server) handlePromptsGet(w http.ResponseWriter, r *http.Request, msg *Message) {
	s.runListOrRead(w, r, msg, promptsGetChain, preparePromptGet, s.writeGetPrompt)
}

// preparePromptGet reads the prompts/get params into the call.
func preparePromptGet(w http.ResponseWriter, cc *CallContext) bool {
	var params GetPromptParams
	if err := json.Unmarshal(cc.msg.Params, &params); err != nil {
		writeJSONRPCError(w, cc.msg.ID, ErrCodeInvalidParams, "invalid prompts/get params")
		return false
	}
	cc.legacy.promptName = params.Name
	cc.legacy.promptArgs = params.Arguments
	return true
}
