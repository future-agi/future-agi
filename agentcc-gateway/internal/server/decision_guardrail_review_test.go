package server

import (
	"context"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/pii"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
)

// decisionWithPII runs the gateway with a blocking PII rule on the input.
func decisionWithPII(t *testing.T) *decisionGateway {
	t.Helper()
	g := newDecisionGateway(t, decisionGatewayOptions{})
	rule := pii.New(map[string]interface{}{})
	reg := map[string]guardrails.Guardrail{rule.Name(): rule}
	engine := guardrails.NewEngine(config.GuardrailsConfig{Rules: []config.GuardrailRuleConfig{{Name: rule.Name(), Stage: "pre", Mode: "sync", Action: "block"}}}, reg)
	useDecisionGuardrails(g, engine, pipeline.NewEngine(authplugin.New(g.keyStore, true), guardrails.NewPlugin(engine, reg, nil, nil, nil)))
	return g
}

// nestedCriteria is a System One body whose one question's criteria nest
// depth levels deep, with a marker at the bottom.
func nestedCriteria(depth int) string {
	return `{"model":"typesafe/jev-latest","state":"safe","questions":{"q":{"type":"choice","criteria":` +
		strings.Repeat("[", depth) + `"deep-marker"` + strings.Repeat("]", depth) + `}}}`
}

// A deeply nested decision field is read once and refused at the depth cap
// when guardrails apply, instead of being decoded again at every level.
func TestDecisionGuardrailDeepNestingIsBoundedAndRefused(t *testing.T) {
	g, c := decisionWithGuardrail(t, "block", "never-matches")
	body := nestedCriteria(9000)
	start := time.Now()
	rr := g.post(t, keyByok, body)
	elapsed := time.Since(start)
	expectError(t, rr, http.StatusBadRequest, "decision_too_deep")
	if len(c.inputs) != 0 {
		t.Errorf("guardrail ran on unchecked text: %d checks", len(c.inputs))
	}
	expectNoCalls(t, g)
	if elapsed > 500*time.Millisecond {
		t.Errorf("a 9000-level body took %v to refuse, want well under 500ms", elapsed)
	}

	// Within the cap, the deepest text is still checked.
	g, c = decisionWithGuardrail(t, "block", "deep-marker")
	expectError(t, g.post(t, keyByok, nestedCriteria(maxDecisionTextDepth-1)), http.StatusForbidden, "content_blocked")
	if len(c.inputs) != 1 {
		t.Fatalf("guardrail checks = %d, want 1", len(c.inputs))
	}
}

// The walk itself does work in proportion to the body, not depth x body.
func TestDecisionGuardrailContentDecodesOnce(t *testing.T) {
	body := []byte(nestedCriteria(maxDecisionTextDepth - 1))
	allocs := testing.AllocsPerRun(20, func() {
		if _, err := decisionGuardrailContent(body, 0); err != nil {
			t.Fatal(err)
		}
	})
	// One decode allocates a slice per level; decoding again at every level
	// would allocate per level squared (about 4,000 here).
	if allocs > 600 {
		t.Errorf("allocations = %.0f for %d levels, want linear", allocs, maxDecisionTextDepth-1)
	}
}

// With no input guardrail at all, no text is extracted: even a body nested
// near encoding/json's limit goes straight to the provider.
func TestDecisionWithoutGuardrailsSkipsExtraction(t *testing.T) {
	g := newDecisionGateway(t, decisionGatewayOptions{})
	start := time.Now()
	rr := g.post(t, keyByok, nestedCriteria(9000))
	elapsed := time.Since(start)
	if rr.Code != http.StatusOK {
		t.Fatalf("status %d: %s", rr.Code, rr.Body.String())
	}
	expectOnlyKey(t, g, orgTypeSafeKey, 1)
	if elapsed > 500*time.Millisecond {
		t.Errorf("took %v with no guardrails, want well under 500ms", elapsed)
	}
}

