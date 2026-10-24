package mcp

import (
	"context"
	"errors"
	"strings"
)

// hookResolvePrincipal is step 1 of every chain (chain.go).
const hookResolvePrincipal = "resolvePrincipal"

// resolvePrincipal is step 1 of every chain and is a no-op by default.
// authenticateHTTP checks the API key on POST, GET and DELETE of the MCP
// endpoint whenever per-key auth is set. Keep observeHook as its first
// statement.
func resolvePrincipal(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookResolvePrincipal)
	return nil
}

// errJWTRejected is the default verifier's answer to a JWT: no issuer is
// configured.
var errJWTRejected = errors.New("mcp: JWT bearer tokens are not accepted on this gateway")

// rejectJWTs is the default BearerVerifier: it rejects every JWT-shaped
// token and leaves anything else to key authentication.
type rejectJWTs struct{}

func (rejectJWTs) Verify(_ context.Context, token, _ string) (*Principal, error) {
	if strings.Count(token, ".") == 2 && strings.HasPrefix(token, "eyJ") {
		return nil, errJWTRejected
	}
	return nil, nil
}
