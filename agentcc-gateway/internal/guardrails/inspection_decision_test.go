package guardrails

import (
	"context"
	"encoding/json"
	"fmt"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestSelectedCompleteDecisionCannotReleaseChangedPayload(t *testing.T) {
	g := &runtimeAdmissionCheck{name: "secret", stage: StagePre}
	p := keyTestPlugin(&tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{keyTestPolicy("selected", "enforce", "block", false)}}, map[string]Guardrail{"secret": g})
	rc := keyTestContext("key-a")
	defer rc.Release()
	if err := p.AdmitSelectedOperation(context.Background(), rc); err != nil {
		t.Fatal(err)
	}
	if g.calls != 0 || rc.Inspection.ReleaseError() == nil {
		t.Fatal("declaration preflight executed a check or granted a decision")
	}
	if err := p.AdmitSelectedRequest(context.Background(), rc); err != nil {
		t.Fatal(err)
	}
	if g.calls != 1 || rc.Inspection.ReleaseError() != nil {
		t.Fatal("complete passing check did not grant a decision")
	}
	rc.Request.Messages[0].Content = json.RawMessage(`"changed after decision"`)
	if err := rc.Inspection.ReleaseError(); err == nil || err.Code != "input_guardrail_unsupported" {
		t.Fatalf("changed payload retained receipt: %v", err)
	}
	if err := p.AdmitSelectedRequest(context.Background(), rc); err == nil || err.Code != "input_guardrail_unsupported" || g.calls != 1 {
		t.Fatalf("stale projection reran as complete: err=%v calls=%d", err, g.calls)
	}
}

func TestSelectedCheckAccessAndProcessShareDecisionWithoutOrgReplay(t *testing.T) {
	selected := &runtimeAdmissionCheck{name: "secret", stage: StagePre}
	org := &runtimeAdmissionCheck{name: "org-check", stage: StagePre}
	cfg := &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{"org-check": {Enabled: true, Action: "block"}}, KeyPolicies: []*tenant.KeyGuardrailPolicy{keyTestPolicy("selected", "enforce", "block", false)}}
	p := keyTestPlugin(cfg, map[string]Guardrail{"secret": selected, "org-check": org})
	e := pipeline.NewEngine(p)
	rc := keyTestContext("key-a")
	defer rc.Release()
	if err := e.CheckAccess(context.Background(), rc); err != nil {
		t.Fatal(err)
	}
	if selected.calls != 1 || org.calls != 0 {
		t.Fatalf("selected seam ran wrong checks: selected=%d org=%d", selected.calls, org.calls)
	}
	providers := 0
	if err := e.Process(context.Background(), rc, func(context.Context, *models.RequestContext) error { providers++; return nil }); err != nil {
		t.Fatal(err)
	}
	if selected.calls != 1 || org.calls != 1 || providers != 1 {
		t.Fatalf("decision was replayed or org skipped: selected=%d org=%d providers=%d", selected.calls, org.calls, providers)
	}
}

func TestSelectedSupportedOutputStillRequiresActualProjection(t *testing.T) {
	for _, complete := range []bool{false, true} {
		t.Run(fmt.Sprint(complete), func(t *testing.T) {
			g := &runtimeAdmissionCheck{name: "secret", stage: StagePost}
			p := keyTestPlugin(&tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{keyTestPolicy("selected", "enforce", "block", false)}}, map[string]Guardrail{"secret": g})
			rc := keyTestContext("key-a")
			defer rc.Release()
			calls := 0
			err := pipeline.NewEngine(p).Process(context.Background(), rc, func(_ context.Context, rc *models.RequestContext) error {
				calls++
				rc.Response = &models.ChatCompletionResponse{Model: "synthetic", Usage: &models.Usage{TotalTokens: 7}}
				if complete {
					rc.Inspection.Complete(models.InspectionOutput, rc.Response)
				}
				return nil
			})
			if calls != 1 {
				t.Fatalf("supported output preflight unexpectedly prevented provider: %d", calls)
			}
			if complete {
				if err != nil || g.calls != 1 || rc.Inspection.ReleaseError() != nil {
					t.Fatalf("complete output failed: error=%v calls=%d release=%v", err, g.calls, rc.Inspection.ReleaseError())
				}
			} else {
				apiErr, ok := err.(*models.APIError)
				if !ok || apiErr.Code != "output_guardrail_unsupported" || g.calls != 0 || rc.Inspection.ReleaseError() == nil || rc.Response.Usage.TotalTokens != 7 {
					t.Fatalf("missing output evidence: error=%v calls=%d response=%+v", err, g.calls, rc.Response)
				}
			}
		})
	}
}

