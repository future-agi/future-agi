package server

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/metrics"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// The key gate and the router read the path the same way. These tests drive
// every registered route through the full middleware chain, so a route added
// later is covered without editing a hand list.

const (
	canonAPIKey     = "sk-canonical-path-key"
	canonAdminToken = "canonical-path-admin-token"
)

// newCanonicalPathTestServer builds a server with request auth on and every
// optional route group enabled, so the route table is as large as it gets.
func newCanonicalPathTestServer(t *testing.T) *Server {
	t.Helper()
	mock := startMockOpenAI(t)
	t.Cleanup(mock.Close)

	cfg := config.DefaultConfig()
	cfg.Admin.Token = canonAdminToken
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name:    "canonical-path",
		Key:     canonAPIKey,
		Owner:   "test-org",
		KeyType: "internal",
	}}
	cfg.A2A.Enabled = true
	cfg.A2A.Card = config.A2ACardConfig{Name: "canonical", Description: "canonical", Version: "1.0.0"}
	cfg.MCP.Enabled = true
	cfg.Cluster.Enabled = true
	cfg.Edge.Enabled = true
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL:   mock.URL,
		APIFormat: "openai",
		Models:    []string{"gpt-4o"},
	}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, metrics.NewRegistry(), testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	return srv
}

// concretePath fills every {param} in a route pattern with a placeholder.
func concretePath(pattern string) string {
	parts := strings.Split(pattern, "/")
	for i, p := range parts {
		if strings.HasPrefix(p, "{") && strings.HasSuffix(p, "}") {
			parts[i] = "canon-" + p[1:len(p)-1]
		}
	}
	return strings.Join(parts, "/")
}

// nonCanonicalVariants returns spellings of a canonical path that a path
// cleaner would fold back into it: doubled slashes, a trailing slash, dot
// and dot-dot segments, and an encoded slash.
func nonCanonicalVariants(c string) map[string]string {
	i := strings.Index(c[1:], "/") + 1 // end of the first segment
	if i == 0 {
		i = len(c)
	}
	rest := c[i:]
	return map[string]string{
		"leading double slash":  "/" + c,
		"leading triple slash":  "//" + c,
		"inner double slash":    c[:i] + "/" + rest,
		"trailing slash":        c + "/",
		"leading dot segment":   "/." + c,
		"inner dot segment":     c[:i] + "/." + rest,
		"trailing dot segment":  c + "/.",
		"leading dot-dot":       "/zz/.." + c,
		"inner dot-dot":         c[:i] + "/zz/.." + rest,
		"encoded leading slash": "/%2F" + c[1:],
		"encoded inner slash":   c[:i] + "/%2F" + strings.TrimPrefix(rest, "/"),
	}
}

func serveCanonical(t *testing.T, srv *Server, method, target, authorization string) *httptest.ResponseRecorder {
	t.Helper()
	var body *bytes.Reader
	if method == http.MethodPost || method == http.MethodPut || method == http.MethodPatch {
		body = bytes.NewReader([]byte(`{}`))
	} else {
		body = bytes.NewReader(nil)
	}
	req := httptest.NewRequest(method, target, body)
	if body.Len() > 0 {
		req.Header.Set("Content-Type", "application/json")
	}
	if authorization != "" {
		req.Header.Set("Authorization", authorization)
	}
	ctx, cancel := context.WithTimeout(req.Context(), 5*time.Second)
	defer cancel()
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req.WithContext(ctx))
	return w
}

func registeredRoutes(t *testing.T, srv *Server, prefix string) []Route {
	t.Helper()
	var out []Route
	for _, r := range srv.router.Routes() {
		if strings.HasPrefix(r.Pattern, prefix) {
			out = append(out, r)
		}
	}
	return out
}

// requireExemplars guards against an enumeration that silently comes back
// short (a renamed field, a route group that stopped registering).
func requireExemplars(t *testing.T, routes []Route, want ...string) {
	t.Helper()
	have := make(map[string]bool, len(routes))
	for _, r := range routes {
		have[r.Method+" "+r.Pattern] = true
	}
	for _, w := range want {
		if !have[w] {
			t.Fatalf("route table is missing %q; enumeration is broken", w)
		}
	}
}

func TestEveryV1RouteRequiresAKeyOnEverySpellingOfItsPath(t *testing.T) {
	srv := newCanonicalPathTestServer(t)
	routes := registeredRoutes(t, srv, "/v1/")
	requireExemplars(t, routes,
		"GET /v1/videos/{video_id}",
		"GET /v1/files/{file_id}/content",
		"GET /v1/async/{job_id}",
		"GET /v1/responses/{id}",
		"POST /v1/chat/completions",
		"POST /v1/a2a",
	)

	for _, rt := range routes {
		canonical := concretePath(rt.Pattern)
		targets := nonCanonicalVariants(canonical)
		targets["canonical"] = canonical
		for name, target := range targets {
			t.Run(rt.Method+" "+rt.Pattern+"/"+name, func(t *testing.T) {
				w := serveCanonical(t, srv, rt.Method, target, "")
				if w.Code != http.StatusUnauthorized {
					t.Fatalf("%s %s with no key: status %d, want 401. Body: %s",
						rt.Method, target, w.Code, w.Body.String())
				}
			})
		}
	}
}

