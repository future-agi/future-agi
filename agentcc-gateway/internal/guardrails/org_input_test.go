package guardrails

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

type inputMutationCheck struct {
	started chan struct{}
	finish  chan struct{}
	done    chan struct{}
}

func (g *inputMutationCheck) Name() string       { return "late-mutator" }
func (g *inputMutationCheck) Stage() Stage       { return StagePre }
func (g *inputMutationCheck) MutatesInput() bool { return true }
func (g *inputMutationCheck) Check(_ context.Context, input *CheckInput) *CheckResult {
	close(g.started)
	<-g.finish
	input.Request.Messages[0].Content[1] = 'X'
	input.Response.Choices[0].Message.Content[1] = 'Y'
	input.Metadata["org_id"] = "other"
	close(g.done)
	return &CheckResult{Pass: true}
}

func TestOrgCheckTimeoutOwnsEntireInput(t *testing.T) {
	for _, failOpen := range []bool{false, true} {
		t.Run(map[bool]string{false: "closed", true: "open"}[failOpen], func(t *testing.T) {
			input := &CheckInput{
				Request:  &models.ChatCompletionRequest{Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"before"`)}}},
				Response: &models.ChatCompletionResponse{Choices: []models.Choice{{Message: models.Message{Role: "assistant", Content: json.RawMessage(`"after"`)}}}},
				Metadata: map[string]string{"org_id": "original"},
			}
			g := &inputMutationCheck{make(chan struct{}), make(chan struct{}), make(chan struct{})}
			p := &GuardrailPlugin{}
			result := p.runOrgCheck(context.Background(), g, input, 20*time.Millisecond, failOpen)
			if result == nil || result.Err == nil || result.FailureAdmitted != failOpen {
				t.Fatalf("outage must retain its error and explicit admission policy: %+v", result)
			}
			close(g.finish)
			<-g.done
			if string(input.Request.Messages[0].Content) != `"before"` || string(input.Response.Choices[0].Message.Content) != `"after"` || input.Metadata["org_id"] != "original" {
				t.Fatalf("late checker mutated caller-owned input: %+v", input)
			}
		})
	}
}

func TestCloneCheckInputPreservesExtras(t *testing.T) {
	var req models.ChatCompletionRequest
	if err := json.Unmarshal([]byte(`{"model":"example","messages":[{"role":"user","content":"hello"}],"custom_flag":{"nested":[1,2]}}`), &req); err != nil {
		t.Fatal(err)
	}
	owned, err := cloneCheckInput(&CheckInput{Request: &req, Metadata: map[string]string{"org_id": "original"}})
	if err != nil {
		t.Fatal(err)
	}
	original, _ := json.Marshal(req)
	cloned, _ := json.Marshal(owned.Request)
	if string(original) != string(cloned) {
		t.Fatalf("snapshot changed wire content: %s != %s", original, cloned)
	}
	owned.Request.Messages[0].Content[1] = 'X'
	owned.Metadata["org_id"] = "other"
	if string(req.Messages[0].Content) != `"hello"` {
		t.Fatal("message bytes shared")
	}
}
