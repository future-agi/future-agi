package server

import "net/http"

// registerMCPProbeRoutes registers POST /-/mcp/probe: an admin-token
// connection test for an upstream MCP URL. It connects through the egress
// guard, detects the era, lists the tools and returns mcp-probe-v1; it
// stores nothing.
func (s *Server) registerMCPProbeRoutes(router *Router) {
	router.Handle("POST", "/-/mcp/probe", func(w http.ResponseWriter, r *http.Request) {
		if !s.checkAdminAuth(w, r) {
			return
		}
		s.mcp.HandleProbe(w, r)
	})
}
