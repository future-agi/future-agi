package guardrails

import (
	"context"
	"errors"
	"fmt"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type invalidStoredOutputCheck struct{ runtimeAdmissionCheck }

func (*invalidStoredOutputCheck) ValidateConfig() error {
	return errors.New("invalid synthetic config")
}

func storedInspectionContext(metadataOnly, complete bool) *models.RequestContext {
	rc := models.AcquireRequestContext()
	contract := models.InspectionContract{Operation: "stored.read", Input: models.InspectionNotApplicable, Output: models.InspectionSupported, Reason: "stored read has no submitted content"}
	if metadataOnly {
		contract.Operation = "metadata.read"
		contract.Output = models.InspectionNotApplicable
		contract.Reason = "exact metadata-only response without stored content"
	}
	rc.Inspection = models.NewInspectionState(contract)
	rc.Inspection.BindPrincipal("org", "key-a")
	// A creator's accounting metadata cannot select or suppress reader policy.
	rc.Metadata["org_id"] = "creator-org"
	rc.Metadata["auth_key_id"] = "creator-key"
	rc.Response = &models.ChatCompletionResponse{}
	if complete {
		rc.Inspection.Complete(models.InspectionOutput, rc.Response)
	}
	return rc
}

func storedUnavailablePlugin(kind, mode, pipelineMode string, failOpen bool) *GuardrailPlugin {
	pol := keyTestPolicy("reader-current", mode, "block", false)
	check := pol.Checks["secret"]
	var registry map[string]Guardrail
	switch kind {
	case "transport":
		check.ConfigurationError = "invalid_config"
	case "invalid-post":
		delete(pol.Checks, "secret")
		pol.Checks["keyword-blocklist"] = check
		registry = map[string]Guardrail{"keyword-blocklist": &invalidStoredOutputCheck{runtimeAdmissionCheck: runtimeAdmissionCheck{name: "keyword-blocklist", stage: StagePost}}}
	case "invalid-stage":
		registry = map[string]Guardrail{"secret": &runtimeAdmissionCheck{name: "secret", stage: Stage(99)}}
	}
	return keyTestPlugin(&tenant.GuardrailConfig{FailOpen: failOpen, PipelineMode: pipelineMode, KeyPolicies: []*tenant.KeyGuardrailPolicy{pol}}, registry)
}

func TestStoredReadUnavailableChecksCannotDisappearIntoInputNA(t *testing.T) {
	for _, kind := range []string{"unknown", "transport", "invalid-post", "invalid-stage"} {
		for _, mode := range []string{"enforce", "monitor"} {
			for _, pipelineMode := range []string{"sequential", "parallel"} {
				for _, failOpen := range []bool{false, true} {
					for _, complete := range []bool{false, true} {
						t.Run(fmt.Sprintf("%s/%s/%s/open=%v/complete=%v", kind, mode, pipelineMode, failOpen, complete), func(t *testing.T) {
							p := storedUnavailablePlugin(kind, mode, pipelineMode, failOpen)
							rc := storedInspectionContext(false, complete)
							defer rc.Release()
							if err := p.AdmitSelectedRequest(context.Background(), rc); err != nil {
								t.Fatalf("output failure was prematurely executed against input N/A: %v", err)
							}
							if mode == "enforce" && (!rc.Inspection.Required(models.InspectionOutput) || rc.Inspection.ReleaseError() == nil) {
								t.Fatal("unavailable output declaration did not hold content release")
							}
							err := p.AdmitSelectedOutput(context.Background(), rc)
							wantCode := ""
							if mode == "enforce" {
								if !complete {
									wantCode = "output_guardrail_unsupported"
								} else if !failOpen {
									wantCode = "guardrail_unavailable"
								}
							}
							if (wantCode == "" && err != nil) || (wantCode != "" && (err == nil || err.Code != wantCode)) {
								t.Fatalf("output admission=%v want code=%q", err, wantCode)
							}
							if len(rc.GuardrailResults) != 1 {
								t.Fatalf("unavailable output check disappeared: %+v", rc.GuardrailResults)
							}
							if mode == "monitor" && (rc.Flags.GuardrailTriggered || !rc.Flags.GuardrailMonitored || rc.GuardrailResults[0].Outcome != "logged") {
								t.Fatal("monitor failure was not observational")
							}
							if wantCode == "" && rc.Inspection.ReleaseError() != nil {
								t.Fatalf("admitted current-policy failure cannot release: %v", rc.Inspection.ReleaseError())
							}
						})
					}
				}
			}
		}
	}
}

func TestStoredUnavailablePoliciesLeaveExactMetadataOnlyControlsAlone(t *testing.T) {
	for _, kind := range []string{"unknown", "transport", "invalid-post", "invalid-stage"} {
		p := storedUnavailablePlugin(kind, "enforce", "parallel", false)
		rc := storedInspectionContext(true, false)
		if err := p.AdmitSelectedRequest(context.Background(), rc); err != nil {
			t.Errorf("%s metadata input refused: %v", kind, err)
		}
		if err := p.AdmitSelectedOutput(context.Background(), rc); err != nil || rc.Inspection.ReleaseError() != nil || len(rc.GuardrailResults) != 0 {
			t.Errorf("%s metadata output refused: err=%v release=%v results=%+v", kind, err, rc.Inspection.ReleaseError(), rc.GuardrailResults)
		}
		rc.Release()
	}
}

func TestStoredReadRechecksUnavailablePolicyAfterCreation(t *testing.T) {
	g := &runtimeAdmissionCheck{name: "secret", stage: StagePost}
	pol := keyTestPolicy("reader-current", "enforce", "block", false)
	cfg := &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{pol}}
	p := keyTestPlugin(cfg, map[string]Guardrail{"secret": g})
	first := storedInspectionContext(false, true)
	if err := p.AdmitSelectedOutput(context.Background(), first); err != nil || g.calls != 1 {
		t.Fatalf("initial read: err=%v calls=%d", err, g.calls)
	}
	first.Release()
	pol.Checks["secret"].ConfigurationError = "invalid_config"
	p.tenantStore.Set("org", &tenant.OrgConfig{Guardrails: cfg})
	second := storedInspectionContext(false, true)
	defer second.Release()
	if err := p.AdmitSelectedOutput(context.Background(), second); err == nil || err.Code != "guardrail_unavailable" || g.calls != 1 {
		t.Fatalf("later read reused old creator/verdict or skipped unavailable output: err=%v calls=%d", err, g.calls)
	}
}
