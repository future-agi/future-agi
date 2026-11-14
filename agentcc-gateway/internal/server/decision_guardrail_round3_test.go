package server

import (
	"encoding/json"
	"net/http"
	"runtime"
	"strings"
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

// flatDecision is a decision whose state is one flat array of n small values.
func flatDecision(n int) []byte {
	return []byte(`{"model":"typesafe/jev-latest","state":[` +
		strings.TrimSuffix(strings.Repeat(`"a",`, n), ",") + `],"questions":{"q":{"type":"noul","instructions":"x"}}}`)
}

// A wide, flat body is read in place: a fixed number of allocations and a
// small multiple of its size, not a tree of a million values.
func TestDecisionGuardrailFlatBodyAllocationsAreBounded(t *testing.T) {
	body := flatDecision(1_000_000)
	allocs := testing.AllocsPerRun(3, func() {
		if _, apiErr := decisionGuardrailContent(body, 0); apiErr != nil {
			t.Fatal(apiErr)
		}
	})
	if allocs > 1000 {
		t.Errorf("allocations = %.0f for 1,000,000 elements, want a fixed bound (<= 1000)", allocs)
	}
	runtime.GC()
	var before, after runtime.MemStats
	runtime.ReadMemStats(&before)
	content, apiErr := decisionGuardrailContent(body, 0)
	runtime.ReadMemStats(&after)
	if apiErr != nil {
		t.Fatal(apiErr)
	}
	if got, limit := after.TotalAlloc-before.TotalAlloc, uint64(24*len(body)); got > limit {
		t.Errorf("allocated %d bytes for a %d-byte body, want <= %d", got, len(body), limit)
	}
	var text string
	if err := json.Unmarshal(content, &text); err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(text, "a\na\n") || !strings.HasSuffix(text, "a\nx") || strings.Count(text, "\n") != 1_000_000 {
		t.Errorf("text has %d lines, starts %q", strings.Count(text, "\n")+1, text[:min(len(text), 12)])
	}
}

// The text the guardrails get is capped; a body over the cap is refused with
// a clear 400, not cut short.
func TestDecisionGuardrailTextIsCapped(t *testing.T) {
	body := flatDecision(100)
	if _, apiErr := decisionGuardrailContent(body, 1000); apiErr != nil {
		t.Fatalf("within the cap: %v", apiErr)
	}
	_, apiErr := decisionGuardrailContent(body, 150)
	if apiErr == nil || apiErr.Status != http.StatusBadRequest || apiErr.Code != "decision_text_too_large" {
		t.Fatalf("over the cap: %+v", apiErr)
	}
}

// The in-place scan keeps the field rule and decodes what the old tree walk
// decoded: escaped keys and strings, numbers as written, arrays of questions
// and non-object questions; true, false and null hold no text.
func TestDecisionGuardrailScanMatchesTheFieldRule(t *testing.T) {
	body := `{"model":"typesafe/jev-latest","questions":[{"type":"choice","name":"n","choices":["xé",{"k\"ey":1.50e2}]},"bare",[true,null,false,-0.5]],` +
		`"meta":{"deep":` + strings.Repeat("[", 10) + strings.Repeat("]", 10) + `,"flag":true},"model2":"m2"}`
	content, apiErr := decisionGuardrailContent([]byte(body), 0)
	if apiErr != nil {
		t.Fatal(apiErr)
	}
	var text string
	if err := json.Unmarshal(content, &text); err != nil {
		t.Fatal(err)
	}
	if want := "xé\nk\"ey\n1.50e2\nbare\n-0.5\ndeep\nflag\nm2"; text != want {
		t.Fatalf("text = %q, want %q", text, want)
	}
}

// A non-content field may nest deeply: it is skipped, not refused.
func TestDecisionGuardrailSkippedFieldsAreNotDepthChecked(t *testing.T) {
	body := `{"model":"m","questions":{"q":{"type":` + strings.Repeat("[", 500) + strings.Repeat("]", 500) + `,"instructions":"ok"}}}`
	content, apiErr := decisionGuardrailContent([]byte(body), 0)
	if apiErr != nil {
		t.Fatal(apiErr)
	}
	if string(content) != `"ok"` {
		t.Fatalf("content = %s", content)
	}
}

// Invalid JSON and a body that is not an object are refused as before.
func TestDecisionGuardrailContentRefusesInvalidJSON(t *testing.T) {
	for _, body := range []string{`{"state":"s"`, `["state"]`, `{"state":"s"}}`, ``, `{"state":tru}`} {
		_, apiErr := decisionGuardrailContent([]byte(body), 0)
		if apiErr == nil || apiErr.Code != "invalid_json" {
			t.Errorf("%q: %+v, want invalid_json", body, apiErr)
		}
	}
}
