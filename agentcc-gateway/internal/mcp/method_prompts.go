package mcp

import (
	"encoding/json"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/mcp/vserver"
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

// prompt is a composed prompt as clients see it: the member's upstream
// prompt under its exposed name.
func (v *callView) prompt(e vserver.Entry) Prompt {
	for _, p := range v.source.MemberPrompts(e.Member.Server) {
		if p.Name == e.Name {
			p.Name, p.Raw = e.Exposed, editRaw(p.Raw, map[string]string{"name": e.Exposed})
			return p
		}
	}
	return Prompt{Name: e.Exposed, Description: e.Description}
}
