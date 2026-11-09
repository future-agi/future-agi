package server

import (
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// registerMCPVSRoutes registers POST, GET and DELETE {endpoint}/v/{vs_id}:
// each serves one virtual server (decision 32), resolved in the caller's org
// only, through the endpoint's own handlers. The router hands {vs_id} over in
// the query string; the handler moves it into the request's virtual-server
// marker, the only thing the MCP server reads, so a vs_id query parameter on
// the endpoint itself selects nothing. Owner: M10.
func (s *Server) registerMCPVSRoutes(router *Router) {
	path := mcpEndpoint(s.cfg) + "/v/{vs_id}"
	router.Handle("POST", path, virtualServerHandler(s.mcp.HandlePost))
	router.Handle("GET", path, virtualServerHandler(s.mcp.HandleSSE))
	router.Handle("DELETE", path, virtualServerHandler(s.mcp.HandleDelete))
}

// virtualServerHandler marks the request with its {vs_id} and serves it.
func virtualServerHandler(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		vsID := q.Get("vs_id")
		if vsID == "" {
			http.NotFound(w, r)
			return
		}
		q.Del("vs_id")
		r.URL.RawQuery = q.Encode()
		next(w, mcp.WithVirtualServer(r, vsID))
	}
}
