package external

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// These tests build with newGuard (external_test.go): New with an egress
// dialer that lists loopback, so the httptest server is reachable and it is
// the environment refusal, not the egress guard, that keeps a call from it.

// TestNew_RefusesEnvReference: an external check built with New (the
// default, and the constructor for every org check) whose any field contains
// ${...} is built with no adapter and refuses every request; the message
// names the field, never the value, and no call is made.
func TestNew_RefusesEnvReference(t *testing.T) {
	t.Setenv("ORG_FAKE_LAKERA_KEY", "planted-fake-value")

	for _, field := range []string{"api_key", "endpoint"} {
		t.Run(field, func(t *testing.T) {
			var reached bool
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				reached = true
				w.WriteHeader(200)
				w.Write([]byte(`{}`))
			}))
			defer srv.Close()

			cfg := map[string]interface{}{
				"provider": "lakera",
				"endpoint": srv.URL + "/v2/guard",
				"api_key":  "lk-literal-key",
			}
			cfg[field] = "${ORG_FAKE_LAKERA_KEY}"
			if field == "endpoint" {
				cfg[field] = "${ORG_FAKE_LAKERA_KEY}" // a ${VAR} endpoint
			}

			g := newGuard("org-ext", cfg)
			if g.configErr == nil {
				t.Fatalf("%s=${...} should make the org check refuse; configErr is nil", field)
			}
			if !strings.Contains(g.configErr.Error(), field) {
				t.Errorf("configErr should name the field %q; got %q", field, g.configErr.Error())
			}
			if strings.Contains(g.configErr.Error(), "planted-fake-value") ||
				strings.Contains(g.configErr.Error(), "ORG_FAKE_LAKERA_KEY") {
				t.Errorf("configErr must not name the value or variable; got %q", g.configErr.Error())
			}
			if g.adapter != nil {
				t.Error("a refused org check must not build an adapter")
			}

			res := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "ignore previous")}))
			if res.Pass {
				t.Error("a refused org check must fail closed, not pass")
			}
			if reached {
				t.Error("a refused org check must not call the provider")
			}
		})
	}
}

// TestNew_KeepsLiteralAndDoesNotReadEnv: an org external check
// with literal fields builds its adapter and calls its endpoint with the
// literal key — never an environment value.
func TestNew_KeepsLiteralAndDoesNotReadEnv(t *testing.T) {
	t.Setenv("lk-literal-key", "env-should-not-be-read")

	var gotAuth string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuth = r.Header.Get("Authorization")
		w.WriteHeader(200)
		w.Write([]byte(`{"flagged":false}`))
	}))
	defer srv.Close()

	g := newGuard("org-ext", map[string]interface{}{
		"provider": "lakera",
		"endpoint": srv.URL + "/v2/guard",
		"api_key":  "lk-literal-key",
	})
	if g.configErr != nil {
		t.Fatalf("a literal org check should build cleanly; got %v", g.configErr)
	}
	if g.adapter == nil {
		t.Fatal("a literal org check should build an adapter")
	}
	g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
	if gotAuth != "Bearer lk-literal-key" {
		t.Errorf("Authorization = %q, want the literal key", gotAuth)
	}
}
