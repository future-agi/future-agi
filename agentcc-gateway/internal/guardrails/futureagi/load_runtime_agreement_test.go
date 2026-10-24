package futureagi

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// The load check (config.Validate) and the runtime check (ValidateConfig)
// read a FutureAGI rule by one rule, written twice because config cannot
// import this package: on every misconfiguration and on the complete rule
// they agree, so a rule is refused at startup exactly when it is refused at
// runtime. A change that teaches New a new key shape updates both.
func TestLoadCheckAndRuntimeCheckAgree(t *testing.T) {
	clearFallbackEnv(t)
	loadRefuses := func(rule map[string]interface{}) bool {
		c := config.DefaultConfig()
		c.Guardrails.Rules = []config.GuardrailRuleConfig{{Name: "fi", Stage: "pre", Action: "block", Config: rule}}
		return c.Validate() != nil
	}
	if r := completeRule(""); loadRefuses(r) || ValidateConfig("fi", r) != nil {
		t.Fatalf("the complete rule: load refuses %v, runtime error %v", loadRefuses(r), ValidateConfig("fi", r))
	}
	for name, tc := range misconfigurations {
		rule := completeRule("")
		tc.mutate(rule)
		load, runtime := loadRefuses(rule), ValidateConfig("fi", rule) != nil
		if load != runtime || !load {
			t.Errorf("%s: load refuses %v, runtime refuses %v; both must refuse", name, load, runtime)
		}
	}
}
