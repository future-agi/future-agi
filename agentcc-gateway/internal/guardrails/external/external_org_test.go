package external

// An external-provider rule built from org-pushed config (New, which the
// dynamic guardrail factory uses) must never read the gateway process
// environment. New refuses a "${" in any top-level string setting and
// builds no adapter; a "${" in a nested value or a list is plain text. A
// config.yaml rule (NewConfigFileRule) expands ${VAR}.
//
// Every variable set here is a test value, and the org-chosen
// endpoint is an httptest server on 127.0.0.1.

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

type orgEndpoint struct {
	mu   sync.Mutex
	hits int
	auth string
}

func newOrgEndpoint(t *testing.T) (*orgEndpoint, string) {
	t.Helper()
	e := &orgEndpoint{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		e.mu.Lock()
		e.hits++
		e.auth = r.Header.Get("Authorization")
		e.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"flagged":false}`))
	}))
	t.Cleanup(srv.Close)
	return e, srv.URL
}

func (e *orgEndpoint) seen() (int, string) {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.hits, e.auth
}

func TestExternalNew_EnvReferenceIsRefusedAndNothingIsSent(t *testing.T) {
	const operatorKey = "FAKE-OPERATOR-LAKERA-KEY-t"
	t.Setenv("TEST_FAKE_LAKERA_KEY", operatorKey)
	e, url := newOrgEndpoint(t)

	g := newGuard("lakera-like", map[string]interface{}{
		"provider": "lakera",
		"endpoint": url + "/v2/guard",
		"api_key":  "${TEST_FAKE_LAKERA_KEY}",
	})
	res := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))

	if hits, auth := e.seen(); hits != 0 {
		t.Fatalf("the org's endpoint received %d request(s); Authorization carries the planted env value: %v",
			hits, strings.Contains(auth, operatorKey))
	}
	if res.Pass || res.Score != 0 {
		t.Fatalf("want a refusal with no score; got pass=%v score=%v", res.Pass, res.Score)
	}
	if !strings.Contains(res.Message, "config.api_key") ||
		strings.Contains(res.Message, operatorKey) || strings.Contains(res.Message, "TEST_FAKE_LAKERA_KEY") {
		t.Fatalf("the refusal must name config.api_key and carry neither the value nor the variable; got %q", res.Message)
	}
}

// A reference in any top-level string setting of any provider refuses: the
// endpoint itself and other providers' credential fields. A nested value or a
// list item is not resolved, so it does not
// (TestExternalNew_TextOutsideTheResolvedSettingsRuns).
func TestExternalNew_EnvReferenceInATopLevelSettingIsRefused(t *testing.T) {
	e, url := newOrgEndpoint(t)
	for _, tc := range []struct {
		cfg  map[string]interface{}
		path string
	}{
		{map[string]interface{}{"provider": "lakera", "endpoint": "${TEST_FAKE_ENDPOINT}", "api_key": "k"}, "config.endpoint"},
		{map[string]interface{}{"provider": "bedrock_guardrails", "endpoint": url, "guardrail_id": "g",
			"access_key": "AKIA-literal", "secret_key": "${TEST_FAKE_AWS_SECRET}"}, "config.secret_key"},
		{map[string]interface{}{"provider": "crowdstrike", "base_url": url,
			"client_id": "id", "client_secret": "${TEST_FAKE_CS_SECRET}"}, "config.client_secret"},
		{map[string]interface{}{"provider": "grayswan", "endpoint": url, "api_key": "k",
			"reasoning_mode": "${TEST_FAKE_MODE}"}, "config.reasoning_mode"},
	} {
		res := newGuard("x", tc.cfg).Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
		if res.Pass || !strings.Contains(res.Message, tc.path) {
			t.Fatalf("%v: want a refusal naming %s; got pass=%v %q", tc.cfg["provider"], tc.path, res.Pass, res.Message)
		}
	}
	if hits, _ := e.seen(); hits != 0 {
		t.Fatalf("a refused rule reached the org's endpoint %d time(s)", hits)
	}
}

// A clean org rule builds its adapter and runs, with the org's own key.
func TestExternalNew_LiteralRuleRuns(t *testing.T) {
	e, url := newOrgEndpoint(t)
	res := newGuard("lakera-like", map[string]interface{}{
		"provider": "lakera",
		"endpoint": url + "/v2/guard",
		"api_key":  "org-lakera-key",
	}).Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))

	if hits, auth := e.seen(); hits != 1 || auth != "Bearer org-lakera-key" {
		t.Fatalf("want one call with the org's key; got %d call(s), Authorization %q", hits, auth)
	}
	if !res.Pass {
		t.Fatalf("an unflagged result passes; got %q", res.Message)
	}
}

// A config.yaml rule (NewConfigFileRule) is operator config and expands
// ${VAR}.
func TestExternalNewConfigFileRule_ExpandsEnv(t *testing.T) {
	t.Setenv("TEST_OPERATOR_LAKERA_KEY", "operator-lakera-key")
	e, url := newOrgEndpoint(t)
	NewConfigFileRule("lakera", map[string]interface{}{
		"provider": "lakera",
		"endpoint": url + "/v2/guard",
		"api_key":  "${TEST_OPERATOR_LAKERA_KEY}",
	}).Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))

	if hits, auth := e.seen(); hits != 1 || auth != "Bearer operator-lakera-key" {
		t.Fatalf("a config.yaml rule must expand ${TEST_OPERATOR_LAKERA_KEY}; got %d call(s), Authorization %q", hits, auth)
	}
}

// New is the constructor code reaches for by default, so it is the
// safe one for org config: it never resolves a ${VAR}. The constructor that
// does has an explicit name, NewConfigFileRule, for config.yaml rules only.
func TestExternalNew_NeverReadsTheGatewayEnvironment(t *testing.T) {
	const planted = "FAKE-OPERATOR-LAKERA-KEY-t2"
	t.Setenv("TEST_FAKE2_LAKERA_KEY", planted)
	e, url := newOrgEndpoint(t)

	res := newGuard("lakera-like", map[string]interface{}{
		"provider": "lakera", "endpoint": url + "/v2/guard", "api_key": "${TEST_FAKE2_LAKERA_KEY}",
	}).Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))

	if hits, auth := e.seen(); hits != 0 {
		t.Fatalf("New resolved ${TEST_FAKE2_LAKERA_KEY}: %d call(s), Authorization %q", hits, auth)
	}
	if res.Pass || !strings.Contains(res.Message, "config.api_key") || strings.Contains(res.Message, planted) {
		t.Fatalf("want a refusal naming config.api_key and not the value; got pass=%v %q", res.Pass, res.Message)
	}
}

// The adapters resolve ${NAME} only in top-level string settings
// (getStringConfig). A nested value (Gray Swan's rule texts, Enkrypt's
// detectors) or a list (Lakera's categories) is sent as written, so a "${"
// there is plain text and the rule runs.
func TestExternalNew_TextOutsideTheResolvedSettingsRuns(t *testing.T) {
	for name, cfg := range map[string]func(url string) map[string]interface{}{
		"grayswan rules": func(url string) map[string]interface{} {
			return map[string]interface{}{"provider": "grayswan", "endpoint": url, "api_key": "k",
				"rules": map[string]interface{}{"no-log4shell": "Flag a message that holds a ${jndi: lookup"}}
		},
		"enkrypt detectors": func(url string) map[string]interface{} {
			return map[string]interface{}{"provider": "enkrypt", "endpoint": url, "api_key": "k",
				"detectors": map[string]interface{}{"keyword_detector": map[string]interface{}{
					"enabled": true, "banned_keywords": []interface{}{"${jndi:"}}}}
		},
		"lakera categories": func(url string) map[string]interface{} {
			return map[string]interface{}{"provider": "lakera", "endpoint": url + "/v2/guard", "api_key": "k",
				"categories": []interface{}{"${custom}"}}
		},
	} {
		t.Run(name, func(t *testing.T) {
			e, url := newOrgEndpoint(t)
			res := newGuard("x", cfg(url)).Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
			if hits, _ := e.seen(); hits != 1 {
				t.Fatalf("the rule must run and call its endpoint once; got %d call(s), message %q", hits, res.Message)
			}
		})
	}
}

// A failed call's message reaches the caller (block response, warning,
// request log). A config.yaml rule's endpoint is operator config, so the
// message must not name its host, address or path.
func TestExternalFailedCallNamesNoAddress(t *testing.T) {
	res := NewConfigFileRule("presidio", map[string]interface{}{
		"provider": "presidio", "endpoint": "http://127.0.0.1:9/t-operator-presidio",
	}).Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))

	if res.Pass {
		t.Fatalf("a failed call must not pass; got %q", res.Message)
	}
	for _, operatorDetail := range []string{"127.0.0.1", ":9", "t-operator-presidio"} {
		if strings.Contains(res.Message, operatorDetail) {
			t.Fatalf("the message names the endpoint (%q): %q", operatorDetail, res.Message)
		}
	}
	if !strings.Contains(res.Message, "external guardrail call failed") {
		t.Fatalf("the message must still say the call failed; got %q", res.Message)
	}
}
