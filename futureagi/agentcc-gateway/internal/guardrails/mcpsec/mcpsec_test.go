package mcpsec

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestMCPSecGuardrail_BlockedTool(t *testing.T) {
	g := New("test-mcp", map[string]interface{}{
		"blocked_tools":   "exec_command,delete_all",
		"provider":        "mcp_security",
		"validate_inputs": true,
	})

	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Tools: []models.Tool{
				{Function: models.ToolFunction{Name: "exec_command"}},
			},
		},
	}

	result := g.Check(context.Background(), input)
	if result.Pass {
		t.Error("expected fail for blocked tool, got pass")
	}
}

func TestMCPSecGuardrail_AllowedTool(t *testing.T) {
	g := New("test-mcp", map[string]interface{}{
		"blocked_tools":   "exec_command",
		"provider":        "mcp_security",
		"validate_inputs": true,
	})

	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Tools: []models.Tool{
				{Function: models.ToolFunction{Name: "read_file"}},
			},
		},
	}

	result := g.Check(context.Background(), input)
	if !result.Pass {
		t.Errorf("expected pass for allowed tool, got fail: %s", result.Message)
	}
}

func TestMCPSecGuardrail_InjectionInArgs(t *testing.T) {
	g := New("test-mcp", map[string]interface{}{
		"provider":        "mcp_security",
		"validate_inputs": true,
	})

	raw, _ := json.Marshal("hello")
	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Messages: []models.Message{
				{
					Role:    "assistant",
					Content: raw,
					ToolCalls: []models.ToolCall{
						{
							ID:   "tc1",
							Type: "function",
							Function: models.FunctionCall{
								Name:      "query_db",
								Arguments: `{"sql": "DROP TABLE users; --"}`,
							},
						},
					},
				},
			},
		},
	}

	result := g.Check(context.Background(), input)
	if result.Pass {
		t.Error("expected fail for injection in tool args, got pass")
	}
}

func TestMCPSecGuardrail_CleanArgs(t *testing.T) {
	g := New("test-mcp", map[string]interface{}{
		"provider":        "mcp_security",
		"validate_inputs": true,
	})

	raw, _ := json.Marshal("hello")
	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Messages: []models.Message{
				{
					Role:    "assistant",
					Content: raw,
					ToolCalls: []models.ToolCall{
						{
							ID:   "tc1",
							Type: "function",
							Function: models.FunctionCall{
								Name:      "search",
								Arguments: `{"query": "what is the weather?"}`,
							},
						},
					},
				},
			},
		},
	}

	result := g.Check(context.Background(), input)
	if !result.Pass {
		t.Errorf("expected pass for clean args, got fail: %s", result.Message)
	}
}

func TestMCPSecGuardrail_MaxCalls(t *testing.T) {
	g := New("test-mcp", map[string]interface{}{
		"provider":              "mcp_security",
		"max_calls_per_request": float64(2),
		"validate_inputs":       false,
	})

	raw, _ := json.Marshal("hello")
	calls := make([]models.ToolCall, 3)
	for i := range calls {
		calls[i] = models.ToolCall{
			ID:       "tc",
			Type:     "function",
			Function: models.FunctionCall{Name: "tool", Arguments: "{}"},
		}
	}

	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Messages: []models.Message{
				{Role: "assistant", Content: raw, ToolCalls: calls},
			},
		},
	}

	result := g.Check(context.Background(), input)
	if result.Pass {
		t.Error("expected fail for exceeding max calls, got pass")
	}
}

func TestMCPSecGuardrail_NilInput(t *testing.T) {
	g := New("test-mcp", map[string]interface{}{
		"provider": "mcp_security",
	})

	result := g.Check(context.Background(), nil)
	if !result.Pass {
		t.Error("nil input should pass")
	}
}

func TestMCPSecGuardrail_ResponseToolCalls(t *testing.T) {
	g := New("test-mcp", map[string]interface{}{
		"blocked_tools":    "dangerous_func",
		"provider":         "mcp_security",
		"validate_outputs": true,
	})

	raw, _ := json.Marshal("ok")
	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{Model: "test"},
		Response: &models.ChatCompletionResponse{
			Choices: []models.Choice{
				{
					Message: models.Message{
						Role:    "assistant",
						Content: raw,
						ToolCalls: []models.ToolCall{
							{ID: "tc1", Type: "function", Function: models.FunctionCall{Name: "dangerous_func", Arguments: "{}"}},
						},
					},
				},
			},
		},
	}

	result := g.Check(context.Background(), input)
	if result.Pass {
		t.Error("expected fail for blocked tool in response, got pass")
	}
}

