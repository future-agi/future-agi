package leakage

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func makeResponseInput(content string) *guardrails.CheckInput {
	raw, _ := json.Marshal(content)
	return &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{Model: "gpt-4o"},
		Response: &models.ChatCompletionResponse{
			Choices: []models.Choice{
				{Message: models.Message{Role: "assistant", Content: raw}},
			},
		},
		Metadata: map[string]string{},
	}
}

func TestDetect_SQLInjection(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), makeResponseInput("You can run DROP TABLE users; to delete everything"))
	if r.Pass {
		t.Fatal("expected SQL injection detection")
	}
}

func TestDetect_ShellInjection(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), makeResponseInput("Run rm -rf / to clean up"))
	if r.Pass {
		t.Fatal("expected shell injection detection")
	}
}

func TestDetect_XSS(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), makeResponseInput("Use <script>alert('xss')</script> in the page"))
	if r.Pass {
		t.Fatal("expected XSS detection")
	}
}

func TestDetect_PathTraversal(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), makeResponseInput("Read from ../../../../etc/passwd"))
	if r.Pass {
		t.Fatal("expected path traversal detection")
	}
}

func TestDetect_EnvLeak(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), makeResponseInput("The DATABASE_URL=postgres://admin:secret@db.example.com"))
	if r.Pass {
		t.Fatal("expected env leak detection")
	}
}

func TestCleanResponse(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), makeResponseInput("Here is a helpful Python function to sort a list: def sort_list(x): return sorted(x)"))
	if !r.Pass {
		t.Fatal("clean response should pass")
	}
}

func TestNilInput(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), nil)
	if !r.Pass {
		t.Fatal("nil should pass")
	}
}

func TestNilResponse(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{Model: "gpt-4o"},
	})
	if !r.Pass {
		t.Fatal("nil response should pass")
	}
}

func TestName(t *testing.T) {
	if New(nil).Name() != "data-leakage-prevention" {
		t.Error("wrong name")
	}
}

func TestStage(t *testing.T) {
	if New(nil).Stage() != guardrails.StagePost {
		t.Error("expected StagePost")
	}
}