func TestSelectedOutputUsesReaderNotCreatorAndRechecksCurrentPolicy(t *testing.T) {
	g := &runtimeAdmissionCheck{name: "secret", stage: StagePost}
	pol := keyTestPolicy("reader-policy", "enforce", "block", false)
	pol.KeyIDs = []string{"reader"}
	cfg := &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{pol}}
	p := keyTestPlugin(cfg, map[string]Guardrail{"secret": g})
	read := func(complete bool) *models.APIError {
		rc := keyTestContext("reader")
		defer rc.Release()
		// Creator charging attribution is deliberately different from the
		// authenticated inspection principal and even its organization.
		rc.Metadata["auth_key_id"] = "creator"
		rc.Metadata["org_id"] = "creator-org"
		rc.Response = &models.ChatCompletionResponse{}
		if complete {
			rc.Inspection.Complete(models.InspectionOutput, rc.Response)
		}
		return pipeline.NewEngine(p).AdmitSelectedOutput(context.Background(), rc)
	}
	if err := read(false); err == nil || err.Code != "output_guardrail_unsupported" {
		t.Fatalf("creator accounting displaced reader policy: %v", err)
	}
	if err := read(true); err != nil || g.calls != 1 {
		t.Fatalf("reader output not inspected: %v calls=%d", err, g.calls)
	}
	// A later read resolves the reader's changed policy, not a creator or prior
	// read verdict. The unavailable checker follows ordinary fail-closed rules.
	pol.Checks = map[string]*tenant.GuardrailCheck{"missing-check": {Enabled: true, Action: "block"}}
	p.tenantStore.Set("org", &tenant.OrgConfig{Guardrails: cfg})
	rc := keyTestContext("reader")
	defer rc.Release()
	if err := p.AdmitSelectedRequest(context.Background(), rc); err == nil || err.Code != "guardrail_unavailable" {
		t.Fatalf("new read reused an old decision: %v", err)
	}
}

func TestSelectedExactNotApplicableAndUnboundReceipt(t *testing.T) {
	for _, tc := range []struct {
		name   string
		reason string
		bound  bool
		wantOK bool
	}{{"reasoned", "metadata-only operation", true, true}, {"missing-reason", "", true, false}, {"unbound", "metadata-only operation", false, false}} {
		t.Run(tc.name, func(t *testing.T) {
			g := &runtimeAdmissionCheck{name: "secret", stage: StagePre}
			p := keyTestPlugin(&tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{keyTestPolicy("selected", "enforce", "block", false)}}, map[string]Guardrail{"secret": g})
			rc := unknownInspectionContext()
			defer rc.Release()
			rc.Inspection = models.NewInspectionState(models.InspectionContract{Operation: "metadata.get", Input: models.InspectionNotApplicable, Output: models.InspectionNotApplicable, Reason: tc.reason})
			if tc.bound {
				rc.Inspection.BindPrincipal("org", "key-a")
			}
			err := p.AdmitSelectedRequest(context.Background(), rc)
			if (err == nil) != tc.wantOK || g.calls != 0 || (rc.Inspection.ReleaseError() == nil) != tc.wantOK {
				t.Fatalf("N/A admission=%v calls=%d release=%v", err, g.calls, rc.Inspection.ReleaseError())
			}
		})
	}
}