func TestIsMCPSecConfig(t *testing.T) {
	if !IsMCPSecConfig(map[string]interface{}{"provider": "mcp_security"}) {
		t.Error("should detect mcp_security provider")
	}
	if IsMCPSecConfig(map[string]interface{}{"provider": "lakera"}) {
		t.Error("should not detect lakera as mcp_security")
	}
	if IsMCPSecConfig(nil) {
		t.Error("nil should return false")
	}
}

func TestContainsInjection(t *testing.T) {
	tests := []struct {
		input    string
		expected bool
	}{
		{`{"query": "SELECT * FROM users"}`, false},
		{`{"sql": "DROP TABLE users"}`, true},
		{`{"cmd": "exec('rm -rf /')"}`, true},
		{`{"text": "hello world"}`, false},
		{`{"script": "<script>alert(1)</script>"}`, true},
		{`{"path": "| sh"}`, true},
		{`{"name": "normal text"}`, false},
	}

	for _, tt := range tests {
		got := containsInjection(tt.input)
		if got != tt.expected {
			t.Errorf("containsInjection(%q) = %v, want %v", tt.input, got, tt.expected)
		}
	}
}

// --- 14.7.1: Custom Injection Patterns ---

func TestCustomInjectionPatterns_Block(t *testing.T) {
	g := New("test-mcp", map[string]interface{}{
		"provider":        "mcp_security",
		"validate_inputs": true,
		"custom_patterns": []interface{}{
			`(?i)\bPASSWORD\b`,
			`(?i)\bSECRET_KEY\b`,
		},
	})

	raw, _ := json.Marshal("hello")
	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Messages: []models.Message{
				{
					Role:    "assistant",
					Content: raw,
					ToolCalls: []models.ToolCall{
						{
							ID:   "tc1",
							Type: "function",
							Function: models.FunctionCall{
								Name:      "get_data",
								Arguments: `{"query": "show me the PASSWORD for admin"}`,
							},
						},
					},
				},
			},
		},
	}

	result := g.Check(context.Background(), input)
	if result.Pass {
		t.Error("expected fail for custom pattern 'PASSWORD', got pass")
	}
}

func TestCustomInjectionPatterns_Pass(t *testing.T) {
	g := New("test-mcp", map[string]interface{}{
		"provider":        "mcp_security",
		"validate_inputs": true,
		"custom_patterns": []interface{}{
			`(?i)\bPASSWORD\b`,
		},
	})

	raw, _ := json.Marshal("hello")
	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Messages: []models.Message{
				{
					Role:    "assistant",
					Content: raw,
					ToolCalls: []models.ToolCall{
						{
							ID:   "tc1",
							Type: "function",
							Function: models.FunctionCall{
								Name:      "search",
								Arguments: `{"query": "what is the weather today?"}`,
							},
						},
					},
				},
			},
		},
	}

	result := g.Check(context.Background(), input)
	if !result.Pass {
		t.Errorf("expected pass for clean args with custom patterns, got fail: %s", result.Message)
	}
}

func TestCustomInjectionPatterns_BuiltinStillWork(t *testing.T) {
	// Even with custom patterns, built-in patterns should still block.
	g := New("test-mcp", map[string]interface{}{
		"provider":        "mcp_security",
		"validate_inputs": true,
		"custom_patterns": []interface{}{
			`(?i)\bFORBIDDEN\b`,
		},
	})

	raw, _ := json.Marshal("hello")
	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Messages: []models.Message{
				{
					Role:    "assistant",
					Content: raw,
					ToolCalls: []models.ToolCall{
						{
							ID:   "tc1",
							Type: "function",
							Function: models.FunctionCall{
								Name:      "query_db",
								Arguments: `{"sql": "DROP TABLE users; --"}`,
							},
						},
					},
				},
			},
		},
	}

	result := g.Check(context.Background(), input)
	if result.Pass {
		t.Error("expected fail for built-in injection pattern, got pass")
	}
}

func TestCustomInjectionPatterns_InvalidRegexSkipped(t *testing.T) {
	// Invalid regex should be skipped gracefully, not crash.
	g := New("test-mcp", map[string]interface{}{
		"provider":        "mcp_security",
		"validate_inputs": true,
		"custom_patterns": []interface{}{
			`[invalid(regex`, // Invalid regex
			`(?i)\bVALID\b`,  // Valid
		},
	})

	// The invalid pattern should be skipped, valid pattern should work.
	if len(g.customPatterns) != 1 {
		t.Fatalf("expected 1 valid custom pattern, got %d", len(g.customPatterns))
	}
}

// --- 14.7.4: Per-Tool Rate Limiting ---

