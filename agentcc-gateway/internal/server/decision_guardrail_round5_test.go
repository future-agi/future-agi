package server

import (
	"encoding/json"
	"net/http"
	"runtime"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/pii"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// decisionWithOrgPII runs the gateway with a PII org check (not a static
// rule) that remediates with mode, so its input is snapshotted per check.
func decisionWithOrgPII(t *testing.T, mode string) *decisionGateway {
	t.Helper()
	org := defaultOrgA()
	org.Guardrails = &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{
		"pii-detection": {Enabled: true, Action: "block"},
	}}
	g := newDecisionGateway(t, decisionGatewayOptions{orgs: map[string]*tenant.OrgConfig{"org-a": org}})
	reg := map[string]guardrails.Guardrail{"pii-detection": pii.New(map[string]interface{}{"remediation": mode})}
	plugin := guardrails.NewPlugin(nil, reg, nil, nil, g.tenants)
	g.srv.handlers.engine = pipeline.NewEngine(authplugin.New(g.keyStore, true), plugin)
	g.srv.SetOrgInputGuardrails(plugin.HasOrgInputChecks)
	return g
}

// A clean decision whose text has '<', '>', '&' and U+2028 reaches the
// vendor through a remediating org check: the check's snapshot keeps the
// content byte for byte, so it is not mistaken for a redaction.
func TestDecisionOrgRemediatingCheckKeepsCleanHTMLText(t *testing.T) {
	for _, mode := range []string{"mask", "redact", "hash"} {
		t.Run(mode, func(t *testing.T) {
			g := decisionWithOrgPII(t, mode)
			body := `{"model":"typesafe/jev-latest","state":"score > 5 & no <errors>` + " " + `ok","questions":{"q":{"type":"noul","instructions":"x"}}}`
			rr := g.post(t, keyByok, body)
			if rr.Code != http.StatusOK {
				t.Fatalf("status %d: %s", rr.Code, rr.Body.String())
			}
			expectOnlyKey(t, g, orgTypeSafeKey, 1)
		})
	}
}

// PII in the same kind of body is still refused, before the vendor.
func TestDecisionOrgRemediatingCheckStillRefusesPII(t *testing.T) {
	g := decisionWithOrgPII(t, "mask")
	body := `{"model":"typesafe/jev-latest","state":"a < b & write to jane.doe@example.com","questions":{"q":{"type":"noul","instructions":"x"}}}`
	expectError(t, g.post(t, keyByok, body), http.StatusForbidden, "content_blocked")
	expectNoCalls(t, g)
}

// Through the org-check path, a body of '<' near its size stays a small
// multiple of the body in allocation: the snapshot does not re-escape it.
func TestDecisionOrgCheckSnapshotStaysBounded(t *testing.T) {
	g := decisionWithOrgPII(t, "mask")
	text := strings.Repeat("<", 2<<20)
	body := `{"model":"typesafe/jev-latest","state":"` + text + `","questions":{"q":{"type":"noul","instructions":"x"}}}`
	runtime.GC()
	var before, after runtime.MemStats
	runtime.ReadMemStats(&before)
	rr := g.post(t, keyByok, body)
	runtime.ReadMemStats(&after)
	if rr.Code != http.StatusOK {
		t.Fatalf("status %d: %s", rr.Code, rr.Body.String()[:min(rr.Body.Len(), 300)])
	}
	if got, limit := after.TotalAlloc-before.TotalAlloc, uint64(40*len(body)); got > limit {
		t.Errorf("allocated %d bytes for a %d-byte body through an org check, want <= %d", got, len(body), limit)
	}
}

// A re-encoded but equal text is not a rewrite; a changed text is.
func TestDecisionGuardrailRewrittenComparesText(t *testing.T) {
	original := json.RawMessage(`"a < b & c"`)
	rc := &models.RequestContext{GuardrailRequest: &models.ChatCompletionRequest{Messages: []models.Message{{Role: "user"}}}}
	for content, rewritten := range map[string]bool{
		`"a < b & c"`:               false,
		"\"a \\u003c b \\u0026 c\"": false,
		`"a [REDACTED] b & c"`:      true,
		`not json`:                  true,
	} {
		rc.GuardrailRequest.Messages[0].Content = json.RawMessage(content)
		if got := decisionGuardrailRewritten(rc, original); got != rewritten {
			t.Errorf("%s: rewritten = %v, want %v", content, got, rewritten)
		}
	}
}
