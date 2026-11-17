package mcp

import (
	"encoding/json"
	"errors"
)

// hookTranslateRetry is step 7 of the tools/call chain (chain.go).
const hookTranslateRetry = "translateRetry"

// translateRetry is filled by the translation step so that, on a request that resumes a
// parked call, it checks the handle's binding (org, principal, virtual
// server, server, tool, tool_call_id, argument hash), adopts the parked call
// (cc.Adopt) and sets cc.Result to the resumed upstream result, refusing
// with request_state_*; the chain then skips to scanResult. Any other call
// passes through. Until then it does nothing. Keep observeHook as its first statement.
func translateRetry(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookTranslateRetry)
	return nil
}

// isParkedRetry reports whether a tools/call resumes a parked call (an MRTR
// retry carrying a sealed requestState). The chain asks it before step 0 and
// then runs only the steps marked onRetry.
//
// Default: false, as nothing is ever parked.
func isParkedRetry(_ *Server, _ *CallContext) bool {
	return false
}

// declineElicitation is Deps.Translator's default: it declines every
// elicitation and refuses any other server-initiated request.
type declineElicitation struct{}

func (declineElicitation) ServerRequest(_ *CallContext, method string, _ json.RawMessage) (json.RawMessage, error) {
	if method == "elicitation/create" {
		return json.RawMessage(`{"action":"decline"}`), nil
	}
	return nil, errors.New("mcp: server-initiated request " + method + " is not supported")
}
