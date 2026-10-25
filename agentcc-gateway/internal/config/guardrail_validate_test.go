package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// unsetEnv (admin_token_test.go) unsets variables for the test and restores them afterwards.

func completeFutureAGIRule() map[string]interface{} {
	return map[string]interface{}{
		"provider":   "futureagi",
		"api_key":    "fi-test-api-key",
		"secret_key": "fi-test-secret-key",
		"eval_id":    "eval-1",
	}
}

func withRule(name string, ruleCfg map[string]interface{}) *Config {
	c := DefaultConfig()
	c.Guardrails.Rules = []GuardrailRuleConfig{
		{Name: "pii-detection", Action: "block"},
		{Name: name, Stage: "pre", Action: "block", Config: ruleCfg},
	}
	return c
}

// TestFutureAGIRuleMissingConfigIsConfigError: a provider: futureagi rule
// that lacks api_key, secret_key (also when written as an unset ${VAR}) or
// every eval id fails Validate with the rule's name and the missing field, so
// the gateway refuses to start with it. A complete rule passes.
func TestFutureAGIRuleMissingConfigIsConfigError(t *testing.T) {
	// A rule names its own credentials; these stay unset so the load check
	// reads the rule alone.
	unsetEnv(t, "FI_API_KEY")
	unsetEnv(t, "FI_SECRET_KEY")
	unsetEnv(t, "AGENTCC_TEST_UNSET_FI_SECRET")
	t.Setenv("AGENTCC_TEST_SET_FI_KEY", "fi-key-from-env")

	refused := map[string]struct {
		mutate func(map[string]interface{})
		field  string
	}{
		"no api_key":            {func(m map[string]interface{}) { delete(m, "api_key") }, "api_key"},
		"empty api_key":         {func(m map[string]interface{}) { m["api_key"] = "" }, "api_key"},
		"no secret_key":         {func(m map[string]interface{}) { delete(m, "secret_key") }, "secret_key"},
		"secret_key unset var":  {func(m map[string]interface{}) { m["secret_key"] = "${AGENTCC_TEST_UNSET_FI_SECRET}" }, "secret_key"},
		"api_key not a string":  {func(m map[string]interface{}) { m["api_key"] = 42 }, "api_key"},
		"no eval id":            {func(m map[string]interface{}) { delete(m, "eval_id") }, "eval_id"},
		"empty eval_id":         {func(m map[string]interface{}) { m["eval_id"] = "" }, "eval_id"},
		"empty eval_ids list":   {func(m map[string]interface{}) { delete(m, "eval_id"); m["eval_ids"] = []interface{}{} }, "eval_id"},
		"blank eval_ids member": {func(m map[string]interface{}) { delete(m, "eval_id"); m["eval_ids"] = []interface{}{""} }, "eval_id"},
	}
	for name, tc := range refused {
		ruleCfg := completeFutureAGIRule()
		tc.mutate(ruleCfg)
		err := withRule("fi-protect", ruleCfg).Validate()
		if err == nil {
			t.Errorf("%s: Validate accepted the rule", name)
			continue
		}
		if !strings.Contains(err.Error(), `"fi-protect"`) || !strings.Contains(err.Error(), tc.field) {
			t.Errorf("%s: error %q does not name the rule and %s", name, err, tc.field)
		}
		for _, secret := range []string{"fi-test-api-key", "fi-test-secret-key"} {
			if strings.Contains(err.Error(), secret) {
				t.Errorf("%s: error %q prints a credential", name, err)
			}
		}
	}

	passed := map[string]func(map[string]interface{}){
		"complete":                  func(map[string]interface{}) {},
		"eval_ids list only":        func(m map[string]interface{}) { delete(m, "eval_id"); m["eval_ids"] = []interface{}{"e1", ""} },
		"eval_ids string slice":     func(m map[string]interface{}) { delete(m, "eval_id"); m["eval_ids"] = []string{"e1"} },
		"api_key from a set ${VAR}": func(m map[string]interface{}) { m["api_key"] = "${AGENTCC_TEST_SET_FI_KEY}" },
	}
	for name, mutate := range passed {
		ruleCfg := completeFutureAGIRule()
		mutate(ruleCfg)
		if err := withRule("fi-protect", ruleCfg).Validate(); err != nil {
			t.Errorf("%s: Validate refused a complete rule: %v", name, err)
		}
	}

	// Only provider: futureagi rules are checked.
	other := withRule("external-check", map[string]interface{}{"provider": "lakera"})
	if err := other.Validate(); err != nil {
		t.Errorf("a non-FutureAGI rule was checked: %v", err)
	}
}

// TestFutureAGIRuleMissingConfigFailsLoad: through the file loader, an
// unset ${VAR} expands to "" before the check, and Load refuses the file.
func TestFutureAGIRuleMissingConfigFailsLoad(t *testing.T) {
	unsetEnv(t, "FI_API_KEY")
	unsetEnv(t, "FI_SECRET_KEY")
	unsetEnv(t, "AGENTCC_TEST_UNSET_FI_SECRET")
	path := filepath.Join(t.TempDir(), "config.yaml")
	body := `
guardrails:
  enabled: true
  rules:
    - name: fi-protect
      stage: pre
      action: block
      config:
        provider: futureagi
        api_key: fi-test-api-key
        secret_key: ${AGENTCC_TEST_UNSET_FI_SECRET}
        eval_ids: [eval-1]
`
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	_, err := Load(path)
	if err == nil || !strings.Contains(err.Error(), `"fi-protect"`) || !strings.Contains(err.Error(), "secret_key") {
		t.Fatalf("Load = %v, want a refusal naming fi-protect and secret_key", err)
	}
}
