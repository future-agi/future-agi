package futureagi

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

const (
	testAPIKey    = "fi-test-api-key"
	testSecretKey = "fi-test-secret-key"
)

// unsetEnv unsets name for the test and restores it afterwards.
func unsetEnv(t *testing.T, name string) {
	t.Helper()
	t.Setenv(name, "")
	if err := os.Unsetenv(name); err != nil {
		t.Fatal(err)
	}
}

func completeRule(baseURL string) map[string]interface{} {
	cfg := map[string]interface{}{
		"provider":   "futureagi",
		"api_key":    testAPIKey,
		"secret_key": testSecretKey,
		"eval_id":    "15",
	}
	if baseURL != "" {
		cfg["base_url"] = baseURL
	}
	return cfg
}

// misconfigurations is every way a rule can lack what the guardrail needs to
// call the evaluation API, with the field the refusal must name.
var misconfigurations = map[string]struct {
	mutate func(map[string]interface{})
	field  string
}{
	"no api_key":            {func(m map[string]interface{}) { delete(m, "api_key") }, "api_key"},
	"empty api_key":         {func(m map[string]interface{}) { m["api_key"] = "" }, "api_key"},
	"api_key unset var":     {func(m map[string]interface{}) { m["api_key"] = "${AGENTCC_TEST_UNSET_FI_KEY}" }, "api_key"},
	"api_key not a string":  {func(m map[string]interface{}) { m["api_key"] = 42 }, "api_key"},
	"no secret_key":         {func(m map[string]interface{}) { delete(m, "secret_key") }, "secret_key"},
	"secret_key unset var":  {func(m map[string]interface{}) { m["secret_key"] = "${AGENTCC_TEST_UNSET_FI_KEY}" }, "secret_key"},
	"no eval id":            {func(m map[string]interface{}) { delete(m, "eval_id") }, "eval_id"},
	"empty eval_id":         {func(m map[string]interface{}) { m["eval_id"] = "" }, "eval_id"},
	"eval_id not a string":  {func(m map[string]interface{}) { m["eval_id"] = 15 }, "eval_id"},
	"empty eval_ids list":   {func(m map[string]interface{}) { delete(m, "eval_id"); m["eval_ids"] = []interface{}{} }, "eval_id"},
	"blank eval_ids member": {func(m map[string]interface{}) { delete(m, "eval_id"); m["eval_ids"] = []string{""} }, "eval_id"},
}

// clearFallbackEnv unsets the variables the misconfigurations name, and sets
// FI_API_KEY and FI_SECRET_KEY, so a test proves that neither stands in for a
// rule's own credentials.
func clearFallbackEnv(t *testing.T) {
	t.Helper()
	unsetEnv(t, "AGENTCC_TEST_UNSET_FI_KEY")
	t.Setenv("FI_API_KEY", "fi-env-fallback-key")
	t.Setenv("FI_SECRET_KEY", "fi-env-fallback-secret")
}

// TestMissingConfigIsLoadError: ValidateConfig returns an error naming the
// rule and the missing field for each missing key (an unset ${VAR} counts as
// missing, and a set FI_API_KEY or FI_SECRET_KEY fills nothing), never printing a credential, and nil
// for a complete rule in each shape the guardrail reads.
func TestMissingConfigIsLoadError(t *testing.T) {
	clearFallbackEnv(t)
	t.Setenv("AGENTCC_TEST_SET_FI_KEY", "fi-key-from-env")

	for name, tc := range misconfigurations {
		cfg := completeRule("")
		tc.mutate(cfg)
		err := ValidateConfig("fi-protect", cfg)
		if err == nil {
			t.Errorf("%s: ValidateConfig accepted the rule", name)
			continue
		}
		msg := err.Error()
		if !strings.Contains(msg, `"fi-protect"`) || !strings.Contains(msg, tc.field) {
			t.Errorf("%s: error %q does not name the rule and %s", name, msg, tc.field)
		}
		for _, secret := range []string{testAPIKey, testSecretKey, "fi-env-fallback-key", "fi-env-fallback-secret"} {
			if strings.Contains(msg, secret) {
				t.Errorf("%s: error %q prints a credential", name, msg)
			}
		}
	}

	if err := ValidateConfig("fi-empty", nil); err == nil ||
		!strings.Contains(err.Error(), "api_key") || !strings.Contains(err.Error(), "secret_key") ||
		!strings.Contains(err.Error(), "eval_id") {
		t.Errorf("a rule with no config must name every missing field, got %v", err)
	}

	complete := map[string]func(map[string]interface{}){
		"eval_id":                   func(map[string]interface{}) {},
		"eval_ids list":             func(m map[string]interface{}) { delete(m, "eval_id"); m["eval_ids"] = []interface{}{"76", ""} },
		"eval_ids string slice":     func(m map[string]interface{}) { delete(m, "eval_id"); m["eval_ids"] = []string{"76"} },
		"api_key from a set ${VAR}": func(m map[string]interface{}) { m["api_key"] = "${AGENTCC_TEST_SET_FI_KEY}" },
	}
	for name, mutate := range complete {
		cfg := completeRule("")
		mutate(cfg)
		if err := ValidateConfig("fi-protect", cfg); err != nil {
			t.Errorf("%s: ValidateConfig refused a complete rule: %v", name, err)
		}
	}
}

