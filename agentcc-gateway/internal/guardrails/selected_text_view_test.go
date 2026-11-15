package guardrails

import (
	"bytes"
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestSelectedTextViewR4StrictContent(t *testing.T) {
	for _, tc := range []struct{ raw, want string }{
		{`"plain"`, `"plain"`},
		{`[]`, `""`},
		{`[{"type":"text","text":"first"},{"type":"text","text":"second"}]`, `"first\nsecond"`},
		{`[{"type":"text","text":""}]`, `""`},
	} {
		got, err := selectedTextContent(json.RawMessage(tc.raw))
		if err != nil || string(got) != tc.want {
			t.Fatalf("%s: got=%s err=%v", tc.raw, got, err)
		}
	}
	for _, raw := range []string{`null`, `{}`, `42`, `[{"type":"text","text":null}]`, `[{"type":"text","text":7}]`, `[{"type":"text","text":"safe","extra":"hidden"}]`, `[{"type":"text","text":"safe"},{"type":"image_url","image_url":{}}]`, `[{"type":"thinking","text":"hidden"}]`, `[{"type":"tool_use","text":"hidden"}]`} {
		if got, err := selectedTextContent(json.RawMessage(raw)); err == nil {
			t.Fatalf("unsupported shape normalized: %s => %s", raw, got)
		}
	}
}

func TestSelectedTextViewR4PreservesContextAndOwnsBytes(t *testing.T) {
	var request models.ChatCompletionRequest
	if err := json.Unmarshal([]byte(`{"model":"synthetic","user":"synthetic-user","temperature":0.3,"metadata":{"context":"synthetic"},"messages":[{"role":"system","name":"synthetic-system","content":[{"type":"text","text":"first"},{"type":"text","text":"second"}]},{"role":"user","content":"unchanged"}]}`), &request); err != nil {
		t.Fatal(err)
	}
	input := &CheckInput{Request: &request, Metadata: map[string]string{"org_id": "org", "user": "synthetic-user", "evaluation_context": "context"}, Response: &models.ChatCompletionResponse{Model: "synthetic", Usage: &models.Usage{TotalTokens: 8}, Choices: []models.Choice{{Index: 0, Message: models.Message{Role: "assistant", Content: json.RawMessage(`[{"type":"text","text":"response first"}]`)}}, {Index: 1, Message: models.Message{Role: "assistant", Content: json.RawMessage(`[{"type":"text","text":"response second"}]`)}}}}}
	before, _ := json.Marshal(input)
	for _, stage := range []Stage{StagePre, StagePost} {
		view, err := selectedTextView(input, stage)
		if err != nil {
			t.Fatal(err)
		}
		if !view.InspectionOnly || view.Request.User != "synthetic-user" || view.Request.Model != "synthetic" || view.Request.Messages[0].Name != "synthetic-system" || view.Metadata["evaluation_context"] != "context" || view.Response.Usage.TotalTokens != 8 || len(view.Response.Choices) != 2 {
			t.Fatalf("context missing: %+v", view)
		}
		if stage == StagePre && string(view.Request.Messages[0].Content) != `"first\nsecond"` {
			t.Fatalf("missing parts: %s", view.Request.Messages[0].Content)
		}
		if stage == StagePost && string(view.Response.Choices[1].Message.Content) != `"response second"` {
			t.Fatalf("missing choice: %+v", view.Response.Choices)
		}
		view.Request.Messages[0].Content[0] = 'x'
		view.Request.Extra["metadata"][0] = 'x'
		view.Metadata["evaluation_context"] = "changed"
		view.Response.Usage.TotalTokens = 99
		view.Response.Choices[0].Message.Content[0] = 'x'
		after, _ := json.Marshal(input)
		if !bytes.Equal(before, after) {
			t.Fatalf("shared snapshot mutated original: %s", after)
		}
	}
}

type textViewR4StringCheck struct{ name string }

func (g *textViewR4StringCheck) Name() string { return g.name }
func (*textViewR4StringCheck) Stage() Stage   { return StagePre }
func (*textViewR4StringCheck) Check(_ context.Context, input *CheckInput) *CheckResult {
	var text string
	if json.Unmarshal(input.Request.Messages[0].Content, &text) != nil {
		return &CheckResult{Pass: true}
	}
	return &CheckResult{Pass: !strings.Contains(text, "secret"), Score: 1}
}

func TestSelectedTextViewR4ProjectionRemediationCannotHideBlock(t *testing.T) {
	for _, sequential := range []bool{false, true} {
		input := &CheckInput{Request: &models.ChatCompletionRequest{Messages: []models.Message{{Role: "user", Content: json.RawMessage(`[{"type":"text","text":"secret"}]`)}}}, InspectionOnly: true}
		before, _ := json.Marshal(input)
		checks := []orgCheck{
			{name: "pii", g: &keyTestCheck{name: "pii", mutate: true}, check: &tenant.GuardrailCheck{Enabled: true, Action: "log"}},
			{name: "text-reader", g: selectedTextCheck{&textViewR4StringCheck{name: "text-reader"}}, check: &tenant.GuardrailCheck{Enabled: true, Action: "block", ConfidenceThreshold: .5}},
		}
		p := &GuardrailPlugin{}
		var outcomes []*CheckResult
		var blocked int
		if sequential {
			outcomes, blocked = p.runOrgChecksSequential(context.Background(), checks, input, time.Second, false)
		} else {
			outcomes, blocked = p.runOrgChecksParallel(context.Background(), checks, input, time.Second, false)
		}
		after, _ := json.Marshal(input)
		if blocked != 1 || !bytes.Equal(before, after) || !outcomes[0].InputRewritten {
			t.Fatalf("projection hidden: blocked=%d before=%s after=%s outcomes=%+v", blocked, before, after, outcomes)
		}
	}
}

func TestSelectedTextViewR4CustomNamesRetainOriginalContract(t *testing.T) {
	for _, name := range []string{"topic-restriction", "language-detection", "system-prompt-protection", "data-leakage-prevention", "hallucination-detection"} {
		if usesSelectedTextView(&textViewR4StringCheck{name: name}) {
			t.Fatalf("custom check %s was adapted", name)
		}
	}
}

func TestSelectedTextViewR4SeparateSelectedProjection(t *testing.T) {
	rc := keyTestContext("key-a")
	defer rc.Release()
	legacy := rc.Request
	rc.SelectedGuardrailRequest = &models.ChatCompletionRequest{Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"selected"`)}}}
	if checkInput(rc, false).Request != legacy || selectedCheckInput(rc, false).Request != rc.SelectedGuardrailRequest || !selectedCheckInput(rc, false).InspectionOnly || inspectionPayload(rc, StagePre) != rc.SelectedGuardrailRequest {
		t.Fatal("selected projection changed legacy view or lost receipt binding")
	}
}
