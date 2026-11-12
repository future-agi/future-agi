package server

import (
	"context"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
)

// licenseAuthorizedContext is a context a managed-service license token
// authorized: LicenseAuth's own record of the token, carried as an async job
// carries it (middleware.WithAuthorizationOf).
func licenseAuthorizedContext(t *testing.T) context.Context {
	t.Helper()
	token, publicPEM := signLicenseToken(t, "turing_small")
	var admitted context.Context
	gate := middleware.LicenseAuth(config.LicenseAuthConfig{Enabled: true, PublicKey: publicPEM}, nil)(
		http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) { admitted = r.Context() }))
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(`{"model":"turing_small"}`))
	req.Header.Set("Authorization", "Bearer "+token)
	w := httptest.NewRecorder()
	gate.ServeHTTP(w, req)
	if admitted == nil || !middleware.IsLicenseAuthorized(admitted) {
		t.Fatalf("LicenseAuth did not authorize the token: status %d: %s", w.Code, w.Body)
	}
	return middleware.WithAuthorizationOf(context.Background(), admitted)
}

// doLicensed sends a request that ctx's license authorized, with no API key.
func (g *asstGateway) doLicensed(ctx context.Context, method, path, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, path, strings.NewReader(body)).WithContext(ctx)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// TestAssistantsHoldLicenseAuthorizedRequestsToTheInternalGroup: a request a
// license authorized has no key and so no access groups of its own; chat
// holds it to the gateway's "internal" access group (resolveRoute). The
// Assistants API holds it to the same group, with chat's answer, before the
// provider is called: on a gateway whose "internal" group holds only gpt-4o,
// every Assistants request that names gpt-4o-mini gets chat's 403 and makes
// no upstream call, and an alias the group defines is sent as the model it
// stands for.
func TestAssistantsHoldLicenseAuthorizedRequestsToTheInternalGroup(t *testing.T) {
	key := asstKeyOpts{groups: config.AccessGroupsConfig{
		"internal": {Models: []string{"gpt-4o"}, Aliases: map[string]string{"fast": "gpt-4o"}},
	}}
	licensed := licenseAuthorizedContext(t)

	chatGW := newAsstGateway(t, key, false)
	chat := chatGW.doLicensed(licensed, "POST", "/v1/chat/completions",
		`{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}`)
	want := errorObject(t, chat)
	if msg, _ := want["message"].(string); chat.Code != http.StatusForbidden || !strings.HasPrefix(msg, `Model "gpt-4o-mini" is not available for this API key.`) {
		t.Fatalf("licensed chat with gpt-4o-mini: status %d, message %q; want chat's access-group 403", chat.Code, msg)
	}
	if n := chatGW.up.calls.Load(); n != 0 {
		t.Fatalf("licensed chat with gpt-4o-mini: %d upstream calls, want none", n)
	}

	for _, e := range asstModelRequests("gpt-4o-mini") {
		t.Run(e.name+" / gpt-4o-mini", func(t *testing.T) {
			g := newAsstGateway(t, key, false)
			w := g.doLicensed(licensed, e.method, e.path, e.body)
			if w.Code != chat.Code || g.up.calls.Load() != 0 {
				t.Fatalf("status %d, upstream calls %d (upstream got %s); want chat's %d and none: %s",
					w.Code, g.up.calls.Load(), g.up.lastBody(), chat.Code, w.Body)
			}
			if got := errorObject(t, w); !reflect.DeepEqual(got, want) {
				t.Errorf("error = %v, want the chat endpoint's %v", got, want)
			}
		})
	}
	for _, model := range []string{"gpt-4o", "fast"} {
		for _, e := range asstModelRequests(model) {
			t.Run(e.name+" / "+model, func(t *testing.T) {
				g := newAsstGateway(t, key, false)
				if w := g.doLicensed(licensed, e.method, e.path, e.body); w.Code != http.StatusOK || g.up.calls.Load() != 1 {
					t.Fatalf("status %d, upstream calls %d; want 200 from one upstream call: %s", w.Code, g.up.calls.Load(), w.Body)
				}
				if got := modelMembers(t, g.up.lastBody()); !reflect.DeepEqual(got, []string{`"gpt-4o"`}) {
					t.Errorf("upstream got model members %v, want only \"gpt-4o\": %s", got, g.up.lastBody())
				}
			})
		}
	}
}
