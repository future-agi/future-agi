package server

import (
	"context"
	"github.com/futureagi/agentcc-gateway/internal/models"
	capbudget "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	"github.com/futureagi/agentcc-gateway/internal/plugins/toolpolicy"
	"github.com/futureagi/agentcc-gateway/internal/toolview"
	"net/http/httptest"
	"testing"
)

var _ capbudget.NativePolicyPreparer = (*toolpolicy.Plugin)(nil)

type syntheticClaimHook struct {
	NativeCapHooks
	claims int
}

func (s *syntheticClaimHook) claimPrepared(*models.RequestContext, string, string) *models.APIError {
	s.claims++
	return nil
}

// This exercises only the server finish closure, not cap authority/settlement.
func TestNativeAttemptContextReleasedOnlyAtFinalFinish(t *testing.T) {
	for _, endpoint := range []string{"anthropic_messages", "responses", "genai_stream"} {
		rc := models.AcquireRequestContext()
		rc.Request = nil
		rc.IsStream = true
		rc.EndpointType = endpoint
		rc.Agent.DeadlineMs = 60000
		hooks := &syntheticClaimHook{}
		h := &Handlers{nativeCaps: hooks}
		callCtx, finish, refusal := h.startAttempt(context.Background(), rc, models.AttemptPrimary, "", "synthetic", "model", models.CredentialBYOK)
		if refusal != "" || finish == nil || callCtx.Err() != nil || hooks.claims != 1 {
			t.Fatal("native start refused or context released early")
		}
		finish(nil, &models.Usage{PromptTokens: 3}, context.Canceled)
		if callCtx.Err() == nil {
			t.Fatal("native final finish retained context")
		}
		finish(nil, &models.Usage{PromptTokens: 9}, context.Canceled)
		attempts := rc.AttemptsSnapshot()
		if len(attempts) != 1 || attempts[0].PromptTokens != 3 || attempts[0].InFlight || attempts[0].FinancialID == "" {
			t.Fatal("final finish duplicated or unbound")
		}
		rc.Release()
	}
}

func TestNativeMissingHooksRefuseCallerOnlyControl(t *testing.T) {
	for _, protocol := range []models.NativeProtocol{models.NativeMessages, models.NativeResponses, models.NativeGenAI} {
		for _, stream := range []bool{false, true} {
			rc := models.AcquireRequestContext()
			rc.Request = nil
			rc.IsStream = stream
			amount := 1.0
			rc.Agent.MaxCostUSD = &amount
			h := &Handlers{}
			if err := h.bindNativeEntry(httptest.NewRequest("POST", "/synthetic", nil), rc, nil, protocol); err == nil {
				t.Fatal("caller-only native bypassed missing hooks")
			}
			if rc.Request != nil {
				t.Fatal("native became chat")
			}
			rc.Release()
		}
	}
}
func TestRequiredNativeCannotFallBackToOrdinaryBody(t *testing.T) {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Request = nil
	rc.Routing.ComputerUse = models.ComputerUseRequirement{Required: true, Protocol: models.NativeResponses, Action: "generate", ToolProtocol: "synthetic", VendorVersion: "test"}
	view := toolview.Responses([]byte(`{"input":"synthetic"}`))
	h := &Handlers{}
	if err := h.attachNativeEntry(context.Background(), rc, nil, view, nil, nil, "model"); err == nil {
		t.Fatal("required CUA downgraded")
	}
	if !rc.Routing.ComputerUse.Required || rc.Request != nil {
		t.Fatal("restriction cleared")
	}
}
