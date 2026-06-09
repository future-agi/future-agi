package toolperm

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestToolPermGuardrail_Allowlist_Passes(t *testing.T) {
	g := New("test-perm", map[string]interface{}{
		"mode":     "allowlist",
		"tools":    "file_read,file_write,search",
		"apply_to": "request",
		"provider": "tool_permission",
	})

	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Tools: []models.Tool{
				{Function: models.ToolFunction{Name: "file_read"}},
				{Function: models.ToolFunction{Name: "search"}},
			},
		},
	}

	result := g.Check(context.Background(), input)
	if !result.Pass {
		t.Errorf("expected pass, got fail: %s", result.Message)
	}
}

func TestToolPermGuardrail_Allowlist_Blocks(t *testing.T) {
	g := New("test-perm", map[string]interface{}{
		"mode":     "allowlist",
		"tools":    "file_read,search",
		"apply_to": "request",
		"provider": "tool_permission",
	})

	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Tools: []models.Tool{
				{Function: models.ToolFunction{Name: "file_read"}},
				{Function: models.ToolFunction{Name: "db_delete"}},
			},
		},
	}

	result := g.Check(context.Background(), input)
	if result.Pass {
		t.Error("expected fail for unlisted tool, got pass")
	}
	if result.Score != 1.0 {
		t.Errorf("expected score 1.0, got %f", result.Score)
	}
}

func TestToolPermGuardrail_Denylist_Blocks(t *testing.T) {
	g := New("test-perm", map[string]interface{}{
		"mode":     "denylist",
		"tools":    "db_delete,rm_file",
		"apply_to": "request",
		"provider": "tool_permission",
	})

	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Tools: []models.Tool{
				{Function: models.ToolFunction{Name: "db_delete"}},
			},
		},
	}

	result := g.Check(context.Background(), input)
	if result.Pass {
		t.Error("expected fail for denied tool, got pass")
	}
}

func TestToolPermGuardrail_Denylist_Passes(t *testing.T) {
	g := New("test-perm", map[string]interface{}{
		"mode":     "denylist",
		"tools":    "db_delete,rm_file",
		"apply_to": "request",
		"provider": "tool_permission",
	})

	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Tools: []models.Tool{
				{Function: models.ToolFunction{Name: "file_read"}},
				{Function: models.ToolFunction{Name: "search"}},
			},
		},
	}

	result := g.Check(context.Background(), input)
	if !result.Pass {
		t.Errorf("expected pass, got fail: %s", result.Message)
	}
}

func TestToolPermGuardrail_Wildcard(t *testing.T) {
	g := New("test-perm", map[string]interface{}{
		"mode":     "denylist",
		"tools":    "file_*,db_*",
		"apply_to": "request",
		"provider": "tool_permission",
	})

	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Tools: []models.Tool{
				{Function: models.ToolFunction{Name: "file_write"}},
			},
		},
	}

	result := g.Check(context.Background(), input)
	if result.Pass {
		t.Error("expected fail for wildcard-matched tool, got pass")
	}
}

func TestToolPermGuardrail_AuditMode(t *testing.T) {
	g := New("test-perm", map[string]interface{}{
		"mode":     "audit",
		"tools":    "db_*",
		"apply_to": "request",
		"provider": "tool_permission",
	})

	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Tools: []models.Tool{
				{Function: models.ToolFunction{Name: "db_delete"}},
			},
		},
	}

	result := g.Check(context.Background(), input)
	if !result.Pass {
		t.Error("audit mode should always pass")
	}
	if result.Details == nil {
		t.Error("audit mode should populate details")
	}
}

func TestToolPermGuardrail_EmptyTools(t *testing.T) {
	g := New("test-perm", map[string]interface{}{
		"mode":     "denylist",
		"tools":    "db_delete",
		"apply_to": "request",
		"provider": "tool_permission",
	})

	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{},
	}

	result := g.Check(context.Background(), input)
	if !result.Pass {
		t.Error("empty tools should pass")
	}
}

func TestToolPermGuardrail_ResponseToolCalls(t *testing.T) {
	g := New("test-perm", map[string]interface{}{
		"mode":     "denylist",
		"tools":    "dangerous_tool",
		"apply_to": "both",
		"provider": "tool_permission",
	})

	tcContent, _ := json.Marshal("hello")
	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{Model: "test"},
		Response: &models.ChatCompletionResponse{
			Choices: []models.Choice{
				{
					Message: models.Message{
						Role:    "assistant",
						Content: tcContent,
						ToolCalls: []models.ToolCall{
							{ID: "tc1", Type: "function", Function: models.FunctionCall{Name: "dangerous_tool", Arguments: "{}"}},
						},
					},
				},
			},
		},
	}

	result := g.Check(context.Background(), input)
	if result.Pass {
		t.Error("expected fail for denied tool in response, got pass")
	}
}

func TestToolPermGuardrail_NilInput(t *testing.T) {
	g := New("test-perm", map[string]interface{}{
		"mode":     "denylist",
		"tools":    "x",
		"apply_to": "both",
		"provider": "tool_permission",
	})

	result := g.Check(context.Background(), nil)
	if !result.Pass {
		t.Error("nil input should pass")
	}
}

func TestIsToolPermConfig(t *testing.T) {
	if !IsToolPermConfig(map[string]interface{}{"provider": "tool_permission"}) {
		t.Error("should detect tool_permission provider")
	}
	if IsToolPermConfig(map[string]interface{}{"provider": "lakera"}) {
		t.Error("should not detect lakera as tool_permission")
	}
	if IsToolPermConfig(nil) {
		t.Error("nil should return false")
	}
}
