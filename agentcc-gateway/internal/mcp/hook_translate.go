package mcp

import (
	"encoding/json"
	"errors"
)

// hookTranslateRetry is step 7 of the tools/call chain (chain.go).
const hookTranslateRetry = "translateRetry"

// translateRetry is step 7 of the tools/call chain and is a no-op by default.
// Keep observeHook as its first statement.
func translateRetry(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookTranslateRetry)
	return nil
}

// isParkedRetry reports whether a tools/call resumes a parked call (an MRTR
// retry carrying a sealed requestState). The chain asks it before step 0 and
// then runs only the steps marked onRetry.
//
// Default: false; no call is parked.
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