// The guardrail view covers every content field, as the vendor error filter
// does: question choices, descriptions and examples, and unknown top-level
// fields.
func TestDecisionGuardrailSeesEveryContentField(t *testing.T) {
	for name, body := range map[string]string{
		"choices":       `{"model":"typesafe/jev-latest","state":"safe","questions":{"q":{"type":"choice","instructions":"pick","choices":["a","person@example.test"]}}}`,
		"description":   `{"model":"typesafe/jev-latest","state":"safe","questions":{"q":{"type":"noul","description":"mail person@example.test"}}}`,
		"examples":      `{"model":"typesafe/jev-latest","state":"safe","questions":{"q":{"type":"noul","examples":[{"in":"person@example.test"}]}}}`,
		"top-level":     `{"model":"typesafe/jev-latest","state":"safe","context":{"note":"person@example.test"},"questions":{"q":{"type":"noul"}}}`,
		"openai option": `{"model":"openai/gpt-6-luna","input":"safe","questions":[{"type":"choice","name":"q","options":["person@example.test"]}]}`,
	} {
		t.Run(name, func(t *testing.T) {
			g := decisionWithPII(t)
			expectError(t, g.post(t, keyByok, body), http.StatusForbidden, "content_blocked")
			expectNoCalls(t, g)
		})
	}
}

// Object state written with \u escapes (Python requests' json= does this to
// every non-ASCII character) is checked as decoded text.
func TestDecisionGuardrailDecodesEscapedObjectState(t *testing.T) {
	g := decisionWithPII(t)
	body := `{"model":"typesafe/jev-latest","state":{"contact":"person\u0040example\u002etest"},"questions":{"q":{"type":"noul"}}}`
	expectError(t, g.post(t, keyByok, body), http.StatusForbidden, "content_blocked")
	expectNoCalls(t, g)
}

// The platform's own internal-key traffic (the Jev evaluator judging traces)
// is not held to decision input guardrails; org keys are.
func TestDecisionGuardrailsSkipInternalKeys(t *testing.T) {
	body := strings.Replace(decisionSysBody, "private-state", "person@example.test", 1)

	g := decisionWithPII(t)
	if rr := g.post(t, keyInternal, body, models.ProviderKeyHeader, callerOwnKey); rr.Code != http.StatusOK {
		t.Fatalf("internal key: status %d: %s", rr.Code, rr.Body.String())
	}
	expectOnlyKey(t, g, callerOwnKey, 1)

	g = decisionWithPII(t)
	expectError(t, g.post(t, keyByok, body), http.StatusForbidden, "content_blocked")
	expectNoCalls(t, g)
}

// failingPlugin stands in for a plugin that refuses a request after the
// guardrails ran, as a rate limit would.
type failingPlugin struct{}

func (failingPlugin) Name() string  { return "test-rate-limit" }
func (failingPlugin) Priority() int { return 60 }
func (failingPlugin) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultError(models.ErrTooManyRequests("synthetic rate limit"))
}
func (failingPlugin) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

// A redacting rule rewrites the guardrail view without blocking; another
// plugin's later refusal is returned as it is, not as the redaction 403.
func TestDecisionKeepsOtherPluginErrorsAfterARedaction(t *testing.T) {
	g := newDecisionGateway(t, decisionGatewayOptions{})
	rule := pii.New(map[string]interface{}{"remediation": "redact"})
	reg := map[string]guardrails.Guardrail{rule.Name(): rule}
	engine := guardrails.NewEngine(config.GuardrailsConfig{Rules: []config.GuardrailRuleConfig{{Name: rule.Name(), Stage: "pre", Mode: "sync", Action: "log"}}}, reg)
	useDecisionGuardrails(g, engine, pipeline.NewEngine(authplugin.New(g.keyStore, true), guardrails.NewPlugin(engine, reg, nil, nil, nil), failingPlugin{}))
	body := strings.Replace(decisionSysBody, "private-state", "person@example.test", 1)
	rr := g.post(t, keyByok, body)
	if rr.Code != http.StatusTooManyRequests {
		t.Fatalf("status %d: %s, want the rate limit's 429", rr.Code, rr.Body.String())
	}
	expectNoCalls(t, g)
}