func TestPerToolRateLimit_Enforcement(t *testing.T) {
	g := New("test-mcp", map[string]interface{}{
		"provider":        "mcp_security",
		"validate_inputs": false,
		"tool_rate_limits": map[string]interface{}{
			"expensive_tool": float64(2), // Max 2 calls per minute
		},
	})

	raw, _ := json.Marshal("hello")

	// First 2 calls should pass.
	for i := 0; i < 2; i++ {
		input := &guardrails.CheckInput{
			Request: &models.ChatCompletionRequest{
				Messages: []models.Message{
					{
						Role:    "assistant",
						Content: raw,
						ToolCalls: []models.ToolCall{
							{
								ID:   "tc1",
								Type: "function",
								Function: models.FunctionCall{
									Name:      "expensive_tool",
									Arguments: `{}`,
								},
							},
						},
					},
				},
			},
		}
		result := g.Check(context.Background(), input)
		if !result.Pass {
			t.Errorf("call %d should have passed, got fail: %s", i+1, result.Message)
		}
	}

	// 3rd call should be blocked.
	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Messages: []models.Message{
				{
					Role:    "assistant",
					Content: raw,
					ToolCalls: []models.ToolCall{
						{
							ID:   "tc1",
							Type: "function",
							Function: models.FunctionCall{
								Name:      "expensive_tool",
								Arguments: `{}`,
							},
						},
					},
				},
			},
		},
	}
	result := g.Check(context.Background(), input)
	if result.Pass {
		t.Error("3rd call should have been rate limited, got pass")
	}
}

func TestPerToolRateLimit_DifferentToolsIndependent(t *testing.T) {
	g := New("test-mcp", map[string]interface{}{
		"provider":        "mcp_security",
		"validate_inputs": false,
		"tool_rate_limits": map[string]interface{}{
			"tool_a": float64(1),
			"tool_b": float64(1),
		},
	})

	raw, _ := json.Marshal("hello")

	// Call tool_a once (should pass).
	inputA := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Messages: []models.Message{
				{Role: "assistant", Content: raw, ToolCalls: []models.ToolCall{
					{ID: "tc1", Type: "function", Function: models.FunctionCall{Name: "tool_a", Arguments: `{}`}},
				}},
			},
		},
	}
	if result := g.Check(context.Background(), inputA); !result.Pass {
		t.Errorf("tool_a first call should pass: %s", result.Message)
	}

	// Call tool_b once (should pass — independent of tool_a).
	inputB := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Messages: []models.Message{
				{Role: "assistant", Content: raw, ToolCalls: []models.ToolCall{
					{ID: "tc1", Type: "function", Function: models.FunctionCall{Name: "tool_b", Arguments: `{}`}},
				}},
			},
		},
	}
	if result := g.Check(context.Background(), inputB); !result.Pass {
		t.Errorf("tool_b first call should pass: %s", result.Message)
	}
}

func TestPerToolRateLimit_UnlimitedToolPasses(t *testing.T) {
	g := New("test-mcp", map[string]interface{}{
		"provider":        "mcp_security",
		"validate_inputs": false,
		"tool_rate_limits": map[string]interface{}{
			"limited_tool": float64(1),
		},
	})

	raw, _ := json.Marshal("hello")

	// Call a tool that has no rate limit — should always pass.
	for i := 0; i < 5; i++ {
		input := &guardrails.CheckInput{
			Request: &models.ChatCompletionRequest{
				Messages: []models.Message{
					{Role: "assistant", Content: raw, ToolCalls: []models.ToolCall{
						{ID: "tc1", Type: "function", Function: models.FunctionCall{Name: "unlimited_tool", Arguments: `{}`}},
					}},
				},
			},
		}
		if result := g.Check(context.Background(), input); !result.Pass {
			t.Errorf("unlimited_tool call %d should pass: %s", i+1, result.Message)
		}
	}
}

func TestCheckToolRateLimit_Internal(t *testing.T) {
	g := New("test-mcp", map[string]interface{}{
		"provider": "mcp_security",
		"tool_rate_limits": map[string]interface{}{
			"t1": float64(3),
		},
	})

	// Should allow 3, block 4th.
	for i := 0; i < 3; i++ {
		if !g.checkToolRateLimit("t1") {
			t.Errorf("call %d should be allowed", i+1)
		}
	}
	if g.checkToolRateLimit("t1") {
		t.Error("4th call should be rate limited")
	}

	// No limit set → always allowed.
	for i := 0; i < 10; i++ {
		if !g.checkToolRateLimit("no_limit_tool") {
			t.Error("tool without limit should always be allowed")
		}
	}
}
