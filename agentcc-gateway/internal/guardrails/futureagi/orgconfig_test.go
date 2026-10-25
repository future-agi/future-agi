package futureagi

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// orgEnvRefFields is every credential/base_url field whose ${VAR} value an org
// config may not use, with how the config names it.
var orgEnvRefFields = []string{"api_key", "secret_key", "base_url"}

// These tests build with newGuard (futureagi_test.go): New with an egress
// dialer that lists loopback, so the httptest server is reachable and it is
// the environment refusal, not the egress guard, that keeps a call from it.

// TestNew_RefusesEnvReference: a rule built with New (the default, and the
// constructor for every org rule) whose api_key, secret_key or base_url
// contains ${...} is built refusing every request, and Check never calls the
// API; the message names the field, never the value.
func TestNew_RefusesEnvReference(t *testing.T) {
	// Define the variable, so the test shows the org path does not read it
	// even when it is set.
	t.Setenv("ORG_FAKE_FI_KEY", "planted-fake-value")

	for _, field := range orgEnvRefFields {
		t.Run(field, func(t *testing.T) {
			var reached bool
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				reached = true
				w.WriteHeader(200)
				w.Write([]byte(`{"result":[]}`))
			}))
			defer srv.Close()

			cfg := map[string]interface{}{
				"provider":   "futureagi",
				"base_url":   srv.URL,
				"api_key":    "fi-literal-key",
				"secret_key": "fi-literal-secret",
				"eval_id":    "toxicity",
			}
			cfg[field] = "${ORG_FAKE_FI_KEY}"

			g := newGuard("org-rule", cfg)
			if g.configErr == nil {
				t.Fatalf("%s=${...} should make the org rule refuse; configErr is nil", field)
			}
			if !strings.Contains(g.configErr.Error(), field) {
				t.Errorf("configErr should name the field %q; got %q", field, g.configErr.Error())
			}
			if strings.Contains(g.configErr.Error(), "planted-fake-value") ||
				strings.Contains(g.configErr.Error(), "ORG_FAKE_FI_KEY") {
				t.Errorf("configErr must not name the value or variable; got %q", g.configErr.Error())
			}

			res := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
			if res.Pass {
				t.Error("a refused org rule must not pass a request")
			}
			if reached {
				t.Error("a refused org rule must not call the evaluation API")
			}
		})
	}
}

// TestNew_KeepsLiteralAndDoesNotReadEnv: an org rule with literal
// credentials is built and calls its configured base_url with exactly those
// literals — never an environment value.
func TestNew_KeepsLiteralAndDoesNotReadEnv(t *testing.T) {
	// A variable named the same as a literal must not be substituted.
	t.Setenv("fi-literal-key", "env-should-not-be-read")

	var gotAPI, gotSecret string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAPI = r.Header.Get("X-Api-Key")
		gotSecret = r.Header.Get("X-Secret-Key")
		w.WriteHeader(200)
		w.Write([]byte(`{"result":[{"evaluations":[{"name":"toxicity","output":"Passed","evalId":"toxicity"}]}]}`))
	}))
	defer srv.Close()

	g := newGuard("org-rule", map[string]interface{}{
		"provider":   "futureagi",
		"base_url":   srv.URL,
		"api_key":    "fi-literal-key",
		"secret_key": "fi-literal-secret",
		"eval_id":    "toxicity",
	})
	if g.configErr != nil {
		t.Fatalf("a literal org rule should build cleanly; got %v", g.configErr)
	}
	g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
	if gotAPI != "fi-literal-key" {
		t.Errorf("X-Api-Key = %q, want the literal value", gotAPI)
	}
	if gotSecret != "fi-literal-secret" {
		t.Errorf("X-Secret-Key = %q, want the literal value", gotSecret)
	}
}