// countingEvalAPI is an evaluation API that counts the calls it is sent and
// answers each with status.
func countingEvalAPI(t *testing.T, status int) (*httptest.Server, *atomic.Int32) {
	t.Helper()
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.WriteHeader(status)
	}))
	t.Cleanup(srv.Close)
	return srv, &calls
}

// blockedByEngine runs g as a pre-stage block rule at threshold through the
// guardrail engine and reports whether the request was blocked.
func blockedByEngine(g guardrails.Guardrail, threshold float64, input *guardrails.CheckInput) bool {
	engine := guardrails.NewEngine(config.GuardrailsConfig{
		Rules: []config.GuardrailRuleConfig{{
			Name: g.Name(), Stage: "pre", Mode: "sync", Action: "block", Threshold: threshold,
		}},
	}, map[string]guardrails.Guardrail{g.Name(): g})
	return engine.RunPre(context.Background(), input, nil, policy.RequestPolicyNone).Blocked
}

// TestMisconfiguredCheckFailsClosed: a guardrail built with each missing
// field returns Pass: false from Check, with a message naming the rule and
// the field (never "skipping"), and sends nothing to the evaluation API. It
// is a hard failure, so a block rule blocks at any threshold, and it refuses
// even an input with no text to evaluate.
func TestMisconfiguredCheckFailsClosed(t *testing.T) {
	clearFallbackEnv(t)
	api, calls := countingEvalAPI(t, http.StatusOK)
	input := makeInput([]models.Message{makeMsg("user", "hello")})

	for name, tc := range misconfigurations {
		cfg := completeRule(api.URL)
		tc.mutate(cfg)
		// These rules are config-file rules (an unset ${VAR} counts as
		// missing), so they are built the way the registry loop builds them.
		g := NewConfigFileRule("fi-protect", cfg)

		for label, in := range map[string]*guardrails.CheckInput{"text": input, "nil input": nil, "no messages": makeInput(nil)} {
			result := g.Check(context.Background(), in)
			if result.Pass {
				t.Errorf("%s, %s: Check passed a misconfigured rule (%q)", name, label, result.Message)
				continue
			}
			if strings.Contains(result.Message, "skipping") ||
				!strings.Contains(result.Message, `"fi-protect"`) || !strings.Contains(result.Message, tc.field) {
				t.Errorf("%s, %s: message %q does not name the rule and %s", name, label, result.Message, tc.field)
			}
		}
		for _, threshold := range []float64{0, 0.8, 1} {
			if !blockedByEngine(g, threshold, input) {
				t.Errorf("%s: a block rule at threshold %v let the request through", name, threshold)
			}
		}
	}
	if n := calls.Load(); n != 0 {
		t.Fatalf("misconfigured guardrails sent %d calls to the evaluation API, want 0", n)
	}
}

// TestApiErrorStillFailsClosed: a complete rule whose evaluation API call
// fails (an error status, or an API that cannot be reached) still returns
// Pass: false after making the call, and a block rule blocks on it.
func TestApiErrorStillFailsClosed(t *testing.T) {
	input := makeInput([]models.Message{makeMsg("user", "hello")})
	for _, status := range []int{http.StatusForbidden, http.StatusBadRequest, http.StatusInternalServerError} {
		api, calls := countingEvalAPI(t, status)
		g := NewConfigFileRule("fi-protect", completeRule(api.URL))
		if err := ValidateConfig("fi-protect", completeRule(api.URL)); err != nil {
			t.Fatalf("the complete rule is refused: %v", err)
		}
		result := g.Check(context.Background(), input)
		if result.Pass {
			t.Errorf("HTTP %d: Check passed on an API error", status)
		}
		if calls.Load() != 1 {
			t.Errorf("HTTP %d: %d calls to the evaluation API, want 1", status, calls.Load())
		}
		if !blockedByEngine(g, 0.8, input) {
			t.Errorf("HTTP %d: a block rule let the request through", status)
		}
	}

	down := httptest.NewServer(http.NotFoundHandler())
	url := down.URL
	down.Close()
	if result := NewConfigFileRule("fi-protect", completeRule(url)).Check(context.Background(), input); result.Pass {
		t.Error("Check passed when the evaluation API cannot be reached")
	}
}