func TestEveryV1RouteServesAnAuthenticatedCanonicalRequest(t *testing.T) {
	srv := newCanonicalPathTestServer(t)
	routes := registeredRoutes(t, srv, "/v1/")
	requireExemplars(t, routes, "GET /v1/videos/{video_id}", "GET /v1/models")

	for _, rt := range routes {
		target := concretePath(rt.Pattern)
		t.Run(rt.Method+" "+rt.Pattern, func(t *testing.T) {
			w := serveCanonical(t, srv, rt.Method, target, "Bearer "+canonAPIKey)
			body := w.Body.String()
			if w.Code == http.StatusUnauthorized {
				t.Fatalf("%s %s with a valid key: 401. Body: %s", rt.Method, target, body)
			}
			for _, routerMiss := range []string{"Unknown endpoint", "non_canonical_path", "method_not_allowed"} {
				if strings.Contains(body, routerMiss) {
					t.Fatalf("%s %s with a valid key never reached its handler (%s). Status %d, body: %s",
						rt.Method, target, routerMiss, w.Code, body)
				}
			}
		})
	}

	// One end-to-end success, so "reached the handler" is not the only proof.
	w := serveCanonical(t, srv, http.MethodGet, "/v1/models", "Bearer "+canonAPIKey)
	if w.Code != http.StatusOK {
		t.Fatalf("GET /v1/models with a valid key: status %d, want 200. Body: %s", w.Code, w.Body.String())
	}
}

// The router must refuse a non-canonical path before matching, whatever the
// credentials, so no handler ever runs on a path the gates did not see.
func TestRouterRejectsEveryNonCanonicalSpellingOfEveryRoute(t *testing.T) {
	srv := newCanonicalPathTestServer(t)
	routes := srv.router.Routes()
	requireExemplars(t, routes,
		"GET /v1/videos/{video_id}",
		"GET /-/keys/{key_id}",
		"PUT /-/orgs/{org_id}/config",
		"GET /healthz",
	)

	for _, rt := range routes {
		authorization := "Bearer " + canonAPIKey
		if strings.HasPrefix(rt.Pattern, "/-/") {
			authorization = "Bearer " + canonAdminToken
		}
		canonical := concretePath(rt.Pattern)
		for name, target := range nonCanonicalVariants(canonical) {
			t.Run(rt.Method+" "+rt.Pattern+"/"+name, func(t *testing.T) {
				w := serveCanonical(t, srv, rt.Method, target, authorization)
				if w.Code != http.StatusNotFound || !strings.Contains(w.Body.String(), "non_canonical_path") {
					t.Fatalf("%s %s with valid credentials: status %d, want 404 non_canonical_path. Body: %s",
						rt.Method, target, w.Code, w.Body.String())
				}
			})
		}
	}
}

// Admin routes are gated inside each handler, not by a path prefix, so the
// canonical path must answer 401 without the admin token, and no other
// spelling may reach the handler at all (covered above with the token).
func TestEveryAdminRouteRequiresTheAdminToken(t *testing.T) {
	srv := newCanonicalPathTestServer(t)
	routes := registeredRoutes(t, srv, "/-/")
	requireExemplars(t, routes,
		"GET /-/keys/{key_id}",
		"POST /-/keys/sync",
		"PUT /-/orgs/{org_id}/config",
		"POST /-/admin/providers/{id}/rotate",
		"GET /-/mcp/status",
	)

	for _, rt := range routes {
		canonical := concretePath(rt.Pattern)
		targets := nonCanonicalVariants(canonical)
		targets["canonical"] = canonical
		for name, target := range targets {
			for label, authorization := range map[string]string{"no credential": "", "api key": "Bearer " + canonAPIKey} {
				t.Run(rt.Method+" "+rt.Pattern+"/"+name+"/"+label, func(t *testing.T) {
					w := serveCanonical(t, srv, rt.Method, target, authorization)
					if name == "canonical" {
						if w.Code != http.StatusUnauthorized {
							t.Fatalf("%s %s without the admin token: status %d, want 401. Body: %s",
								rt.Method, target, w.Code, w.Body.String())
						}
						return
					}
					if w.Code != http.StatusNotFound || !strings.Contains(w.Body.String(), "non_canonical_path") {
						t.Fatalf("%s %s without the admin token: status %d, want 404 non_canonical_path. Body: %s",
							rt.Method, target, w.Code, w.Body.String())
					}
				})
			}
		}
	}
}
