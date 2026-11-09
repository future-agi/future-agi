package server

import "strings"

// unknownEndpointMessage is the 404's message for a canonical path no route
// serves. The two base_url mistakes SDK users make get a hint, named from
// the route table: a path missing its /v1 (an OpenAI SDK whose base_url is
// the gateway's root) and a path with /v1 twice (an Anthropic SDK, which adds
// /v1 itself, given a base_url ending in /v1). It names routes only, so it
// tells a caller without a key nothing more than the endpoints the gateway
// documents.
func (rt *Router) unknownEndpointMessage(method, path string) string {
	msg := "Unknown endpoint: " + path
	if rest, ok := strings.CutPrefix(path, "/v1/v1/"); ok {
		fixed := "/v1/" + rest
		if !rt.hasRoute(method, fixed) {
			return msg + "."
		}
		if fixed == "/v1/messages" || strings.HasPrefix(fixed, "/v1/messages/") {
			return msg + ". The path has /v1 twice: the Anthropic SDK adds /v1 itself, so set its base_url to the gateway URL without /v1."
		}
		return msg + ". The path has /v1 twice: did you mean " + fixed + "? Set your SDK's base_url so it ends in /v1 only once."
	}
	if !strings.HasPrefix(path, "/v1/") && rt.hasRoute(method, "/v1"+path) {
		return msg + ". Did you mean /v1" + path + "? Set your OpenAI SDK's base_url to the gateway URL ending in /v1."
	}
	return msg + "."
}

// hasRoute reports whether a route serves method on path, a canonical path.
func (rt *Router) hasRoute(method, path string) bool {
	if _, ok := rt.static[method+" "+path]; ok {
		return true
	}
	pathParts := strings.Split(strings.Trim(path, "/"), "/")
	for _, route := range rt.paramIndex[paramKey{segments: len(pathParts), method: method}] {
		if _, ok := matchParts(route.parts, pathParts); ok {
			return true
		}
	}
	return false
}
