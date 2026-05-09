// Package toolperm implements a guardrail that enforces tool/function call permissions.
// It validates tools in requests (pre-stage) and tool_calls in responses (post-stage)
// against configurable allow/deny lists with glob pattern matching.
package toolperm

import (
	"context"
	"fmt"
	"path/filepath"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ToolPermGuardrail enforces tool permission policies.
type ToolPermGuardrail struct {
	name     string
	mode     string // "allowlist", "denylist", "audit"
	patterns []string
	applyTo  string // "request", "response", "both"
}

// New creates a ToolPermGuardrail from config.
func New(name string, cfg map[string]interface{}) *ToolPermGuardrail {
	g := &ToolPermGuardrail{
		name:    name,
		mode:    "denylist",
		applyTo: "both",
	}

	if v, ok := cfg["mode"].(string); ok && v != "" {
		g.mode = v
	}
	if v, ok := cfg["apply_to"].(string); ok && v != "" {
		g.applyTo = v
	}

	// Parse tool patterns from comma-separated string or string slice.
	if v, ok := cfg["tools"].(string); ok && v != "" {
		for _, p := range strings.Split(v, ",") {
			if t := strings.TrimSpace(p); t != "" {
				g.patterns = append(g.patterns, t)
			}
		}
	} else if arr, ok := cfg["tools"].([]interface{}); ok {
		for _, item := range arr {
			if s, ok := item.(string); ok && s != "" {
				g.patterns = append(g.patterns, s)
			}
		}
	}

	return g
}

func (g *ToolPermGuardrail) Name() string           { return g.name }
func (g *ToolPermGuardrail) Stage() guardrails.Stage { return guardrails.StagePre }

// IsToolPermConfig returns true if the config specifies a tool permission guardrail.
func IsToolPermConfig(cfg map[string]interface{}) bool {
	if cfg == nil {
		return false
	}
	provider, _ := cfg["provider"].(string)
	return provider == "tool_permission"
}

// Check evaluates tool names against the permission policy.
func (g *ToolPermGuardrail) Check(ctx context.Context, input *guardrails.CheckInput) *guardrails.CheckResult {
	if input == nil {
		return &guardrails.CheckResult{Pass: true}
	}

	var toolNames []string

	// Collect tool names from request (pre-stage).
	if (g.applyTo == "request" || g.applyTo == "both") && input.Request != nil {
		for _, tool := range input.Request.Tools {
			toolNames = append(toolNames, tool.Function.Name)
		}
	}

	// Collect tool names from response (post-stage).
	if (g.applyTo == "response" || g.applyTo == "both") && input.Response != nil {
		toolNames = append(toolNames, extractResponseToolNames(input.Response)...)
	}

	if len(toolNames) == 0 {
		return &guardrails.CheckResult{Pass: true}
	}

	var violations []string

	switch g.mode {
	case "allowlist":
		for _, name := range toolNames {
			if !g.matchesAny(name) {
				violations = append(violations, name)
			}
		}
	case "denylist":
		for _, name := range toolNames {
			if g.matchesAny(name) {
				violations = append(violations, name)
			}
		}
	case "audit":
		// In audit mode, flag matches but always pass.
		var matched []string
		for _, name := range toolNames {
			if g.matchesAny(name) {
				matched = append(matched, name)
			}
		}
		return &guardrails.CheckResult{
			Pass:    true,
			Score:   0.0,
			Message: fmt.Sprintf("audit: %d tools matched patterns", len(matched)),
			Details: map[string]interface{}{
				"matched_tools": matched,
				"all_tools":     toolNames,
				"mode":          "audit",
			},
		}
	}

	if len(violations) == 0 {
		return &guardrails.CheckResult{Pass: true, Score: 0.0, Message: "all tools permitted"}
	}

	return &guardrails.CheckResult{
		Pass:    false,
		Score:   1.0,
		Message: fmt.Sprintf("tool permission %s violation: %s", g.mode, strings.Join(violations, ", ")),
		Details: map[string]interface{}{
			"violations": violations,
			"mode":       g.mode,
		},
	}
}

// matchesAny checks if a tool name matches any of the configured patterns.
func (g *ToolPermGuardrail) matchesAny(name string) bool {
	for _, pattern := range g.patterns {
		if matched, _ := filepath.Match(pattern, name); matched {
			return true
		}
		// Also check exact match for simple strings.
		if pattern == name {
			return true
		}
	}
	return false
}

// extractResponseToolNames gets tool call names from response choices.
func extractResponseToolNames(resp *models.ChatCompletionResponse) []string {
	if resp == nil {
		return nil
	}
	var names []string
	for _, choice := range resp.Choices {
		for _, tc := range choice.Message.ToolCalls {
			if tc.Function.Name != "" {
				names = append(names, tc.Function.Name)
			}
		}
	}
	return names
}
