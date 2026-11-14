package server

import (
	"net/http"
	"net/http/httptest"
	"sort"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

const (
	routeTestKey        = "route-test-key"
	routeTestAdminToken = "route-test-admin-token"
)

func newKeyAuthRouteServer(t *testing.T) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "route-test", Key: routeTestKey}}
	cfg.Admin.Token = routeTestAdminToken
	cfg.MCP.Enabled = true
	cfg.A2A.Enabled = true
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	return srv
}

func serveRouteTest(srv *Server, method, path, key string) *httptest.ResponseRecorder {
	request := httptest.NewRequest(method, path, nil)
	if key != "" {
		request.Header.Set("Authorization", "Bearer "+key)
	}
	response := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(response, request)
	return response
}

// keyAuthExempt mirrors the paths KeyAuth leaves to their own handlers:
// health and discovery, and the admin namespace with its own token.
func keyAuthExempt(path string) bool {
	switch path {
	case "/healthz", "/readyz", "/livez", "/.well-known/agent.json":
		return true
	}
	return strings.HasPrefix(path, "/-/")
}

// registeredRoutes lists every route the server registered, with each path
// parameter filled by a probe value.
func registeredRoutes(rt *Router) [][2]string {
	var routes [][2]string
	for key := range rt.static {
		method, path, _ := strings.Cut(key, " ")
		routes = append(routes, [2]string{method, path})
	}
	for _, candidates := range rt.paramIndex {
		for _, route := range candidates {
			segments := make([]string, len(route.parts))
			for i, part := range route.parts {
				if part.isParam {
					segments[i] = "probe"
				} else {
					segments[i] = part.value
				}
			}
			routes = append(routes, [2]string{route.method, "/" + strings.Join(segments, "/")})
		}
	}
	sort.Slice(routes, func(i, j int) bool { return routes[i][1]+routes[i][0] < routes[j][1]+routes[j][0] })
	return routes
}

func TestKeyAuthGuardsEveryRegisteredRoute(t *testing.T) {
	srv := newKeyAuthRouteServer(t)
	guarded := map[string]bool{}
	for _, route := range registeredRoutes(srv.router) {
		method, path := route[0], route[1]
		if keyAuthExempt(path) {
			continue
		}
		guarded[method+" "+path] = true
		for _, key := range []string{"", routeTestAdminToken, "not-a-gateway-key"} {
			response := serveRouteTest(srv, method, path, key)
			if response.Code != http.StatusUnauthorized || !strings.Contains(response.Body.String(), "Invalid or missing API key") {
				t.Errorf("%s %s with key %q: got %d %s", method, path, key, response.Code, response.Body.String())
			}
		}
	}
	// The routes that skipped KeyAuth before it failed closed must be in the walk.
	for _, want := range []string{"POST /mcp", "GET /mcp", "POST /a2a", "POST /v1beta/models/probe", "POST /v1beta/models/probe/probe"} {
		if !guarded[want] {
			t.Errorf("route %s was not registered or not checked", want)
		}
	}
}

func TestKeyAuthGuardsOtherMethodsOnRegisteredPaths(t *testing.T) {
	srv := newKeyAuthRouteServer(t)
	for _, path := range []string{"/mcp", "/a2a"} {
		response := serveRouteTest(srv, http.MethodDelete, path, "")
		if response.Code != http.StatusUnauthorized {
			t.Errorf("DELETE %s without a key: got %d %s", path, response.Code, response.Body.String())
		}
		response = serveRouteTest(srv, http.MethodDelete, path, routeTestKey)
		if response.Code != http.StatusMethodNotAllowed {
			t.Errorf("DELETE %s with a key: got %d %s", path, response.Code, response.Body.String())
		}
	}
}

func TestUnregisteredPathsKeepTheirNotFound(t *testing.T) {
	srv := newKeyAuthRouteServer(t)
	for _, tc := range []struct {
		path string
		key  string
		want int
	}{
		{"/admin/video/capabilities", "", http.StatusNotFound},
		{"/admin/video/capabilities", routeTestAdminToken, http.StatusNotFound},
		{"/admin/video/capabilities", routeTestKey, http.StatusNotFound},
		{"/future-route", "", http.StatusNotFound},
		{"/mcp/extra", "", http.StatusNotFound},
		{"/v1/future-route", "", http.StatusUnauthorized},
		{"/v1/future-route", routeTestAdminToken, http.StatusUnauthorized},
		{"/v1/future-route", routeTestKey, http.StatusNotFound},
	} {
		response := serveRouteTest(srv, http.MethodGet, tc.path, tc.key)
		if response.Code != tc.want {
			t.Errorf("GET %s with key %q: got %d, want %d: %s", tc.path, tc.key, response.Code, tc.want, response.Body.String())
		}
	}
}
