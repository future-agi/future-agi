package server

import (
	"slices"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// TestAPIRouteRootsCoverEveryRoute keeps config.APIRouteRoots, the roots an
// mcp.endpoint may not be under, equal to the roots
// of the router's routes, with every optional surface on, so a route added
// under a new root cannot be swallowed by an MCP endpoint config load accepts.
func TestAPIRouteRootsCoverEveryRoute(t *testing.T) {
	srv := newCanonicalPathTestServer(t)
	mcp := mcpEndpoint(srv.cfg)
	seen := map[string]bool{}
	for _, r := range srv.router.Routes() {
		if r.Pattern == mcp || strings.HasPrefix(r.Pattern, mcp+"/") {
			continue
		}
		root := "/" + strings.SplitN(strings.TrimPrefix(r.Pattern, "/"), "/", 2)[0]
		seen[root] = true
		if !slices.Contains(config.APIRouteRoots, root) {
			t.Errorf("route %s %s is under %s, which config.APIRouteRoots does not list", r.Method, r.Pattern, root)
		}
	}
	for _, root := range config.APIRouteRoots {
		if !seen[root] {
			t.Errorf("config.APIRouteRoots lists %s, which no route uses", root)
		}
	}
}
