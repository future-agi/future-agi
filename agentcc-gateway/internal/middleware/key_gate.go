package middleware

import (
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/httppath"
)

// keyGate decides which request paths KeyAuth requires an API key on: every
// /v1 path, and every path at or under one of its roots: the MCP endpoint and
// /a2a, which the gateway passes in.
//
// Like isV1Path it decides on the canonical path, so every spelling of a path
// is gated as its canonical form. A root covers its whole subtree and every
// method, so a caller without a key learns nothing from which paths or
// methods exist under it.
type keyGate struct {
	roots []string // canonical
}

func newKeyGate(roots []string) keyGate {
	g := keyGate{roots: make([]string, 0, len(roots))}
	for _, root := range roots {
		if root == "" {
			continue
		}
		g.roots = append(g.roots, httppath.Canonical(root))
	}
	return g
}

// covers reports whether a request on path p needs an API key.
func (g keyGate) covers(p string) bool {
	if isV1Path(p) {
		return true
	}
	c := httppath.Canonical(p)
	for _, root := range g.roots {
		if c == root || strings.HasPrefix(c, root+"/") {
			return true
		}
	}
	return false
}