func TestSelectedProjectionPointerTakesPrecedenceOverCarrier(t *testing.T) {
	g := &keyTestCheck{name: "secret"}
	p := keyTestPlugin(&tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{keyTestPolicy("selected", "enforce", "block", false)}}, map[string]Guardrail{"secret": g})
	rc := keyTestContext("key-a")
	defer rc.Release()
	rc.GuardrailRequest = rc.Request
	rc.Request = &models.ChatCompletionRequest{Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"safe carrier"`)}}}
	rc.Inspection.Complete(models.InspectionInput, rc.GuardrailRequest)
	if err := p.AdmitSelectedRequest(context.Background(), rc); err == nil || err.Code != "content_blocked" || g.calls != 1 {
		t.Fatalf("carrier displaced projected content: %v calls=%d", err, g.calls)
	}
}

func TestSelectedDecisionCannotSurvivePolicyRevision(t *testing.T) {
	g := &keyTestCheck{name: "secret"}
	pol := keyTestPolicy("selected", "monitor", "block", false)
	cfg := &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{pol}}
	p := keyTestPlugin(cfg, map[string]Guardrail{"secret": g})
	rc := keyTestContext("key-a")
	defer rc.Release()
	if err := p.AdmitSelectedRequest(context.Background(), rc); err != nil || g.calls != 1 {
		t.Fatalf("monitor admission: error=%v calls=%d", err, g.calls)
	}
	pol.Mode = "enforce"
	if err := p.AdmitSelectedRequest(context.Background(), rc); err == nil || err.Code != "content_blocked" || g.calls != 2 {
		t.Fatalf("old Monitor decision admitted current Block policy: error=%v calls=%d", err, g.calls)
	}
	if rc.Inspection.ReleaseError() == nil {
		t.Fatal("blocked current policy retained a releasable decision")
	}
}

func TestSelectedOutputRevisionPreservesCompletedInputInvocation(t *testing.T) {
	for _, withOutputCheck := range []bool{false, true} {
		t.Run(fmt.Sprintf("output-check=%t", withOutputCheck), func(t *testing.T) {
			// The unavailable PRE check is admitted under the invocation's
			// fail-open snapshot. A subsequent POST resolution must not erase
			// that completed decision when the tenant config is replaced.
			pol := keyTestPolicy("selected", "enforce", "block", false)
			output := &runtimeAdmissionCheck{name: "output", stage: StagePost}
			if withOutputCheck {
				pol.Checks["output"] = &tenant.GuardrailCheck{Enabled: true, Action: "block"}
			}
			cfg := &tenant.GuardrailConfig{FailOpen: true, KeyPolicies: []*tenant.KeyGuardrailPolicy{pol}}
			p := keyTestPlugin(cfg, map[string]Guardrail{"output": output})
			rc := keyTestContext("key-a")
			defer rc.Release()
			providers := 0
			err := pipeline.NewEngine(p).Process(context.Background(), rc, func(_ context.Context, rc *models.RequestContext) error {
				providers++
				strict := *cfg
				strict.FailOpen = false
				p.tenantStore.Set("org", &tenant.OrgConfig{Guardrails: &strict})
				rc.Response = &models.ChatCompletionResponse{}
				rc.Inspection.Complete(models.InspectionOutput, rc.Response)
				return nil
			})
			if err != nil || providers != 1 || rc.Inspection.ReleaseError() != nil {
				t.Fatalf("completed invocation lost its receipt: err=%v providers=%d release=%v", err, providers, rc.Inspection.ReleaseError())
			}
			wantOutputCalls := 0
			if withOutputCheck {
				wantOutputCalls = 1
			}
			if output.calls != wantOutputCalls {
				t.Fatalf("output admission calls=%d want=%d", output.calls, wantOutputCalls)
			}
			// A new request and an independent repeated PRE admission must
			// both resolve the new strict policy, not freeze policy forever.
			next := keyTestContext("key-a")
			defer next.Release()
			for _, current := range []*models.RequestContext{next, rc} {
				if err := p.AdmitSelectedRequest(context.Background(), current); err == nil || err.Code != "guardrail_unavailable" {
					t.Fatalf("independent admission reused fail-open decision: %v", err)
				}
				if current.Inspection.ReleaseError() == nil {
					t.Fatal("failed independent admission retained a release decision")
				}
			}
		})
	}
}

func TestSelectedStoredOutputRevisionRechecksSameReceipt(t *testing.T) {
	g := &runtimeAdmissionCheck{name: "secret", stage: StagePost}
	pol := keyTestPolicy("reader-current", "enforce", "block", false)
	cfg := &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{pol}}
	p := keyTestPlugin(cfg, map[string]Guardrail{"secret": g})
	rc := storedInspectionContext(false, true)
	defer rc.Release()
	if err := p.AdmitSelectedOutput(context.Background(), rc); err != nil || g.calls != 1 || rc.Inspection.ReleaseError() != nil {
		t.Fatalf("initial stored admission: err=%v calls=%d release=%v", err, g.calls, rc.Inspection.ReleaseError())
	}
	changed := keyTestPolicy("reader-current", "enforce", "block", false)
	changed.Checks["secret"].ConfigurationError = "invalid_config"
	p.tenantStore.Set("org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{changed}}})
	if err := p.AdmitSelectedOutput(context.Background(), rc); err == nil || err.Code != "guardrail_unavailable" || g.calls != 1 {
		t.Fatalf("repeated stored admission reused old decision: err=%v calls=%d", err, g.calls)
	}
	if rc.Inspection.ReleaseError() == nil {
		t.Fatal("unavailable current output retained a release decision")
	}
}

func TestSelectedInvalidTransportCannotResolveRealtimeDefaults(t *testing.T) {
	g := &runtimeAdmissionCheck{name: "secret", stage: StagePre}
	p := keyTestPlugin(&tenant.GuardrailConfig{}, map[string]Guardrail{"secret": g})
	p.dynamicFactory = func(string, map[string]interface{}) Guardrail {
		t.Fatal("invalid transport invoked factory")
		return nil
	}
	check := &tenant.GuardrailCheck{Enabled: true, ConfigurationError: "invalid_config"}
	resolved := p.getKeyPolicyGuardrail("org", "secret", "cached", check)
	if _, ok := resolved.(unavailableKeyPolicyCheck); !ok {
		t.Fatalf("invalid transport fell back to defaults: %T", resolved)
	}
}
