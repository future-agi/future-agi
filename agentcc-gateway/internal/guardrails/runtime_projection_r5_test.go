package guardrails

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestRuntimeR5LegacyCheckInputPrecedence(t *testing.T) {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Request = &models.ChatCompletionRequest{Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"legacy request"`)}}}
	rc.GuardrailRequest = &models.ChatCompletionRequest{Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"legacy fallback"`)}}}
	rc.SelectedGuardrailRequest = &models.ChatCompletionRequest{Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"selected"`)}}}
	rc.Response = &models.ChatCompletionResponse{Choices: []models.Choice{{Message: models.Message{Content: json.RawMessage(`"legacy response"`)}}}}
	rc.GuardrailResponse = &models.ChatCompletionResponse{Choices: []models.Choice{{Message: models.Message{Content: json.RawMessage(`"legacy output projection"`)}}}}
	rc.SelectedGuardrailResponse = &models.ChatCompletionResponse{Choices: []models.Choice{{Message: models.Message{Content: json.RawMessage(`"all selected output"`)}}}}
	rc.GuardrailInspectionOnly = true
	for _, withResponse := range []bool{false, true} {
		legacy := checkInput(rc, withResponse)
		if legacy.Request != rc.Request || legacy.InspectionOnly || (withResponse && legacy.Response != rc.GuardrailResponse) || (!withResponse && legacy.Response != nil) {
			t.Fatal("legacy precedence or mutable working view differs from dev")
		}
		selected := selectedCheckInput(rc, withResponse)
		if selected.Request != rc.SelectedGuardrailRequest || !selected.InspectionOnly || (withResponse && selected.Response != rc.SelectedGuardrailResponse) {
			t.Fatal("selected view leaked into legacy or lost complete output")
		}
	}
	rc.Request = nil
	if checkInput(rc, false).Request != rc.GuardrailRequest || checkInput(rc, false).InspectionOnly {
		t.Fatal("native legacy fallback differs from dev")
	}
}

func TestRuntimeR5ProjectionCopyReceiptLifecycle(t *testing.T) {
	for _, stage := range []Stage{StagePre, StagePost} {
		for _, complete := range []bool{false, true} {
			rc := models.AcquireRequestContext()
			rc.GuardrailRequest = &models.ChatCompletionRequest{Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"original"`)}}, Extra: map[string]json.RawMessage{"metadata": json.RawMessage(`{"a":1}`)}}
			rc.GuardrailResponse = &models.ChatCompletionResponse{Choices: []models.Choice{{Index: 2, Message: models.Message{Role: "assistant", Content: json.RawMessage(`"original"`)}}}, Usage: &models.Usage{TotalTokens: 3}}
			rc.Inspection = models.NewInspectionState(models.InspectionContract{Operation: "native.copy", Input: models.InspectionSupported, Output: models.InspectionSupported})
			rc.Inspection.BindPrincipal("org", "key")
			rc.Inspection.Require(inspectionStage(stage))
			if complete {
				rc.Inspection.Complete(inspectionStage(stage), inspectionPayload(rc, stage))
			}
			preserveSelectedProjection(rc, stage)
			owned := inspectionPayload(rc, stage)
			before, err := json.Marshal(owned)
			if err != nil {
				t.Fatal(err)
			}
			if rc.Inspection.IsComplete(inspectionStage(stage), owned) != complete {
				t.Fatal("copy minted or lost shape evidence")
			}
			rc.Inspection.SetPolicyRevision(inspectionStage(stage), "synthetic-policy")
			rc.Inspection.Decide(inspectionStage(stage), owned)
			// Legacy remediators may replace or mutate every nested byte. The
			// selected snapshot and the receipt's retained payload must stay owned.
			rc.GuardrailRequest.Messages[0].Content[1] = 'X'
			rc.GuardrailRequest.Extra["metadata"][0] = 'X'
			rc.GuardrailResponse.Choices[0].Message.Content[1] = 'X'
			rc.GuardrailResponse.Usage.TotalTokens = 99
			after, _ := json.Marshal(owned)
			if string(after) != string(before) || (rc.Inspection.ReleaseError() == nil) != complete {
				t.Fatal("legacy mutation changed selected payload or receipt")
			}
			state := rc.Inspection
			rc.Release()
			if (state.ReleaseError() == nil) != complete {
				t.Fatal("pool release changed the retained owned receipt")
			}
			if stage == StagePre {
				owned.(*models.ChatCompletionRequest).Messages[0].Content = json.RawMessage(`"changed"`)
			} else {
				owned.(*models.ChatCompletionResponse).Choices[0].Message.Content = json.RawMessage(`"changed"`)
			}
			if state.ReleaseError() == nil {
				t.Fatal("selected payload mutation reused old decision")
			}
		}
	}
}

type runtimeR5DelayedCheck struct {
	started chan struct{}
	resume  chan struct{}
	seen    chan string
}

func (*runtimeR5DelayedCheck) Name() string { return "delayed-selected-output" }
func (*runtimeR5DelayedCheck) Stage() Stage { return StagePost }
func (g *runtimeR5DelayedCheck) Check(_ context.Context, in *CheckInput) *CheckResult {
	close(g.started)
	<-g.resume
	g.seen <- string(in.Response.Choices[0].Message.Content)
	in.Response.Choices[0].Message.Content[1] = 'X'
	return &CheckResult{Pass: true}
}

func TestRuntimeR5SelectedAsyncSnapshotSurvivesRelease(t *testing.T) {
	rc := models.AcquireRequestContext()
	rc.SelectedGuardrailResponse = &models.ChatCompletionResponse{Choices: []models.Choice{{Message: models.Message{Role: "assistant", Content: json.RawMessage(`[{"type":"text","text":"original"}]`)}}}}
	original := rc.SelectedGuardrailResponse
	check := &runtimeR5DelayedCheck{started: make(chan struct{}), resume: make(chan struct{}), seen: make(chan string, 1)}
	finished := make(chan *CheckResult, 1)
	input := selectedCheckInput(rc, true)
	go func() {
		finished <- (selectedTextCheck{Guardrail: check}).Check(context.Background(), input)
	}()
	select {
	case <-check.started: // normalization and deep-copy have finished
	case <-time.After(time.Second):
		rc.Release()
		t.Fatal("selected snapshot did not reach delayed checker")
	}
	rc.Release()
	next := models.AcquireRequestContext()
	next.SelectedGuardrailResponse = &models.ChatCompletionResponse{Model: "next-request"}
	defer next.Release()
	close(check.resume)
	select {
	case got := <-check.seen:
		if got != `"original"` {
			t.Fatalf("delayed check read pooled request: %s", got)
		}
	case <-time.After(time.Second):
		t.Fatal("delayed selected check did not finish")
	}
	if result := <-finished; !result.Pass {
		t.Fatal("delayed checker failed")
	}
	if string(original.Choices[0].Message.Content) != `[{"type":"text","text":"original"}]` || next.SelectedGuardrailResponse.Model != "next-request" {
		t.Fatal("delayed selected checker mutated original or reused context")
	}
}
