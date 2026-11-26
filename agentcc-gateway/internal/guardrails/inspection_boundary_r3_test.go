package guardrails

import (
	"context"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// This fixture deliberately supplies no typed inspection evidence. A chat
// carrier/default EndpointType and internal-looking metadata are not receipts.
func unknownInspectionContext() *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.Metadata["org_id"] = "org"
	rc.Metadata["auth_key_id"] = "key-a"
	rc.Request = &models.ChatCompletionRequest{}
	rc.Response = &models.ChatCompletionResponse{}
	return rc
}

func TestR3UnknownOperationRefusesBeforeProvider(t *testing.T) {
	for _, failOpen := range []bool{false, true} {
		g := &runtimeAdmissionCheck{name: "secret", stage: StagePre}
		p := keyTestPlugin(&tenant.GuardrailConfig{FailOpen: failOpen, KeyPolicies: []*tenant.KeyGuardrailPolicy{keyTestPolicy("selected", "enforce", "block", false)}}, map[string]Guardrail{"secret": g})
		rc := unknownInspectionContext()
		rc.Metadata["inspection_complete"] = "true"
		rc.Metadata["guardrail_decision"] = "passed"
		calls := 0
		err := pipeline.NewEngine(p).Process(context.Background(), rc, func(context.Context, *models.RequestContext) error { calls++; return nil })
		apiErr, ok := err.(*models.APIError)
		if !ok || apiErr.Code != "input_guardrail_unsupported" || apiErr.Status != 403 || calls != 0 || g.calls != 0 {
			t.Errorf("fail_open=%v error=%v provider_calls=%d checker_calls=%d", failOpen, err, calls, g.calls)
		}
		rc.Release()
	}
}

func TestR3CheckAccessRunsSelectedAdmission(t *testing.T) {
	g := &runtimeAdmissionCheck{name: "secret", stage: StagePre}
	p := keyTestPlugin(&tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{keyTestPolicy("selected", "enforce", "block", false)}}, map[string]Guardrail{"secret": g})
	rc := unknownInspectionContext()
	defer rc.Release()
	err := pipeline.NewEngine(p).CheckAccess(context.Background(), rc)
	if err == nil || err.Code != "input_guardrail_unsupported" || g.calls != 0 {
		t.Fatalf("selected CheckAccess error=%v checker_calls=%d", err, g.calls)
	}
}

func TestR3MissingOutputReceiptRefused(t *testing.T) {
	g := &runtimeAdmissionCheck{name: "secret", stage: StagePost}
	p := keyTestPlugin(&tenant.GuardrailConfig{FailOpen: true, KeyPolicies: []*tenant.KeyGuardrailPolicy{keyTestPolicy("selected", "enforce", "block", false)}}, map[string]Guardrail{"secret": g})
	rc := unknownInspectionContext()
	defer rc.Release()
	res := p.ProcessResponse(context.Background(), rc)
	if res.Error == nil || res.Error.Code != "output_guardrail_unsupported" || g.calls != 0 {
		t.Fatalf("missing output receipt error=%v checker_calls=%d", res.Error, g.calls)
	}
}

func TestR3UnknownMonitorIsTruthful(t *testing.T) {
	for _, stage := range []Stage{StagePre, StagePost} {
		g := &runtimeAdmissionCheck{name: "secret", stage: stage}
		p := keyTestPlugin(&tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{keyTestPolicy("selected", "monitor", "block", false)}}, map[string]Guardrail{"secret": g})
		rc := unknownInspectionContext()
		var res pipeline.PluginResult
		if stage == StagePre {
			res = p.ProcessRequest(context.Background(), rc)
		} else {
			res = p.ProcessResponse(context.Background(), rc)
		}
		if res.Error != nil || g.calls != 0 || !rc.Flags.GuardrailMonitored || rc.Flags.GuardrailTriggered || len(rc.GuardrailResults) != 1 || rc.GuardrailResults[0].Outcome != "logged" || !strings.Contains(rc.GuardrailResults[0].Message, "guardrail_unavailable") {
			t.Errorf("stage=%v result=%+v checker_calls=%d observations=%+v", stage, res, g.calls, rc.GuardrailResults)
		}
		rc.Release()
	}
}
