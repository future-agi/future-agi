// Package keyscope is the scope and the spend budget a platform-minted key
// carries (CN22c). An Omega EE agent key may call the MCP endpoint and the
// model endpoints (chat completions, embeddings, responses) and nothing else,
// and spends at most its budget per period. Both ride on reserved key
// metadata (auth.reservedMetadataKeys) that only the control plane sets, from
// records no tenant can write, so no tenant write can widen them.
//
// A key with no scope entry is unrestricted, as every key was before. A
// scope or budget the gateway cannot read allows nothing: it fails closed.
package keyscope

import (
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/httppath"
)

// Reserved key metadata names. Django sets them on the keys it mints for
// Omega EE (futureagi/agentcc/services/ee_agent_key_policy.py).
const (
	MetaScopes         = "scopes"
	MetaBudgetLimitUSD = "budget_limit_usd"
	MetaBudgetPeriod   = "budget_period"
)

// The scopes a key may carry, as a comma list in MetaScopes.
const (
	MCP    = "mcp"    // the MCP endpoint and everything under it
	Models = "models" // POST /v1/chat/completions, /v1/embeddings, /v1/responses
)

// modelRoutes are the model endpoints the models scope admits: canonical path
// to method.
var modelRoutes = map[string]string{
	"/v1/chat/completions": "POST",
	"/v1/embeddings":       "POST",
	"/v1/responses":        "POST",
}

// Scope is what a key may call. The zero Scope is unrestricted.
type Scope struct {
	restricted  bool
	mcp, models bool
}

// Parse reads the scope entry of a key's metadata. No entry: unrestricted.
// An entry that is blank, names a scope this gateway does not know, or names
// one twice allows nothing: a scope the gateway cannot read never widens.
func Parse(metadata map[string]string) Scope {
	raw, ok := metadata[MetaScopes]
	if !ok {
		return Scope{}
	}
	none := Scope{restricted: true}
	if strings.TrimSpace(raw) == "" {
		return none
	}
	s := none
	for _, part := range strings.Split(raw, ",") {
		switch strings.TrimSpace(part) {
		case MCP:
			if s.mcp {
				return none
			}
			s.mcp = true
		case Models:
			if s.models {
				return none
			}
			s.models = true
		default:
			return none
		}
	}
	return s
}

// Restricted reports whether the key carries a scope at all.
func (s Scope) Restricted() bool { return s.restricted }

// Allows reports whether a key with this scope may call method on path.
// mcpRoot is the gateway's MCP endpoint ("" when MCP is off). The path is
// judged in its canonical form, as the key gate and the router judge it.
func (s Scope) Allows(method, path, mcpRoot string) bool {
	if !s.restricted {
		return true
	}
	c := httppath.Canonical(path)
	if s.mcp && mcpRoot != "" {
		root := httppath.Canonical(mcpRoot)
		if c == root || strings.HasPrefix(c, root+"/") {
			return true
		}
	}
	if s.models {
		if m, ok := modelRoutes[c]; ok && m == method {
			return true
		}
	}
	return false
}

// describe names what the scope admits, for a refusal's message.
func (s Scope) describe() string {
	var parts []string
	if s.mcp {
		parts = append(parts, "the MCP endpoint")
	}
	if s.models {
		parts = append(parts, "POST /v1/chat/completions, /v1/embeddings and /v1/responses")
	}
	if len(parts) == 0 {
		return "nothing (its scope cannot be read)"
	}
	return strings.Join(parts, " and ")
}
