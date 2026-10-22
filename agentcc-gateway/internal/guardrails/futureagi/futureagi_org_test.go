package futureagi

// A provider: futureagi rule built from org-pushed config (New, which the
// dynamic guardrail factory uses) must never send a credential from the
// gateway process environment: not by expanding a ${VAR} in its config, and
// not by falling back to FI_API_KEY and FI_SECRET_KEY when it names no key.
// A config.yaml rule (NewConfigFileRule) does both.
//
// Every variable set here is a test value, and the org-chosen
// host is an httptest server on 127.0.0.1.

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// collector stands in for a host the org picked, and records what reached it.
type collector struct {
	mu     sync.Mutex
	hits   int
	apiKey string
	secret string
}

func newCollector(t *testing.T) (*collector, string) {
	t.Helper()
	c := &collector{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		c.mu.Lock()
		c.hits++
		c.apiKey = r.Header.Get("X-Api-Key")
		c.secret = r.Header.Get("X-Secret-Key")
		c.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"result":[]}`))
	}))
	t.Cleanup(srv.Close)
	return c, srv.URL
}

func (c *collector) seen() (hits int, apiKey, secret string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.hits, c.apiKey, c.secret
}

func checkOnce(g *FutureAGIGuardrail) *guardrails.CheckResult {
	return g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
}

// An org config whose api_key and secret_key are ${VAR} references to the
// gateway's own environment and whose base_url is the org's host.
func TestNew_EnvReferenceIsRefusedAndNothingIsSent(t *testing.T) {
	const adminToken = "FAKE-ADMIN-TOKEN-t-0123456789abcdef"
	const webhookSecret = "FAKE-WEBHOOK-SECRET-t-0123456789"
	t.Setenv("TEST_FAKE_ADMIN_TOKEN", adminToken)
	t.Setenv("TEST_FAKE_WEBHOOK_SECRET", webhookSecret)
	c, url := newCollector(t)

	g := newGuard("x", map[string]interface{}{
		"provider":   "futureagi",
		"base_url":   url,
		"api_key":    "${TEST_FAKE_ADMIN_TOKEN}",
		"secret_key": "${TEST_FAKE_WEBHOOK_SECRET}",
		"eval_id":    "toxicity",
	})
	res := checkOnce(g)

	hits, gotKey, gotSecret := c.seen()
	if hits != 0 {
		t.Fatalf("the org's host received %d request(s); X-Api-Key is the planted env value: %v, X-Secret-Key: %v",
			hits, gotKey == adminToken, gotSecret == webhookSecret)
	}
	if res.Pass {
		t.Fatal("a ${VAR} in an org rule must refuse the request, not pass it")
	}
	// No score: a failure without a score triggers at any threshold.
	if res.Score != 0 {
		t.Fatalf("the refusal must carry no score; got %v", res.Score)
	}
	if !strings.Contains(res.Message, "config.api_key") {
		t.Fatalf("the refusal must name the field; got %q", res.Message)
	}
	for _, leaked := range []string{adminToken, webhookSecret, "TEST_FAKE_ADMIN_TOKEN"} {
		if strings.Contains(res.Message, leaked) {
			t.Fatalf("the refusal must not carry the value or the variable name; got %q", res.Message)
		}
	}
}

// A "${" in api_key, secret_key or base_url refuses, including one that would
// not be expanded (not the whole string). A "${" in any other field is not a
// reference the gateway resolves for a config.yaml rule (TestNew_RefusesOnlyTheFieldsAConfigFileRuleResolves).
func TestNew_EnvReferenceInAResolvedFieldIsRefused(t *testing.T) {
	c, url := newCollector(t)
	for field, value := range map[string]string{
		"api_key":    "Bearer ${TEST_FAKE_KEY}",
		"base_url":   "${TEST_FAKE_BASE_URL}",
		"secret_key": "prefix-${TEST_FAKE_SECRET}",
	} {
		cfg := map[string]interface{}{
			"provider": "futureagi", "base_url": url,
			"api_key": "k", "secret_key": "s", "eval_id": "toxicity",
		}
		cfg[field] = value
		res := checkOnce(newGuard("x", cfg))
		if res.Pass || !strings.Contains(res.Message, "config."+field) {
			t.Fatalf("%s=%q: want a refusal naming config.%s; got pass=%v %q", field, value, field, res.Pass, res.Message)
		}
	}
	if hits, _, _ := c.seen(); hits != 0 {
		t.Fatalf("a refused rule reached the org's host %d time(s)", hits)
	}
}

// An org rule that names no key sends no key: never the operator's
// FI_API_KEY or FI_SECRET_KEY.
func TestNew_NoKeyNeverBorrowsTheProcessKey(t *testing.T) {
	const operatorKey = "FAKE-OPERATOR-FI-KEY-t"
	const operatorSecret = "FAKE-OPERATOR-FI-SECRET-t"
	t.Setenv("FI_API_KEY", operatorKey)
	t.Setenv("FI_SECRET_KEY", operatorSecret)
	c, url := newCollector(t)

	for name, cfg := range map[string]map[string]interface{}{
		"no key":        {"provider": "futureagi", "base_url": url, "eval_id": "toxicity"},
		"no secret key": {"provider": "futureagi", "base_url": url, "eval_id": "toxicity", "api_key": "org-key"},
		"no api key":    {"provider": "futureagi", "base_url": url, "eval_id": "toxicity", "secret_key": "org-secret"},
	} {
		res := checkOnce(newGuard("x", cfg))
		if res.Pass || res.Score != 0 || !strings.Contains(res.Message, "missing") {
			t.Fatalf("%s: want a refusal with no score naming the missing field; got pass=%v score=%v %q",
				name, res.Pass, res.Score, res.Message)
		}
	}
	if hits, gotKey, gotSecret := c.seen(); hits != 0 {
		t.Fatalf("the org's host received %d request(s) carrying the operator's FI_API_KEY: %v, FI_SECRET_KEY: %v",
			hits, gotKey == operatorKey, gotSecret == operatorSecret)
	}
}

// An org rule with no eval id has nothing to run, and refuses the request.
func TestNew_NoEvalIDRefuses(t *testing.T) {
	c, url := newCollector(t)
	res := checkOnce(newGuard("x", map[string]interface{}{
		"provider": "futureagi", "base_url": url, "api_key": "k", "secret_key": "s",
	}))
	if res.Pass || !strings.Contains(res.Message, "eval_id") {
		t.Fatalf("want a refusal naming the eval id; got pass=%v %q", res.Pass, res.Message)
	}
	if hits, _, _ := c.seen(); hits != 0 {
		t.Fatalf("an incomplete rule reached the host %d time(s)", hits)
	}
}

// A complete org rule with literal values runs and sends the org's
// own keys, never the operator's.
func TestNew_LiteralRuleRuns(t *testing.T) {
	t.Setenv("FI_API_KEY", "FAKE-OPERATOR-FI-KEY-t")
	t.Setenv("FI_SECRET_KEY", "FAKE-OPERATOR-FI-SECRET-t")
	c, url := newCollector(t)

	res := checkOnce(newGuard("x", map[string]interface{}{
		"provider": "futureagi", "base_url": url,
		"api_key": "org-key", "secret_key": "org-secret", "eval_ids": []interface{}{"toxicity"},
	}))

	hits, gotKey, gotSecret := c.seen()
	if hits != 1 || gotKey != "org-key" || gotSecret != "org-secret" {
		t.Fatalf("want one call with the org's own keys; got %d call(s), X-Api-Key %q, X-Secret-Key %q", hits, gotKey, gotSecret)
	}
	if !res.Pass {
		t.Fatalf("an empty evaluation result passes; got %q", res.Message)
	}
}

// FI_BASE_URL names where the operator's FutureAGI platform is for an
// org rule that sets no base_url (the Helm chart points it at the in-cluster
// backend). It is a destination the operator chose, not a credential: the org
// rule's own keys go there, never FI_API_KEY.
func TestNew_OperatorBaseURLAppliesWithTheOrgsKeys(t *testing.T) {
	t.Setenv("FI_API_KEY", "FAKE-OPERATOR-FI-KEY-t")
	t.Setenv("FI_SECRET_KEY", "FAKE-OPERATOR-FI-SECRET-t")
	c, url := newCollector(t)
	t.Setenv("FI_BASE_URL", url)

	checkOnce(newGuard("x", map[string]interface{}{
		"provider": "futureagi", "api_key": "org-key", "secret_key": "org-secret", "eval_id": "toxicity",
	}))

	if hits, gotKey, gotSecret := c.seen(); hits != 1 || gotKey != "org-key" || gotSecret != "org-secret" {
		t.Fatalf("want one call to FI_BASE_URL with the org's keys; got %d call(s), X-Api-Key %q, X-Secret-Key %q",
			hits, gotKey, gotSecret)
	}
}

// A config.yaml rule (NewConfigFileRule) is operator config: it expands
// ${VAR}, and with no key named it uses FI_API_KEY / FI_SECRET_KEY.
func TestNewConfigFileRule_ReadsTheEnvironment(t *testing.T) {
	t.Setenv("TEST_OPERATOR_KEY", "operator-key-from-env")
	c, url := newCollector(t)
	checkOnce(NewConfigFileRule("x", map[string]interface{}{
		"provider": "futureagi", "base_url": url,
		"api_key": "${TEST_OPERATOR_KEY}", "secret_key": "s", "eval_id": "toxicity",
	}))
	if hits, gotKey, _ := c.seen(); hits != 1 || gotKey != "operator-key-from-env" {
		t.Fatalf("a config.yaml rule must expand ${TEST_OPERATOR_KEY}; got %d call(s), X-Api-Key %q", hits, gotKey)
	}

	t.Setenv("FI_API_KEY", "operator-fi-key")
	t.Setenv("FI_SECRET_KEY", "operator-fi-secret")
	c2, url2 := newCollector(t)
	checkOnce(NewConfigFileRule("y", map[string]interface{}{
		"provider": "futureagi", "base_url": url2, "eval_id": "toxicity",
	}))
	if hits, gotKey, gotSecret := c2.seen(); hits != 1 || gotKey != "operator-fi-key" || gotSecret != "operator-fi-secret" {
		t.Fatalf("a config.yaml rule with no key must use FI_API_KEY/FI_SECRET_KEY; got %d call(s), %q, %q",
			hits, gotKey, gotSecret)
	}
}

// New is the constructor code reaches for by default, so it is the
// safe one for org config: it never resolves a ${VAR} and never falls back to
// FI_API_KEY or FI_SECRET_KEY. The constructor that reads credentials from the
// environment has an explicit name, NewConfigFileRule, for config.yaml rules only.
func TestNew_NeverReadsACredentialFromTheGatewayEnvironment(t *testing.T) {
	const planted = "FAKE-GATEWAY-SECRET-t2"
	t.Setenv("TEST_FAKE2_SECRET", planted)
	t.Setenv("FI_API_KEY", "FAKE-OPERATOR-FI-KEY-t2")
	t.Setenv("FI_SECRET_KEY", "FAKE-OPERATOR-FI-SECRET-t2")

	c, url := newCollector(t)
	res := checkOnce(newGuard("x", map[string]interface{}{
		"provider": "futureagi", "base_url": url,
		"api_key": "${TEST_FAKE2_SECRET}", "secret_key": "org-secret", "eval_id": "toxicity",
	}))
	if hits, gotKey, _ := c.seen(); hits != 0 {
		t.Fatalf("New resolved ${TEST_FAKE2_SECRET}: %d call(s), X-Api-Key %q", hits, gotKey)
	}
	if res.Pass {
		t.Fatalf("New with a ${...} in api_key must refuse; got pass %q", res.Message)
	}

	c2, url2 := newCollector(t)
	checkOnce(newGuard("y", map[string]interface{}{"provider": "futureagi", "base_url": url2, "eval_id": "toxicity"}))
	if hits, gotKey, gotSecret := c2.seen(); hits != 0 {
		t.Fatalf("New with no key fell back to FI_*: %d call(s), X-Api-Key %q, X-Secret-Key %q", hits, gotKey, gotSecret)
	}
}

// A config.yaml futureagi rule resolves ${NAME} only in its
// api_key, secret_key and base_url, so those are the only fields an org rule
// refuses a "${" in (the same fields Django refuses on save). Any other field
// is sent as written and the rule runs.
func TestNew_RefusesOnlyTheFieldsAConfigFileRuleResolves(t *testing.T) {
	var gotCallType string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Inputs []struct {
				CallType string `json:"call_type"`
			} `json:"inputs"`
		}
		json.NewDecoder(r.Body).Decode(&body)
		if len(body.Inputs) > 0 {
			gotCallType = body.Inputs[0].CallType
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"result":[]}`))
	}))
	defer srv.Close()

	res := checkOnce(newGuard("x", map[string]interface{}{
		"provider": "futureagi", "base_url": srv.URL, "api_key": "org-key", "secret_key": "org-secret",
		"eval_id": "toxicity", "call_type": "${not-an-env-reference}",
	}))
	if gotCallType != "${not-an-env-reference}" {
		t.Fatalf("a ${ outside api_key, secret_key and base_url must be sent as written; got call_type %q (message %q)",
			gotCallType, res.Message)
	}
}

// FI_BASE_URL is operator config. When the call to it fails, the
// check's message (which a block response, a warning and the request log carry
// to the org) must not name the operator's host, address or path.
func TestNew_FailedCallNamesNoOperatorAddress(t *testing.T) {
	t.Setenv("FI_BASE_URL", "http://127.0.0.1:9/t-operator-fi-host")

	res := checkOnce(newGuard("x", map[string]interface{}{
		"provider": "futureagi", "api_key": "org-key", "secret_key": "org-secret", "eval_id": "toxicity",
	}))

	if res.Pass {
		t.Fatalf("a failed call must not pass; got %q", res.Message)
	}
	for _, operatorDetail := range []string{"127.0.0.1", ":9", "t-operator-fi-host", "/sdk/api/v1/eval"} {
		if strings.Contains(res.Message, operatorDetail) {
			t.Fatalf("the message names the operator's evaluation host (%q): %q", operatorDetail, res.Message)
		}
	}
	if !strings.Contains(res.Message, "futureagi evaluation failed") {
		t.Fatalf("the message must still say the evaluation failed; got %q", res.Message)
	}
}
