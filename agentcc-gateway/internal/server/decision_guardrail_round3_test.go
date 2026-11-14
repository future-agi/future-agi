package server

import (
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/pii"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// orgWithEnabledInputCheck is org-a with an enabled PII check, which runs on
// input when a guardrail plugin is in the pipeline.
func orgWithEnabledInputCheck() *tenant.OrgConfig {
	org := defaultOrgA()
	org.Guardrails = &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{
		"pii-detection": {Enabled: true, Action: "block"},
	}}
	return org
}

// guardrails.enabled is false by default: no guardrail plugin is built and no
// org check runs, so no decision text is extracted and a deep body is not
// refused, whatever the org's stored checks say.
func TestDecisionWithoutGuardrailPluginIgnoresOrgChecks(t *testing.T) {
	g := newDecisionGateway(t, decisionGatewayOptions{orgs: map[string]*tenant.OrgConfig{"org-a": orgWithEnabledInputCheck()}})
	if g.srv.handlers.engine.Plugin("guardrails") != nil {
		t.Fatal("the default test pipeline has a guardrail plugin")
	}
	rr := g.post(t, keyByok, nestedCriteria(9000))
	if rr.Code != http.StatusOK {
		t.Fatalf("status %d: %s", rr.Code, rr.Body.String())
	}
	expectOnlyKey(t, g, orgTypeSafeKey, 1)
}

// With a guardrail plugin in the pipeline but no stage callback, an enabled
// org check still counts as an input guardrail, so the depth cap applies.
func TestDecisionGuardrailPluginWithoutCallbackKeepsTheFallback(t *testing.T) {
	g := newDecisionGateway(t, decisionGatewayOptions{orgs: map[string]*tenant.OrgConfig{"org-a": orgWithEnabledInputCheck()}})
	reg := map[string]guardrails.Guardrail{"pii-detection": pii.New(map[string]interface{}{})}
	g.srv.handlers.engine = pipeline.NewEngine(authplugin.New(g.keyStore, true), guardrails.NewPlugin(nil, reg, nil, nil, g.tenants))
	expectError(t, g.post(t, keyByok, nestedCriteria(9000)), http.StatusBadRequest, "decision_too_deep")
	expectNoCalls(t, g)
}
