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

// toolRef is one tool to decide about, and whether alias equivalence applies
// to it. An application function is matched by its exact name; two spellings
// of a function name are two different functions.
type toolRef struct {
	name    string
	builtin bool
}

// matches reports whether a configured pattern covers this tool.
//
// For a provider-executed tool, every spelling of the same capability is
// tried: the patterns are globs, so canonicalizing the name alone would stop a
// pattern written the other way from matching — "*Execution" would miss
// code_execution. For a function the exact name is used, so an application
// function called "codeExecution" is not covered by a rule about the built-in
// code_execution, and the reverse.
func (g *ToolPermGuardrail) matches(tool toolRef) bool {
	if !tool.builtin {
		return g.matchesAny(tool.name)
	}
	for _, spelling := range models.CapabilitySpellings(tool.name) {
		if g.matchesAny(spelling) {
			return true
		}
	}
	return false
}

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

func (g *ToolPermGuardrail) Name() string            { return g.name }
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

	var tools []toolRef

	// Collect tools from the request (pre-stage).
	if (g.applyTo == "request" || g.applyTo == "both") && input.Request != nil {
		for _, tool := range input.Request.Tools {
			// The capability the provider will forward, not the function name
			// beside it. An entry naming one capability in its type and another
			// in its function passed a denylist for the first and was sent
			// anyway, and a bare {"google_search":{}} names no function at all.
			tools = append(tools, toolRef{
				name:    tool.Capability(),
				builtin: tool.NamesBuiltinCapability(),
			})
		}
	}

	// Collect tools from the response (post-stage). These are function calls
	// the model made, so they are matched by their exact names.
	if (g.applyTo == "response" || g.applyTo == "both") && input.Response != nil {
		for _, name := range extractResponseToolNames(input.Response) {
			tools = append(tools, toolRef{name: name})
		}
	}

	if len(tools) == 0 {
		return &guardrails.CheckResult{Pass: true}
	}

	toolNames := make([]string, 0, len(tools))
	for _, tool := range tools {
		toolNames = append(toolNames, tool.name)
	}

	var violations []string

	switch g.mode {
	case "allowlist":
		for _, tool := range tools {
			if !g.matches(tool) {
				violations = append(violations, tool.name)
			}
		}
	case "denylist":
		for _, tool := range tools {
			if g.matches(tool) {
				violations = append(violations, tool.name)
			}
		}
	case "audit":
		// In audit mode, flag matches but always pass.
		var matched []string
		for _, tool := range tools {
			if g.matches(tool) {
				matched = append(matched, tool.name)
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
