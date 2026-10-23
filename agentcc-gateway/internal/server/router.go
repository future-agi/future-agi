package server

import (
	"net/http"
	"net/url"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/httppath"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Route represents a registered route (kept for external access).
type Route struct {
	Method  string
	Pattern string
	Handler http.HandlerFunc
}

// routePart is a pre-parsed segment of a URL pattern.
type routePart struct {
	value   string // literal value, or param name if isParam
	isParam bool
}

// paramRoute is a parametric route with pre-parsed parts.
type paramRoute struct {
	method  string
	parts   []routePart
	handler http.HandlerFunc
}

// paramKey indexes parametric routes by segment count and HTTP method.
type paramKey struct {
	segments int
	method   string
}

// Router is a fast HTTP router using O(1) static lookup and segment-indexed parametric matching.
type Router struct {
	// O(1) lookup for static routes: "METHOD /path" → handler.
	static map[string]http.HandlerFunc

	// Parametric routes indexed by (segment count, method) for fast narrowing.
	paramIndex map[paramKey][]paramRoute

	// Track which paths exist (for 405 detection).
	// Key is the normalized path pattern.
	knownPaths map[string]bool

	// Every registration, in order, so callers (and tests) can enumerate the
	// route table instead of keeping a hand list in sync with it.
	routes []Route
}

// NewRouter creates a new router.
func NewRouter() *Router {
	return &Router{
		static:     make(map[string]http.HandlerFunc, 64),
		paramIndex: make(map[paramKey][]paramRoute, 32),
		knownPaths: make(map[string]bool, 64),
	}
}

// Handle registers a route.
func (rt *Router) Handle(method, pattern string, handler http.HandlerFunc) {
	// ServeHTTP serves only canonical paths, so a pattern is registered in its
	// canonical form: an operator-configured "/mcp/" registers as "/mcp".
	pattern = httppath.Canonical(pattern)
	rt.knownPaths[pattern] = true
	rt.routes = append(rt.routes, Route{Method: method, Pattern: pattern, Handler: handler})

	// Check if the pattern contains any parameters.
	if !strings.Contains(pattern, "{") {
		// Static route — O(1) lookup.
		key := method + " " + pattern
		rt.static[key] = handler
		return
	}

	// Parametric route — pre-parse and index by segment count.
	trimmed := strings.Trim(pattern, "/")
	rawParts := strings.Split(trimmed, "/")
	parts := make([]routePart, len(rawParts))
	for i, p := range rawParts {
		if len(p) > 1 && p[0] == '{' && p[len(p)-1] == '}' {
			parts[i] = routePart{value: p[1 : len(p)-1], isParam: true}
		} else {
			parts[i] = routePart{value: p}
		}
	}

	pk := paramKey{segments: len(parts), method: method}
	rt.paramIndex[pk] = append(rt.paramIndex[pk], paramRoute{
		method:  method,
		parts:   parts,
		handler: handler,
	})
}

// Routes returns every registered route in registration order. The slice is a
// copy; changing it does not change the router.
func (rt *Router) Routes() []Route {
	out := make([]Route, len(rt.routes))
	copy(out, rt.routes)
	return out
}

// ServeHTTP dispatches the request to the matching route.
//
// A path that is not canonical ("//v1/x", "/v1/x/", "/./v1/x", "/a/../v1/x")
// is rejected before any matching. The gates in front of the router decide on
// the canonical path; refusing every other spelling here means no handler ever
// runs on a path those gates did not see.
func (rt *Router) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Path
	method := r.Method

	if !httppath.IsCanonical(path) {
		models.WriteError(w, models.ErrNotFound("non_canonical_path",
			"Unknown endpoint: "+path+" is not a canonical path; use "+httppath.Canonical(path)))
		return
	}

	// Phase 1: Try static O(1) lookup.
	if handler, ok := rt.static[method+" "+path]; ok {
		handler(w, r)
		return
	}

	// Phase 2: Try parametric routes indexed by segment count + method.
	trimmed := strings.Trim(path, "/")
	pathParts := strings.Split(trimmed, "/")
	segCount := len(pathParts)

	pk := paramKey{segments: segCount, method: method}
	if candidates, ok := rt.paramIndex[pk]; ok {
		for i := range candidates {
			route := &candidates[i]
			params, ok := matchParts(route.parts, pathParts)
			if !ok {
				continue
			}

			// Store path params in query string (matches existing handler expectations).
			if len(params) > 0 {
				setPathParams(r, params)
			}

			route.handler(w, r)
			return
		}
	}

	// Phase 3: Check for method-not-allowed (path matches but different method).
	if rt.isMethodNotAllowed(path, method, pathParts, segCount) {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusMethodNotAllowed,
			Type:    models.ErrTypeInvalidRequest,
			Code:    "method_not_allowed",
			Message: "Method not allowed for this endpoint",
		})
		return
	}

	models.WriteError(w, models.ErrNotFound("not_found", "Unknown endpoint: "+path))
}

// isMethodNotAllowed checks if the path exists for a different method.
func (rt *Router) isMethodNotAllowed(path, method string, pathParts []string, segCount int) bool {
	// Check static routes for other methods.
	methods := [...]string{"GET", "POST", "PUT", "DELETE", "PATCH"}
	for _, m := range methods {
		if m == method {
			continue
		}
		if _, ok := rt.static[m+" "+path]; ok {
			return true
		}
	}

	// Check parametric routes for other methods.
	for _, m := range methods {
		if m == method {
			continue
		}
		pk := paramKey{segments: segCount, method: m}
		if candidates, ok := rt.paramIndex[pk]; ok {
			for i := range candidates {
				if _, ok := matchParts(candidates[i].parts, pathParts); ok {
					return true
				}
			}
		}
	}

	return false
}

// matchParts matches pre-parsed route parts against path segments.
// Returns extracted params on match, nil on mismatch.
func matchParts(parts []routePart, pathParts []string) (map[string]string, bool) {
	// Length already guaranteed by segment-count indexing.
	var params map[string]string

	for i, part := range parts {
		if part.isParam {
			if params == nil {
				params = make(map[string]string, 2) // most routes have 1-2 params
			}
			params[part.value] = pathParts[i]
			continue
		}
		if part.value != pathParts[i] {
			return nil, false
		}
	}

	return params, true
}

// setPathParams efficiently encodes path params into the request's query string.
// Fast path: if no existing query string, build it directly without parsing.
func setPathParams(r *http.Request, params map[string]string) {
	if r.URL.RawQuery == "" {
		// Fast path: no existing query params — build directly.
		var b strings.Builder
		first := true
		for k, v := range params {
			if !first {
				b.WriteByte('&')
			}
			b.WriteString(url.QueryEscape(k))
			b.WriteByte('=')
			b.WriteString(url.QueryEscape(v))
			first = false
		}
		r.URL.RawQuery = b.String()
	} else {
		// Slow path: merge with existing query params.
		q := r.URL.Query()
		for k, v := range params {
			q.Set(k, v)
		}
		r.URL.RawQuery = q.Encode()
	}
}
