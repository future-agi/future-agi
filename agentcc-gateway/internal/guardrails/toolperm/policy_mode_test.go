package toolperm

import (
	"context"
	"fmt"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestInvalidPolicyModeFailsExplicitly(t *testing.T) {
	request := &guardrails.CheckInput{Request: &models.ChatCompletionRequest{
		Tools: []models.Tool{{Function: models.ToolFunction{Name: "qa_blocked_tool"}}},
	}}
	for _, mode := range []interface{}{"sync", "async", "", "unknown", 7, nil} {
		t.Run(fmt.Sprintf("%v", mode), func(t *testing.T) {
			g := New("tool-permissions", map[string]interface{}{"mode": mode, "tools": "qa_blocked_*"})
			for _, input := range []*guardrails.CheckInput{request, {}, nil} {
				result := g.Check(context.Background(), input)
				if result.Pass || result.Score != 1 || !strings.Contains(result.Message, "invalid tool permission mode") {
					t.Fatalf("invalid policy must fail explicitly, got %+v", result)
				}
			}
		})
	}
}

func TestPolicyDefaultAndAuditRemainDistinct(t *testing.T) {
	input := &guardrails.CheckInput{Request: &models.ChatCompletionRequest{
		Tools: []models.Tool{{Function: models.ToolFunction{Name: "qa_blocked_tool"}}},
	}}
	if result := New("default", map[string]interface{}{"tools": "qa_blocked_*"}).Check(context.Background(), input); result.Pass {
		t.Fatal("omitted mode must retain the denylist default")
	}
	if result := New("audit", map[string]interface{}{"mode": "audit", "tools": "qa_blocked_*"}).Check(context.Background(), input); !result.Pass {
		t.Fatal("valid audit policy must retain its intentional pass behavior")
	}
}
